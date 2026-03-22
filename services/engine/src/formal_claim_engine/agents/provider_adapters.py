"""Provider-specific request shaping for agent and ingest LLM calls."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ModelSlot

PROVIDER_ADAPTER_ID = "formal_claim_engine.provider_adapter_registry"
PROVIDER_ADAPTER_VERSION = "1.0.0"


@dataclass(frozen=True)
class CompletionRequest:
    system: str
    messages: list[dict[str, str]]
    response_format: dict | None = None


class DisabledProviderError(RuntimeError):
    """Raised when a workflow tries to call a disabled provider slot."""


class ProviderAdapter:
    def prepare(
        self,
        *,
        slot: ModelSlot,
        system: str,
        messages: list[dict[str, str]],
        expect_json: bool,
    ) -> CompletionRequest:
        raise NotImplementedError


class AnthropicAdapter(ProviderAdapter):
    def prepare(
        self,
        *,
        slot: ModelSlot,
        system: str,
        messages: list[dict[str, str]],
        expect_json: bool,
    ) -> CompletionRequest:
        return CompletionRequest(system=system, messages=messages)


class OpenAICompatibleAdapter(ProviderAdapter):
    def prepare(
        self,
        *,
        slot: ModelSlot,
        system: str,
        messages: list[dict[str, str]],
        expect_json: bool,
    ) -> CompletionRequest:
        return CompletionRequest(
            system=system,
            messages=messages,
            response_format={"type": "json_object"} if expect_json else None,
        )


class DisabledAdapter(ProviderAdapter):
    def prepare(
        self,
        *,
        slot: ModelSlot,
        system: str,
        messages: list[dict[str, str]],
        expect_json: bool,
    ) -> CompletionRequest:
        raise DisabledProviderError(
            f"Provider slot '{slot.model}' is disabled for this workflow."
        )


PROVIDER_ADAPTERS: dict[str, ProviderAdapter] = {
    "anthropic": AnthropicAdapter(),
    "openai": OpenAICompatibleAdapter(),
    "local": OpenAICompatibleAdapter(),
    "codex_session": OpenAICompatibleAdapter(),
    "claude_session": OpenAICompatibleAdapter(),
    "disabled": DisabledAdapter(),
}


def prepare_completion_request(
    *,
    slot: ModelSlot,
    system: str,
    messages: list[dict[str, str]],
    expect_json: bool,
) -> CompletionRequest:
    try:
        adapter = PROVIDER_ADAPTERS[slot.provider]
    except KeyError as exc:
        raise ValueError(f"Unknown provider adapter: {slot.provider}") from exc
    return adapter.prepare(
        slot=slot,
        system=system,
        messages=messages,
        expect_json=expect_json,
    )


def provider_adapter_metadata(slot: ModelSlot) -> dict[str, str]:
    return {
        "provider_adapter_id": PROVIDER_ADAPTER_ID,
        "provider_adapter_version": PROVIDER_ADAPTER_VERSION,
        "provider": slot.provider,
        "model": slot.model,
    }


__all__ = [
    "CompletionRequest",
    "DisabledProviderError",
    "PROVIDER_ADAPTERS",
    "PROVIDER_ADAPTER_ID",
    "PROVIDER_ADAPTER_VERSION",
    "provider_adapter_metadata",
    "prepare_completion_request",
]
