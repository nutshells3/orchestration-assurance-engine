"""Smoke tests for Codex-session provider routing and subprocess wiring."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from session provider test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.config import PipelineConfig, ModelSlot  # noqa: E402
from formal_claim_engine.llm_client import LLMClient  # noqa: E402


class Completed:
    def __init__(self, *, stdout: str = "", stderr: str = ""):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = stderr


def main() -> None:
    previous = {
        "FORMAL_CLAIM_USE_SESSION_PROVIDERS": os.environ.get("FORMAL_CLAIM_USE_SESSION_PROVIDERS"),
        "FORMAL_CLAIM_USE_CODEX_SESSION": os.environ.get("FORMAL_CLAIM_USE_CODEX_SESSION"),
        "FORMAL_CLAIM_CODEX_LARGE_MODEL": os.environ.get("FORMAL_CLAIM_CODEX_LARGE_MODEL"),
        "FORMAL_CLAIM_CODEX_SMALL_MODEL": os.environ.get("FORMAL_CLAIM_CODEX_SMALL_MODEL"),
        "FORMAL_CLAIM_CLAUDE_LARGE_MODEL": os.environ.get("FORMAL_CLAIM_CLAUDE_LARGE_MODEL"),
        "FORMAL_CLAIM_CLAUDE_SMALL_MODEL": os.environ.get("FORMAL_CLAIM_CLAUDE_SMALL_MODEL"),
        "FORMAL_CLAIM_FORMALIZER_A_MODEL": os.environ.get("FORMAL_CLAIM_FORMALIZER_A_MODEL"),
        "FORMAL_CLAIM_FORMALIZER_A_EFFORT": os.environ.get("FORMAL_CLAIM_FORMALIZER_A_EFFORT"),
        "FORMAL_CLAIM_FORMALIZER_B_MODEL": os.environ.get("FORMAL_CLAIM_FORMALIZER_B_MODEL"),
    }
    os.environ["FORMAL_CLAIM_USE_SESSION_PROVIDERS"] = "1"
    os.environ["FORMAL_CLAIM_CODEX_LARGE_MODEL"] = "gpt-5.4"
    os.environ["FORMAL_CLAIM_CODEX_SMALL_MODEL"] = "gpt-5.4-mini"
    os.environ["FORMAL_CLAIM_CLAUDE_LARGE_MODEL"] = "claude-sonnet-4-20250514"
    os.environ["FORMAL_CLAIM_CLAUDE_SMALL_MODEL"] = "claude-sonnet-4-20250514"
    os.environ["FORMAL_CLAIM_FORMALIZER_A_MODEL"] = "gpt-5.4-nano"
    os.environ["FORMAL_CLAIM_FORMALIZER_A_EFFORT"] = "xhigh"
    os.environ["FORMAL_CLAIM_FORMALIZER_B_MODEL"] = "haiku"
    try:
        config = PipelineConfig()
        assert config.model_routing["planner"].provider == "claude_session", config.model_routing["planner"]
        assert config.model_routing["planner"].model == "claude-sonnet-4-20250514", config.model_routing["planner"]
        assert config.model_routing["formalizer_a"].provider == "codex_session", config.model_routing["formalizer_a"]
        assert config.model_routing["formalizer_a"].model == "gpt-5.4-nano", config.model_routing["formalizer_a"]
        assert config.model_routing["formalizer_a"].reasoning_effort == "xhigh", config.model_routing["formalizer_a"]
        assert config.model_routing["formalizer_b"].provider == "claude_session", config.model_routing["formalizer_b"]
        assert config.model_routing["formalizer_b"].model == "haiku", config.model_routing["formalizer_b"]
        assert config.model_routing["research_agent"].provider == "codex_session", config.model_routing["research_agent"]
        assert config.model_routing["research_agent"].model == "gpt-5.4", config.model_routing["research_agent"]
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    captured: dict[str, object] = {}

    def fake_run(cmd, capture_output, text, check, timeout, env, **kwargs):
        captured["cmd"] = list(cmd)
        captured["timeout"] = timeout
        captured["env"] = dict(env)
        output_index = cmd.index("-o") + 1
        Path(cmd[output_index]).write_text('{"ok": true}', encoding="utf-8")
        if "--output-schema" in cmd:
            schema_index = cmd.index("--output-schema") + 1
            captured["schema"] = Path(cmd[schema_index]).read_text(encoding="utf-8")
        return Completed()

    client = LLMClient()
    slot = ModelSlot(
        provider="codex_session",
        model="gpt-5.4-nano",
        max_tokens=1024,
        reasoning_effort="xhigh",
    )
    with patch("formal_claim_engine.llm_client.subprocess.run", side_effect=fake_run):
        response = asyncio.run(
            client.complete(
                slot=slot,
                system="Return a JSON object.",
                messages=[{"role": "user", "content": "hi"}],
                response_format={"type": "json_object"},
            )
        )

    assert response.text == '{"ok": true}', response
    assert response.usage is None, response
    cmd = captured["cmd"]
    assert isinstance(cmd, list) and cmd[:2] == ["codex", "exec"], cmd
    assert "--output-schema" in cmd, cmd
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "gpt-5.4-nano", cmd
    assert "-c" in cmd and 'model_reasoning_effort="xhigh"' in cmd, cmd
    assert '"type": "object"' in str(captured.get("schema") or ""), captured
    env = captured["env"]
    assert isinstance(env, dict) and "OPENAI_API_KEY" not in env, env
    assert env.get("PYTHONIOENCODING") == "utf-8", env
    assert env.get("PYTHONUTF8") == "1", env

    claude_captured: dict[str, object] = {}

    def fake_claude_run(cmd, capture_output, text, check, timeout, env, **kwargs):
        claude_captured["cmd"] = list(cmd)
        claude_captured["env"] = dict(env)
        claude_captured["input"] = kwargs.get("input")
        return Completed(stdout='{"ok": true}')

    os.environ["ANTHROPIC_API_KEY"] = "should-not-be-used"
    os.environ["CLAUDE_API_KEY"] = "should-not-be-used"
    try:
        with patch("formal_claim_engine.llm_client.subprocess.run", side_effect=fake_claude_run):
            response = asyncio.run(
                client.complete(
                    slot=ModelSlot(provider="claude_session", model="haiku", max_tokens=1024),
                    system="Return a JSON object.",
                    messages=[{"role": "user", "content": "hi"}],
                    response_format={"type": "json_object"},
                )
            )
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("CLAUDE_API_KEY", None)

    assert response.text == '{"ok": true}', response
    claude_cmd = claude_captured["cmd"]
    assert isinstance(claude_cmd, list) and claude_cmd[:2] == ["claude", "-p"], claude_cmd
    assert "--model" in claude_cmd and claude_cmd[claude_cmd.index("--model") + 1] == "haiku", claude_cmd
    assert "--input-format" in claude_cmd and "text" in claude_cmd, claude_cmd
    assert "--permission-mode" in claude_cmd and "bypassPermissions" in claude_cmd, claude_cmd
    assert "<SYSTEM>" in str(claude_captured.get("input") or ""), claude_captured
    claude_env = claude_captured["env"]
    assert isinstance(claude_env, dict) and "ANTHROPIC_API_KEY" not in claude_env and "CLAUDE_API_KEY" not in claude_env, claude_env
    assert claude_env.get("PYTHONIOENCODING") == "utf-8", claude_env
    assert claude_env.get("PYTHONUTF8") == "1", claude_env


if __name__ == "__main__":
    main()
