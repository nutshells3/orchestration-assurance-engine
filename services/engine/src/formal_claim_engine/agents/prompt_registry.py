"""File-backed prompt registry with prompt and response-schema lineage."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

from ..config import ModelSlot
from .provider_adapters import provider_adapter_metadata
from .response_schema_registry import response_schema_metadata


@dataclass(frozen=True)
class PromptSpec:
    prompt_identifier: str
    prompt_version: str
    filename: str


@dataclass(frozen=True)
class PromptLineage:
    role: str
    prompt_identifier: str
    prompt_version: str
    prompt_sha256: str
    response_schema_id: str
    response_schema_version: str
    response_schema_sha256: str
    provider_adapter_id: str
    provider_adapter_version: str
    provider: str
    model: str


PROMPT_SPECS = {
    "planner": PromptSpec(
        prompt_identifier="formal_claim_engine.agent.planner.system",
        prompt_version="1.0.0",
        filename="planner.system.md",
    ),
    "claim_graph_agent": PromptSpec(
        prompt_identifier="formal_claim_engine.agent.claim_graph_agent.system",
        prompt_version="1.0.0",
        filename="claim_graph_agent.system.md",
    ),
    "formalizer": PromptSpec(
        prompt_identifier="formal_claim_engine.agent.formalizer.system",
        prompt_version="1.0.0",
        filename="formalizer.system.md",
    ),
    "proof_verifier": PromptSpec(
        prompt_identifier="formal_claim_engine.agent.proof_verifier.system",
        prompt_version="1.0.0",
        filename="proof_verifier.system.md",
    ),
    "auditor": PromptSpec(
        prompt_identifier="formal_claim_engine.agent.auditor.system",
        prompt_version="1.0.0",
        filename="auditor.system.md",
    ),
    "research_agent": PromptSpec(
        prompt_identifier="formal_claim_engine.agent.research_agent.system",
        prompt_version="1.0.0",
        filename="research_agent.system.md",
    ),
    "dev_agent": PromptSpec(
        prompt_identifier="formal_claim_engine.agent.dev_agent.system",
        prompt_version="1.0.0",
        filename="dev_agent.system.md",
    ),
    "policy_engine": PromptSpec(
        prompt_identifier="formal_claim_engine.agent.policy_engine.system",
        prompt_version="1.0.0",
        filename="policy_engine.system.md",
    ),
}


def canonical_prompt_role(role: str) -> str:
    if role.startswith("formalizer_"):
        return "formalizer"
    return role


def prompt_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt_template(role: str) -> str:
    normalized_role = canonical_prompt_role(role)
    try:
        spec = PROMPT_SPECS[normalized_role]
    except KeyError as exc:  # pragma: no cover - defensive registry guard
        raise KeyError(f"Unknown agent role for prompt registry: {role}") from exc
    return (prompt_dir() / spec.filename).read_text(encoding="utf-8")


def prompt_template_hash(role: str) -> str:
    template = load_prompt_template(role)
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def render_system_prompt(role: str, **variables: object) -> str:
    template = Template(load_prompt_template(role))
    return template.safe_substitute({key: str(value) for key, value in variables.items()})


def get_system_prompt(role: str, **variables: object) -> str:
    return render_system_prompt(role, **variables)


def build_prompt_lineage(role: str, slot: ModelSlot) -> dict[str, str]:
    normalized_role = canonical_prompt_role(role)
    spec = PROMPT_SPECS[normalized_role]
    provider = provider_adapter_metadata(slot)
    schema = response_schema_metadata(normalized_role)
    lineage = PromptLineage(
        role=normalized_role,
        prompt_identifier=spec.prompt_identifier,
        prompt_version=spec.prompt_version,
        prompt_sha256=prompt_template_hash(normalized_role),
        response_schema_id=schema["response_schema_id"],
        response_schema_version=schema["response_schema_version"],
        response_schema_sha256=schema["response_schema_sha256"],
        provider_adapter_id=provider["provider_adapter_id"],
        provider_adapter_version=provider["provider_adapter_version"],
        provider=provider["provider"],
        model=provider["model"],
    )
    return asdict(lineage)


def list_prompt_roles() -> list[str]:
    return sorted(PROMPT_SPECS)


__all__ = [
    "PROMPT_SPECS",
    "PromptLineage",
    "PromptSpec",
    "build_prompt_lineage",
    "canonical_prompt_role",
    "get_system_prompt",
    "list_prompt_roles",
    "load_prompt_template",
    "prompt_template_hash",
    "prompt_dir",
    "render_system_prompt",
]
