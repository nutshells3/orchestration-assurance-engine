"""Configuration for the formal-claim engine and its FWP proof seam."""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "packages" / "contracts" / "schemas").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from formal_claim_engine.")


REPO_ROOT = resolve_repo_root()
SCHEMA_DIR = REPO_ROOT / "packages" / "contracts" / "schemas"

# ---------------------------------------------------------------------------
# Model provider abstraction
# ---------------------------------------------------------------------------

@dataclass
class ModelSlot:
    """One LLM endpoint used by a specific agent role."""
    provider: str          # "anthropic" | "openai" | "local" | ...
    model: str             # e.g. "claude-sonnet-4-20250514", "gpt-4o", ...
    temperature: float = 0.2
    max_tokens: int = 8192
    reasoning_effort: str | None = None
    api_base: str | None = None   # override for local/proxy endpoints
    api_key_env: str | None = None  # env var name holding the key


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _session_override_enabled() -> bool:
    return _env_flag("FORMAL_CLAIM_USE_SESSION_PROVIDERS") or _env_flag("FORMAL_CLAIM_USE_CODEX_SESSION")


def _session_model_for(role: str) -> str:
    large_default = str(os.environ.get("FORMAL_CLAIM_CODEX_LARGE_MODEL") or "gpt-5.4").strip()
    small_default = str(os.environ.get("FORMAL_CLAIM_CODEX_SMALL_MODEL") or "gpt-5.4-mini").strip()
    role_map = {
        "planner": large_default,
        "claim_graph_agent": large_default,
        "claim_tracer": large_default,
        "formalizer_a": str(os.environ.get("FORMAL_CLAIM_FORMALIZER_A_MODEL") or "gpt-5.4-nano").strip(),
        "proof_verifier": large_default,
        "auditor": large_default,
        "research_agent": large_default,
        "dev_agent": small_default,
        "policy_engine": large_default,
    }
    return role_map.get(role, small_default)


def _session_effort_for(role: str) -> str | None:
    role_map = {
        "formalizer_a": str(os.environ.get("FORMAL_CLAIM_FORMALIZER_A_EFFORT") or "xhigh").strip(),
    }
    value = role_map.get(role)
    return value or None


def _claude_session_model_for(role: str, slot: ModelSlot) -> str:
    large_default = str(os.environ.get("FORMAL_CLAIM_CLAUDE_LARGE_MODEL") or slot.model).strip()
    small_default = str(os.environ.get("FORMAL_CLAIM_CLAUDE_SMALL_MODEL") or slot.model).strip()
    role_map = {
        "planner": large_default,
        "claim_graph_agent": large_default,
        "claim_tracer": large_default,
        "formalizer_b": str(os.environ.get("FORMAL_CLAIM_FORMALIZER_B_MODEL") or "haiku").strip(),
        "proof_verifier": large_default,
        "auditor": large_default,
        "dev_agent": small_default,
        "policy_engine": large_default,
    }
    return role_map.get(role, large_default)


def _claude_session_effort_for(role: str) -> str | None:
    role_map = {
        "formalizer_b": str(os.environ.get("FORMAL_CLAIM_FORMALIZER_B_EFFORT") or "").strip(),
    }
    value = role_map.get(role)
    return value or None


def _with_session_override(routing: dict[str, ModelSlot]) -> dict[str, ModelSlot]:
    if not _session_override_enabled():
        return routing
    overridden: dict[str, ModelSlot] = {}
    for role, slot in routing.items():
        if role == "formalizer_a":
            overridden[role] = ModelSlot(
                provider="codex_session",
                model=_session_model_for(role),
                temperature=slot.temperature,
                max_tokens=slot.max_tokens,
                reasoning_effort=_session_effort_for(role),
            )
            continue
        if role == "formalizer_b":
            overridden[role] = ModelSlot(
                provider="claude_session",
                model=_claude_session_model_for(role, slot),
                temperature=slot.temperature,
                max_tokens=slot.max_tokens,
                reasoning_effort=_claude_session_effort_for(role),
            )
            continue
        if slot.provider == "anthropic":
            overridden[role] = ModelSlot(
                provider="claude_session",
                model=_claude_session_model_for(role, slot),
                temperature=slot.temperature,
                max_tokens=slot.max_tokens,
                reasoning_effort=_claude_session_effort_for(role),
            )
        elif slot.provider == "openai":
            overridden[role] = ModelSlot(
                provider="codex_session",
                model=_session_model_for(role),
                temperature=slot.temperature,
                max_tokens=slot.max_tokens,
                reasoning_effort=_session_effort_for(role),
            )
        else:
            overridden[role] = slot
    return overridden


# ---------------------------------------------------------------------------
# Default routing table  (edit this to assign models)
# ---------------------------------------------------------------------------

DEFAULT_MODEL_ROUTING: dict[str, ModelSlot] = {
    # --- intent layer ---
    "planner": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.3,
        max_tokens=8192,
    ),
    "claim_graph_agent": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.2,
        max_tokens=8192,
    ),
    "claim_tracer": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.15,
        max_tokens=8192,
    ),
    # --- formal layer ---
    "formalizer_a": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.1,
        max_tokens=16384,
    ),
    "formalizer_b": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.1,
        max_tokens=16384,
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "proof_verifier": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.0,
        max_tokens=8192,
    ),
    # --- audit / adversary ---
    "auditor": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.2,
        max_tokens=8192,
    ),
    # --- support layer ---
    "research_agent": ModelSlot(
        provider="openai",
        model="gpt-4o",
        temperature=0.3,
        max_tokens=8192,
    ),
    "dev_agent": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.2,
        max_tokens=8192,
    ),
    # --- policy ---
    "policy_engine": ModelSlot(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        temperature=0.0,
        max_tokens=4096,
    ),
}


def default_model_routing() -> dict[str, ModelSlot]:
    return _with_session_override(dict(DEFAULT_MODEL_ROUTING))

# ---------------------------------------------------------------------------
# Generic governed proof-run settings
# ---------------------------------------------------------------------------

@dataclass
class RunBudgetConfig:
    wall_timeout_seconds: int = 600
    idle_timeout_seconds: int = 120
    cancel_grace_seconds: int = 5
    max_rss_mb: int = 512
    max_output_bytes: int = 32768
    max_diag_count: int = 128
    max_children: int = 2
    max_restarts: int = 0


@dataclass
class ProofProtocolConfig:
    # formal-claim no longer hosts backend-specific proof runtimes directly.
    backend: str = "fwp"
    transport: str = "local_hub"
    endpoint: str | None = None
    target_backend_id: str = "lean-local"
    auth_token_env: str = "FWP_AUTH_TOKEN"
    origin: str = "formal-claim"
    timeout_seconds: float = 5.0
    poll_interval_seconds: float = 0.25
    fwp_repo_root: str | None = None
    budget: RunBudgetConfig = field(default_factory=RunBudgetConfig)


# ---------------------------------------------------------------------------
# Pipeline-wide settings
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    project_id: str = "project.default"
    data_dir: str = "./pipeline_data"
    model_routing: dict[str, ModelSlot] = field(
        default_factory=default_model_routing
    )
    proof_protocol: ProofProtocolConfig = field(default_factory=ProofProtocolConfig)
    require_dual_formalization: bool = True
    max_retries_per_phase: int = 3


def proof_backend_family(backend_id: str) -> str:
    normalized = str(backend_id or "").strip().lower()
    if "lean" in normalized:
        return "lean"
    if "rocq" in normalized or "coq" in normalized:
        return "rocq"
    return "isabelle"


def proof_source_extension(backend_id: str) -> str:
    return {
        "lean": ".lean",
        "rocq": ".v",
        "isabelle": ".thy",
    }[proof_backend_family(backend_id)]


def proof_language_id(backend_id: str) -> str:
    return {
        "lean": "lean",
        "rocq": "coq",
        "isabelle": "isabelle",
    }[proof_backend_family(backend_id)]


def proof_system_name(backend_id: str) -> str:
    return {
        "lean": "lean4",
        "rocq": "rocq",
        "isabelle": "isabelle_hol",
    }[proof_backend_family(backend_id)]
