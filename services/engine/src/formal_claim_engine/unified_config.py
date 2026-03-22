"""Unified configuration loader for the verification stack.

Reads ``verification.toml`` (walking up from cwd), applies env-var overrides,
merges role-specific LLM settings with defaults, and exposes bridge helpers
that convert into the existing ``config.py`` dataclasses.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import (
    ModelSlot,
    PipelineConfig,
    ProofProtocolConfig,
    RunBudgetConfig,
)

_CONFIG_FILENAME = "verification.toml"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff: str = "none"  # none | linear | exponential
    base_ms: int = 0
    cap_ms: int = 0
    jitter: bool = False


@dataclass
class AuditProbeConfig:
    counterexample: bool = True
    proof_search: bool = True
    dependency_slice: bool = True
    premise_deletion: bool = False
    conclusion_perturbation: bool = False


@dataclass
class VerificationBackendConfig:
    id: str = "lean-local"
    transport: str = "local_hub"
    endpoint: str = ""
    auth_token_env: str = "FWP_AUTH_TOKEN"
    origin: str = "formal-claim"
    timeout_seconds: float = 5.0
    poll_interval_seconds: float = 0.25


@dataclass
class BudgetConfig:
    wall_timeout_seconds: int = 600
    idle_timeout_seconds: int = 120
    cancel_grace_seconds: int = 5
    max_rss_mb: int = 512
    max_output_bytes: int = 32768
    max_diag_count: int = 128
    max_children: int = 2
    max_restarts: int = 0


@dataclass
class SessionOverrideConfig:
    enabled: bool = False
    codex_large_model: str = "gpt-5.4"
    codex_small_model: str = "gpt-5.4-mini"
    claude_large_model: str = ""
    claude_small_model: str = ""
    formalizer_a_model: str = "gpt-5.4-nano"
    formalizer_a_effort: str = "xhigh"
    formalizer_b_model: str = "haiku"
    formalizer_b_effort: str = ""


@dataclass
class SafeSliceConfig:
    enabled: bool = False
    src_path: str = ""
    relation_types: list[str] = field(
        default_factory=lambda: [
            "depends_on",
            "decomposes_into",
            "refines",
            "specializes",
        ]
    )
    include_baseline_slice: bool = True
    include_scope_conditions_in_context: bool = True
    include_semantics_guard_in_context: bool = True


@dataclass
class UnifiedConfig:
    # LLM
    llm_defaults: dict[str, Any]
    model_routing: dict[str, ModelSlot]
    session_override: SessionOverrideConfig = field(
        default_factory=SessionOverrideConfig
    )
    # Retry
    retry_policies: dict[str, RetryPolicy] = field(default_factory=dict)
    # Verification
    dual_formalization: bool = True
    sorry_detection: str = "strict"
    audit_probes: AuditProbeConfig = field(default_factory=AuditProbeConfig)
    verification_backend: VerificationBackendConfig = field(
        default_factory=VerificationBackendConfig
    )
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    # Integration
    certification_frequency: str = "on_request"
    http_api_port: int = 8321
    safe_slice: SafeSliceConfig = field(default_factory=SafeSliceConfig)
    # Data
    data_dir: str = "./pipeline_data"


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_config_file(start: Path | None = None) -> Path | None:
    """Locate ``verification.toml``.

    Search order:
    1. Explicit *start* path (if given and is a file).
    2. ``settings/verification.toml`` relative to the formal-claim monorepo root.
    3. Walk up from *start* (or cwd) checking each directory and its
       ``settings/`` subdirectory.
    """
    if start is not None and start.is_file():
        return start

    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        # Direct match
        candidate = directory / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
        # settings/ subdirectory (canonical location)
        candidate = directory / "settings" / _CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Env-var overrides
# ---------------------------------------------------------------------------

def _env_key(*parts: str) -> str:
    """Build ``VERIFY_SECTION_KEY`` from path segments."""
    return "_".join(["VERIFY"] + [p.upper() for p in parts])


def _apply_env_overrides(raw: dict[str, Any], prefix_parts: tuple[str, ...] = ()) -> dict[str, Any]:
    """Recursively check ``VERIFY_<SECTION>_<KEY>`` env vars and override."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        parts = (*prefix_parts, key)
        if isinstance(value, dict):
            out[key] = _apply_env_overrides(value, parts)
        else:
            env_name = _env_key(*parts)
            env_val = os.environ.get(env_name)
            if env_val is not None:
                out[key] = _coerce(env_val, value)
            else:
                out[key] = value
    return out


def _coerce(env_val: str, reference: Any) -> Any:
    """Coerce an env-var string to match the type of *reference*."""
    if isinstance(reference, bool):
        return env_val.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(reference, int):
        return int(env_val)
    if isinstance(reference, float):
        return float(env_val)
    return env_val


# ---------------------------------------------------------------------------
# Role merging
# ---------------------------------------------------------------------------

def _merge_role(defaults: dict[str, Any], role_overrides: dict[str, Any]) -> ModelSlot:
    """Produce a ``ModelSlot`` by layering *role_overrides* on *defaults*."""
    merged = {**defaults, **role_overrides}
    return ModelSlot(
        provider=str(merged.get("provider", "anthropic")),
        model=str(merged.get("model", "claude-sonnet-4-20250514")),
        temperature=float(merged.get("temperature", 0.2)),
        max_tokens=int(merged.get("max_tokens", 8192)),
        reasoning_effort=str(merged.get("reasoning_effort", "")) or None,
        api_base=str(merged.get("api_base", "")) or None,
        api_key_env=str(merged.get("api_key_env", "")) or None,
    )


# ---------------------------------------------------------------------------
# Dataclass hydration helpers
# ---------------------------------------------------------------------------

def _build_retry(d: dict[str, Any]) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=int(d.get("max_attempts", 3)),
        backoff=str(d.get("backoff", "none")),
        base_ms=int(d.get("base_ms", 0)),
        cap_ms=int(d.get("cap_ms", 0)),
        jitter=bool(d.get("jitter", False)),
    )


def _build_audit_probes(d: dict[str, Any]) -> AuditProbeConfig:
    return AuditProbeConfig(
        counterexample=bool(d.get("counterexample", True)),
        proof_search=bool(d.get("proof_search", True)),
        dependency_slice=bool(d.get("dependency_slice", True)),
        premise_deletion=bool(d.get("premise_deletion", False)),
        conclusion_perturbation=bool(d.get("conclusion_perturbation", False)),
    )


def _build_backend(d: dict[str, Any]) -> VerificationBackendConfig:
    return VerificationBackendConfig(
        id=str(d.get("id", "lean-local")),
        transport=str(d.get("transport", "local_hub")),
        endpoint=str(d.get("endpoint", "")),
        auth_token_env=str(d.get("auth_token_env", "FWP_AUTH_TOKEN")),
        origin=str(d.get("origin", "formal-claim")),
        timeout_seconds=float(d.get("timeout_seconds", 5.0)),
        poll_interval_seconds=float(d.get("poll_interval_seconds", 0.25)),
    )


def _build_budget(d: dict[str, Any]) -> BudgetConfig:
    return BudgetConfig(
        wall_timeout_seconds=int(d.get("wall_timeout_seconds", 600)),
        idle_timeout_seconds=int(d.get("idle_timeout_seconds", 120)),
        cancel_grace_seconds=int(d.get("cancel_grace_seconds", 5)),
        max_rss_mb=int(d.get("max_rss_mb", 512)),
        max_output_bytes=int(d.get("max_output_bytes", 32768)),
        max_diag_count=int(d.get("max_diag_count", 128)),
        max_children=int(d.get("max_children", 2)),
        max_restarts=int(d.get("max_restarts", 0)),
    )


def _build_session_override(d: dict[str, Any]) -> SessionOverrideConfig:
    return SessionOverrideConfig(
        enabled=bool(d.get("enabled", False)),
        codex_large_model=str(d.get("codex_large_model", "gpt-5.4")),
        codex_small_model=str(d.get("codex_small_model", "gpt-5.4-mini")),
        claude_large_model=str(d.get("claude_large_model", "")),
        claude_small_model=str(d.get("claude_small_model", "")),
        formalizer_a_model=str(d.get("formalizer_a_model", "gpt-5.4-nano")),
        formalizer_a_effort=str(d.get("formalizer_a_effort", "xhigh")),
        formalizer_b_model=str(d.get("formalizer_b_model", "haiku")),
        formalizer_b_effort=str(d.get("formalizer_b_effort", "")),
    )


def _build_safe_slice(d: dict[str, Any]) -> SafeSliceConfig:
    return SafeSliceConfig(
        enabled=bool(d.get("enabled", False)),
        src_path=str(d.get("src_path", "")),
        relation_types=[
            str(item)
            for item in list(
                d.get(
                    "relation_types",
                    ["depends_on", "decomposes_into", "refines", "specializes"],
                )
                or []
            )
        ],
        include_baseline_slice=bool(d.get("include_baseline_slice", True)),
        include_scope_conditions_in_context=bool(
            d.get("include_scope_conditions_in_context", True)
        ),
        include_semantics_guard_in_context=bool(
            d.get("include_semantics_guard_in_context", True)
        ),
    )


# ---------------------------------------------------------------------------
# Main loader
# ---------------------------------------------------------------------------

def load_config(path: Path | None = None) -> UnifiedConfig:
    """Load from ``verification.toml``, apply env overrides, merge defaults.

    If *path* is ``None`` the file is located by walking up from cwd.
    Raises ``FileNotFoundError`` when the config cannot be found.
    """
    if path is None:
        path = find_config_file()
    if path is None or not path.is_file():
        raise FileNotFoundError(
            f"Cannot locate {_CONFIG_FILENAME}. "
            "Place it at the project root or pass an explicit path."
        )

    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    raw = _apply_env_overrides(raw)

    # -- LLM ----------------------------------------------------------------
    llm = raw.get("llm", {})
    defaults = dict(llm.get("defaults", {}))
    roles_raw = llm.get("roles", {})

    model_routing: dict[str, ModelSlot] = {}
    for role_name, role_overrides in roles_raw.items():
        model_routing[role_name] = _merge_role(defaults, role_overrides or {})

    session_override = _build_session_override(llm.get("session_override", {}))

    # -- Retry ---------------------------------------------------------------
    retry_section = raw.get("retry", {})
    retry_policies: dict[str, RetryPolicy] = {
        name: _build_retry(vals)
        for name, vals in retry_section.items()
    }

    # -- Verification --------------------------------------------------------
    verif = raw.get("verification", {})

    # -- Stack / data --------------------------------------------------------
    stack = raw.get("stack", {})

    return UnifiedConfig(
        llm_defaults=defaults,
        model_routing=model_routing,
        session_override=session_override,
        retry_policies=retry_policies,
        dual_formalization=bool(verif.get("dual_formalization", True)),
        sorry_detection=str(verif.get("sorry_detection", "strict")),
        audit_probes=_build_audit_probes(verif.get("audit_probes", {})),
        verification_backend=_build_backend(verif.get("backend", {})),
        budget=_build_budget(verif.get("budget", {})),
        certification_frequency=str(
            raw.get("integration", {}).get("certification_frequency", "on_request")
        ),
        http_api_port=int(
            raw.get("integration", {}).get("http_api_port", 8321)
        ),
        safe_slice=_build_safe_slice(raw.get("integration", {}).get("safeslice", {})),
        data_dir=str(stack.get("data_dir", "./pipeline_data")),
    )


# ---------------------------------------------------------------------------
# Bridge helpers -> existing config.py types
# ---------------------------------------------------------------------------

def _apply_session_overrides(
    uc: UnifiedConfig,
    routing: dict[str, ModelSlot],
) -> dict[str, ModelSlot]:
    """Replicate the session-override logic from config.py using TOML values."""
    so = uc.session_override
    if not so.enabled:
        return routing

    overridden: dict[str, ModelSlot] = {}
    for role, slot in routing.items():
        if role == "formalizer_a":
            overridden[role] = ModelSlot(
                provider="codex_session",
                model=so.formalizer_a_model or so.codex_large_model,
                temperature=slot.temperature,
                max_tokens=slot.max_tokens,
                reasoning_effort=so.formalizer_a_effort or None,
            )
            continue
        if role == "formalizer_b":
            overridden[role] = ModelSlot(
                provider="claude_session",
                model=so.formalizer_b_model or so.claude_large_model or slot.model,
                temperature=slot.temperature,
                max_tokens=slot.max_tokens,
                reasoning_effort=so.formalizer_b_effort or None,
            )
            continue
        if slot.provider == "anthropic":
            overridden[role] = ModelSlot(
                provider="claude_session",
                model=so.claude_large_model or slot.model,
                temperature=slot.temperature,
                max_tokens=slot.max_tokens,
            )
        elif slot.provider == "openai":
            overridden[role] = ModelSlot(
                provider="codex_session",
                model=so.codex_large_model,
                temperature=slot.temperature,
                max_tokens=slot.max_tokens,
            )
        else:
            overridden[role] = slot
    return overridden


def to_pipeline_config(uc: UnifiedConfig, project_id: str = "") -> PipelineConfig:
    """Convert *UnifiedConfig* to the legacy ``PipelineConfig``."""
    routing = _apply_session_overrides(uc, dict(uc.model_routing))
    return PipelineConfig(
        project_id=project_id or "project.default",
        data_dir=uc.data_dir,
        model_routing=routing,
        proof_protocol=to_proof_protocol_config(uc),
        require_dual_formalization=uc.dual_formalization,
        max_retries_per_phase=uc.retry_policies.get(
            "workflow_phase", RetryPolicy()
        ).max_attempts,
    )


def to_proof_protocol_config(uc: UnifiedConfig) -> ProofProtocolConfig:
    """Convert *UnifiedConfig* to ``ProofProtocolConfig``."""
    vb = uc.verification_backend
    return ProofProtocolConfig(
        backend="fwp",
        transport=vb.transport,
        endpoint=vb.endpoint or None,
        target_backend_id=vb.id,
        auth_token_env=vb.auth_token_env,
        origin=vb.origin,
        timeout_seconds=vb.timeout_seconds,
        poll_interval_seconds=vb.poll_interval_seconds,
        budget=to_run_budget_config(uc),
    )


def to_run_budget_config(uc: UnifiedConfig) -> RunBudgetConfig:
    """Convert *UnifiedConfig* to ``RunBudgetConfig``."""
    b = uc.budget
    return RunBudgetConfig(
        wall_timeout_seconds=b.wall_timeout_seconds,
        idle_timeout_seconds=b.idle_timeout_seconds,
        cancel_grace_seconds=b.cancel_grace_seconds,
        max_rss_mb=b.max_rss_mb,
        max_output_bytes=b.max_output_bytes,
        max_diag_count=b.max_diag_count,
        max_children=b.max_children,
        max_restarts=b.max_restarts,
    )
