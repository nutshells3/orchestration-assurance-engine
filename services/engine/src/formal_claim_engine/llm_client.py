"""
Unified LLM client.

Dispatches to Anthropic or OpenAI based on the ModelSlot.provider field.
Adding a new provider = adding one branch in `complete()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ModelSlot
from .unified_config import RetryPolicy

log = logging.getLogger(__name__)

# Default retry policies used when no unified config is injected.
_DEFAULT_LLM_CALL_RETRY = RetryPolicy(max_attempts=3, backoff="exponential", base_ms=1000, cap_ms=30000, jitter=True)
_DEFAULT_EMPTY_OUTPUT_RETRY = RetryPolicy(max_attempts=3, backoff="none")


def _session_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("NO_COLOR", "1")
    return env


def _claude_cli_effort(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return {
        "xhigh": "max",
        "high": "high",
        "medium": "medium",
        "low": "low",
        "max": "max",
    }.get(normalized, normalized)


@dataclass
class LLMResponse:
    text: str
    raw: Any  # provider-specific response object
    usage: dict[str, int] | None = None


class LLMClient:
    """Thin wrapper over multiple LLM providers."""

    def __init__(
        self,
        *,
        llm_call_retry: RetryPolicy | None = None,
        empty_output_retry: RetryPolicy | None = None,
    ):
        self._anthropic = None
        self._openai = None
        self._llm_call_retry = llm_call_retry or _DEFAULT_LLM_CALL_RETRY
        self._empty_output_retry = empty_output_retry or _DEFAULT_EMPTY_OUTPUT_RETRY

    # --- lazy provider init ---

    def _resolve_anthropic_api_key(self, slot: ModelSlot) -> str | None:
        if slot.api_key_env:
            explicit = os.environ.get(slot.api_key_env)
            if explicit:
                return explicit
        return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")

    def _get_anthropic(self):
        if self._anthropic is None:
            import anthropic
            api_key = self._resolve_anthropic_api_key(
                ModelSlot(provider="anthropic", model="default")
            )
            if api_key:
                self._anthropic = anthropic.Anthropic(api_key=api_key)
            else:
                self._anthropic = anthropic.Anthropic()
        return self._anthropic

    def _get_openai(self):
        if self._openai is None:
            import openai
            self._openai = openai.OpenAI()
        return self._openai

    # --- main call ---

    async def complete(
        self,
        slot: ModelSlot,
        system: str,
        messages: list[dict[str, str]],
        response_format: dict | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        `messages` is a list of {"role": "user"|"assistant", "content": "..."}.
        `response_format` is optional; when set to {"type": "json_object"}
        providers that support it will enforce JSON output.
        """
        if slot.provider == "anthropic":
            return await self._call_anthropic(slot, system, messages)
        elif slot.provider == "openai":
            return await self._call_openai(slot, system, messages, response_format)
        elif slot.provider == "local":
            return await self._call_openai_compat(slot, system, messages, response_format)
        elif slot.provider == "codex_session":
            return await self._call_codex_session(slot, system, messages, response_format)
        elif slot.provider == "claude_session":
            return await self._call_claude_session(slot, system, messages, response_format)
        else:
            raise ValueError(f"Unknown provider: {slot.provider}")

    # --- Anthropic ---

    async def _call_anthropic(
        self, slot: ModelSlot, system: str, messages: list[dict]
    ) -> LLMResponse:
        import anthropic

        api_key = self._resolve_anthropic_api_key(slot)
        client = anthropic.Anthropic(api_key=api_key) if api_key else self._get_anthropic()

        resp = client.messages.create(
            model=slot.model,
            max_tokens=slot.max_tokens,
            temperature=slot.temperature,
            system=system,
            messages=messages,
        )
        text = resp.content[0].text if resp.content else ""
        usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        return LLMResponse(text=text, raw=resp, usage=usage)

    # --- OpenAI ---

    async def _call_openai(
        self,
        slot: ModelSlot,
        system: str,
        messages: list[dict],
        response_format: dict | None = None,
    ) -> LLMResponse:
        import openai

        if slot.api_key_env:
            client = openai.OpenAI(api_key=os.environ[slot.api_key_env])
        else:
            client = self._get_openai()

        oai_messages = [{"role": "system", "content": system}] + messages
        kwargs: dict[str, Any] = dict(
            model=slot.model,
            max_tokens=slot.max_tokens,
            temperature=slot.temperature,
            messages=oai_messages,
        )
        if response_format:
            kwargs["response_format"] = response_format

        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        usage = {
            "input_tokens": resp.usage.prompt_tokens,
            "output_tokens": resp.usage.completion_tokens,
        } if resp.usage else None
        return LLMResponse(text=text, raw=resp, usage=usage)

    # --- OpenAI-compatible local ---

    async def _call_openai_compat(
        self,
        slot: ModelSlot,
        system: str,
        messages: list[dict],
        response_format: dict | None = None,
    ) -> LLMResponse:
        import openai

        client = openai.OpenAI(
            base_url=slot.api_base or "http://localhost:8000/v1",
            api_key=os.environ.get(slot.api_key_env or "", "dummy"),
        )
        oai_messages = [{"role": "system", "content": system}] + messages
        kwargs: dict[str, Any] = dict(
            model=slot.model,
            max_tokens=slot.max_tokens,
            temperature=slot.temperature,
            messages=oai_messages,
        )
        if response_format:
            kwargs["response_format"] = response_format

        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        return LLMResponse(text=text, raw=resp)

    # --- Codex session-backed local shell provider ---

    async def _call_codex_session(
        self,
        slot: ModelSlot,
        system: str,
        messages: list[dict],
        response_format: dict | None = None,
    ) -> LLMResponse:
        return await asyncio.to_thread(
            self._call_codex_session_sync,
            slot,
            system,
            messages,
            response_format,
        )

    def _call_codex_session_sync(
        self,
        slot: ModelSlot,
        system: str,
        messages: list[dict],
        response_format: dict | None = None,
    ) -> LLMResponse:
        prompt_lines = [
            "You are serving as a stateless completion backend for a structured engine.",
            "Follow the provided system instruction exactly.",
            "Return only the final answer body with no extra commentary.",
        ]
        if response_format == {"type": "json_object"}:
            prompt_lines.append("Return exactly one valid JSON object and nothing else.")
        prompt_lines.extend(
            [
                "",
                "<SYSTEM>",
                system,
                "</SYSTEM>",
                "",
                "<MESSAGES>",
                json.dumps(messages, ensure_ascii=False),
                "</MESSAGES>",
            ]
        )
        prompt = "\n".join(prompt_lines)

        retry = self._empty_output_retry
        max_attempts = max(1, retry.max_attempts)
        last_completed: subprocess.CompletedProcess[str] | None = None
        for attempt in range(1, max_attempts + 1):
            with tempfile.TemporaryDirectory(prefix="formal-claim-codex-") as tmp_dir:
                tmp_root = Path(tmp_dir)
                output_path = tmp_root / "codex-output.txt"
                schema_path = tmp_root / "response-schema.json"
                cmd = [
                    "codex",
                    "exec",
                    "--skip-git-repo-check",
                    "--color",
                    "never",
                    "--sandbox",
                    "read-only",
                    "-a",
                    "never",
                    "-C",
                    os.getcwd(),
                    "-o",
                    str(output_path),
                ]
                if slot.model:
                    cmd.extend(["-m", slot.model])
                if slot.reasoning_effort:
                    cmd.extend(["-c", f'model_reasoning_effort="{slot.reasoning_effort}"'])
                if response_format == {"type": "json_object"}:
                    schema_path.write_text(
                        json.dumps({"type": "object"}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    cmd.extend(["--output-schema", str(schema_path)])
                cmd.append(prompt)
                env = _session_subprocess_env()
                env.pop("OPENAI_API_KEY", None)
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=max(60, int(slot.max_tokens / 8)),
                    env=env,
                    encoding="utf-8",
                    errors="replace",
                )
                last_completed = completed
                if completed.returncode != 0:
                    raise RuntimeError(
                        "codex_session provider failed: "
                        f"exit={completed.returncode} stderr={completed.stderr.strip()}"
                    )
                text = output_path.read_text(encoding="utf-8").strip() if output_path.exists() else ""
                if text:
                    return LLMResponse(
                        text=text,
                        raw={
                            "command": cmd,
                            "stdout": completed.stdout,
                            "stderr": completed.stderr,
                            "returncode": completed.returncode,
                            "attempt": attempt,
                        },
                        usage=None,
                    )
        raise RuntimeError(
            f"codex_session provider returned empty output after {max_attempts} attempts: "
            f"stdout={(last_completed.stdout if last_completed else '').strip()} "
            f"stderr={(last_completed.stderr if last_completed else '').strip()}"
        )

    async def _call_claude_session(
        self,
        slot: ModelSlot,
        system: str,
        messages: list[dict],
        response_format: dict | None = None,
    ) -> LLMResponse:
        return await asyncio.to_thread(
            self._call_claude_session_sync,
            slot,
            system,
            messages,
            response_format,
        )

    def _call_claude_session_sync(
        self,
        slot: ModelSlot,
        system: str,
        messages: list[dict],
        response_format: dict | None = None,
    ) -> LLMResponse:
        prompt_lines = [
            "You are serving as a stateless completion backend for a structured engine.",
            "Follow the provided system instruction exactly.",
            "Return only the final answer body with no extra commentary.",
        ]
        if response_format == {"type": "json_object"}:
            prompt_lines.append("Return exactly one valid JSON object and nothing else.")
        prompt_lines.extend(
            [
                "",
                "<SYSTEM>",
                system,
                "</SYSTEM>",
                "",
                "<MESSAGES>",
                json.dumps(messages, ensure_ascii=False),
                "</MESSAGES>",
            ]
        )
        prompt = "\n".join(prompt_lines)

        cmd = [
            "claude",
            "-p",
            "--input-format",
            "text",
            "--permission-mode",
            "bypassPermissions",
            "--no-session-persistence",
        ]
        if slot.model:
            cmd.extend(["--model", slot.model])
        claude_effort = _claude_cli_effort(slot.reasoning_effort)
        if claude_effort:
            cmd.extend(["--effort", claude_effort])
        env = _session_subprocess_env()
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_API_KEY", None)
        retry = self._empty_output_retry
        max_attempts = max(1, retry.max_attempts)
        last_completed: subprocess.CompletedProcess[str] | None = None
        for attempt in range(1, max_attempts + 1):
            completed = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                check=False,
                timeout=max(60, int(slot.max_tokens / 8)),
                env=env,
                encoding="utf-8",
                errors="replace",
            )
            last_completed = completed
            if completed.returncode != 0:
                raise RuntimeError(
                    "claude_session provider failed: "
                    f"exit={completed.returncode} stderr={completed.stderr.strip()}"
                )
            text = completed.stdout.strip()
            if text:
                return LLMResponse(
                    text=text,
                    raw={
                        "command": cmd,
                        "stdout": completed.stdout,
                        "stderr": completed.stderr,
                        "returncode": completed.returncode,
                        "attempt": attempt,
                    },
                    usage=None,
                )
        raise RuntimeError(
            f"claude_session provider returned empty output after {max_attempts} attempts: "
            f"stdout={(last_completed.stdout if last_completed else '').strip()} "
            f"stderr={(last_completed.stderr if last_completed else '').strip()}"
        )


# Singleton (default retry policies; callers can construct with custom policies)
llm_client = LLMClient()


def llm_client_from_unified_config() -> LLMClient:
    """Build an ``LLMClient`` whose retry policies come from ``verification.toml``."""
    from .unified_config import load_config

    try:
        uc = load_config()
    except FileNotFoundError:
        return LLMClient()
    return LLMClient(
        llm_call_retry=uc.retry_policies.get("llm_call"),
        empty_output_retry=uc.retry_policies.get("llm_empty_output"),
    )
