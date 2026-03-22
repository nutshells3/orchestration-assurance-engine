"""Integration smoke for file-backed prompt registry and provider adapters."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from prompt/provider test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.agents.formalizer import FormalizerAgent  # noqa: E402
from formal_claim_engine.agents.planner import PlannerAgent  # noqa: E402
from formal_claim_engine.agents.prompt_registry import (  # noqa: E402
    build_prompt_lineage,
    list_prompt_roles,
    render_system_prompt,
)
from formal_claim_engine.agents.provider_adapters import (  # noqa: E402
    DisabledProviderError,
    prepare_completion_request,
)
from formal_claim_engine.config import ModelSlot, PipelineConfig  # noqa: E402
from formal_claim_engine.llm_client import LLMClient  # noqa: E402


class DummyLLM(LLMClient):
    async def complete(self, *args, **kwargs):  # pragma: no cover - defensive guard
        raise AssertionError("Prompt/provider smoke should not call a real LLM.")


def main() -> None:
    roles = set(list_prompt_roles())
    assert roles == {
        "auditor",
        "claim_graph_agent",
        "dev_agent",
        "formalizer",
        "proof_verifier",
        "planner",
        "policy_engine",
        "research_agent",
    }, roles

    planner_prompt = render_system_prompt("planner")
    assert "Planner in a Formal Claim Pipeline" in planner_prompt, planner_prompt
    claim_graph_prompt = render_system_prompt("claim_graph_agent")
    assert "The engine injects `graph_id`, `project_id`, `created_at`, and `updated_at`" in claim_graph_prompt, claim_graph_prompt
    assert "`claim_class` must be one of" in claim_graph_prompt, claim_graph_prompt
    formalizer_prompt = render_system_prompt("formalizer", label="A", other="B")
    assert "Formalizer A" in formalizer_prompt, formalizer_prompt
    assert "Formalizer B" in formalizer_prompt, formalizer_prompt
    assert "$label" not in formalizer_prompt, formalizer_prompt

    planner_agent = PlannerAgent(PipelineConfig(), DummyLLM())
    assert planner_agent.system_prompt({}) == planner_prompt

    formalizer_agent = FormalizerAgent(PipelineConfig(), DummyLLM(), label="A")
    assert "Formalizer A" in formalizer_agent.system_prompt({})
    lineage = build_prompt_lineage(
        "formalizer_a",
        ModelSlot(provider="openai", model="gpt"),
    )
    assert lineage["prompt_identifier"] == "formal_claim_engine.agent.formalizer.system"
    assert lineage["response_schema_id"] == "formal_claim_engine.agent.formalizer"
    assert lineage["provider_adapter_version"] == "1.0.0"
    assert len(lineage["prompt_sha256"]) == 64
    assert len(lineage["response_schema_sha256"]) == 64

    anthropic_request = prepare_completion_request(
        slot=ModelSlot(provider="anthropic", model="claude"),
        system="system",
        messages=[{"role": "user", "content": "hi"}],
        expect_json=True,
    )
    assert anthropic_request.response_format is None

    openai_request = prepare_completion_request(
        slot=ModelSlot(provider="openai", model="gpt"),
        system="system",
        messages=[{"role": "user", "content": "hi"}],
        expect_json=True,
    )
    assert openai_request.response_format == {"type": "json_object"}

    session_request = prepare_completion_request(
        slot=ModelSlot(provider="codex_session", model="gpt-5.4-mini"),
        system="system",
        messages=[{"role": "user", "content": "hi"}],
        expect_json=True,
    )
    assert session_request.response_format == {"type": "json_object"}

    claude_session_request = prepare_completion_request(
        slot=ModelSlot(provider="claude_session", model="claude-sonnet-4-20250514"),
        system="system",
        messages=[{"role": "user", "content": "hi"}],
        expect_json=True,
    )
    assert claude_session_request.response_format == {"type": "json_object"}

    try:
        prepare_completion_request(
            slot=ModelSlot(provider="disabled", model="off"),
            system="system",
            messages=[{"role": "user", "content": "hi"}],
            expect_json=True,
        )
    except DisabledProviderError:
        return
    raise AssertionError("disabled provider should raise DisabledProviderError")


if __name__ == "__main__":
    main()
