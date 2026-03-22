"""High-level certification API for external consumers.

Provides ``certified()`` (full pipeline) and ``verify_only()`` (proof build
only) as the two entry-points that _iteration/first (Rust) and
_iteration/second (Node.js MCP) call through HTTP or direct import.
"""

from __future__ import annotations

import asyncio
import enum
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PipelineConfig
from .engine_api import FormalClaimEngineAPI
from .proof_protocol import FilesystemProofAdapter
from .store import canonical_artifact_id
from .unified_config import (
    UnifiedConfig,
    load_config,
    to_pipeline_config,
    to_proof_protocol_config,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

class CertificationVerdict(enum.Enum):
    """Aggregate verdict for a single claim."""

    CERTIFIED = "certified"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


@dataclass
class VerificationResult:
    """Outcome of a standalone proof build (no LLM)."""

    success: bool
    sorry_count: int = 0
    oops_count: int = 0
    diagnostics: list[str] = field(default_factory=list)
    backend_id: str = ""
    duration_seconds: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CertificationResult:
    """Full pipeline outcome for one claim."""

    verdict: CertificationVerdict
    claim_id: str = ""
    project_id: str = ""
    gate: str = ""
    assurance_profile: dict[str, Any] = field(default_factory=dict)
    dual_formalization: dict[str, Any] = field(default_factory=dict)
    audit: dict[str, Any] = field(default_factory=dict)
    verification_a: VerificationResult | None = None
    verification_b: VerificationResult | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

def get_config(path: str | Path | None = None) -> UnifiedConfig:
    """Load and return the current ``UnifiedConfig``.

    If *path* is ``None`` the file is located by walking up from cwd.
    """
    config_path = Path(path) if path is not None else None
    return load_config(config_path)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_engine(
    config_overrides: dict[str, Any] | None,
    data_dir: str | None = None,
) -> tuple[FormalClaimEngineAPI, PipelineConfig]:
    """Build an engine instance, optionally applying config overrides."""
    try:
        uc = load_config()
    except FileNotFoundError:
        uc = None

    if uc is not None:
        pipeline_config = to_pipeline_config(uc)
    else:
        pipeline_config = PipelineConfig()

    if config_overrides:
        override_fields: dict[str, Any] = {}
        if "data_dir" in config_overrides:
            override_fields["data_dir"] = str(config_overrides["data_dir"])
        if "max_retries_per_phase" in config_overrides:
            override_fields["max_retries_per_phase"] = int(
                config_overrides["max_retries_per_phase"]
            )
        if "require_dual_formalization" in config_overrides:
            override_fields["require_dual_formalization"] = bool(
                config_overrides["require_dual_formalization"]
            )
        if override_fields:
            from dataclasses import replace

            pipeline_config = replace(pipeline_config, **override_fields)

    engine = FormalClaimEngineAPI(
        config=pipeline_config,
        data_dir=data_dir or pipeline_config.data_dir,
    )
    return engine, pipeline_config


def _extract_verification(
    build_results: dict[str, Any],
    label: str,
) -> VerificationResult | None:
    """Pull a VerificationResult for formalizer A or B from build_results."""
    key = f"formalizer_{label.lower()}"
    entry = build_results.get(key)
    if entry is None:
        return None

    return VerificationResult(
        success=bool(entry.get("success", False)),
        sorry_count=int(entry.get("sorry_count", 0)),
        oops_count=int(entry.get("oops_count", 0)),
        diagnostics=list(entry.get("diagnostics") or []),
        backend_id=str(entry.get("backend_id", "")),
        duration_seconds=float(entry.get("duration_seconds", 0.0)),
        raw=dict(entry),
    )


def _derive_verdict(
    profile: dict[str, Any],
    build_results: dict[str, Any],
    audit_output: dict[str, Any],
) -> CertificationVerdict:
    """Determine the aggregate verdict from profile, build, and audit data."""
    gate = str(profile.get("gate", "")).lower()
    formal_status = str(profile.get("formal_status", "")).lower()

    if gate in ("rejected", "blocked"):
        return CertificationVerdict.REFUTED

    if gate in ("assured", "accepted"):
        if formal_status in ("verified", "certified"):
            return CertificationVerdict.CERTIFIED

    # Check if any build truly succeeded with no sorry/oops
    any_clean_build = False
    for key in ("formalizer_a", "formalizer_b"):
        entry = build_results.get(key, {})
        if isinstance(entry, dict) and entry.get("success"):
            sorry = int(entry.get("sorry_count", 0))
            oops = int(entry.get("oops_count", 0))
            if sorry == 0 and oops == 0:
                any_clean_build = True

    if any_clean_build and gate not in ("rejected", "blocked"):
        if formal_status in ("verified", "certified"):
            return CertificationVerdict.CERTIFIED

    # Explicit refutation signals
    audit_verdict = str(audit_output.get("verdict", "")).lower()
    if audit_verdict in ("refuted", "rejected"):
        return CertificationVerdict.REFUTED

    return CertificationVerdict.INCONCLUSIVE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def certified(
    claim_text: str,
    *,
    config_overrides: dict[str, Any] | None = None,
    domain: str = "development",
    project_name: str | None = None,
) -> CertificationResult:
    """Run the full certification pipeline for a single natural-language claim.

    Steps: create project -> structure claim -> formalize (dual) -> verify
    (proof build) -> audit -> compute assurance profile.
    """
    errors: list[str] = []
    name = project_name or f"cert-{uuid.uuid4().hex[:8]}"

    try:
        engine, _ = _make_engine(config_overrides)
    except Exception as exc:
        return CertificationResult(
            verdict=CertificationVerdict.ERROR,
            errors=[f"config_load_failed: {exc}"],
        )

    # 1. Create project
    try:
        handle = engine.create_project(name=name, domain=domain, description=claim_text)
        project_id = handle.project_id
    except Exception as exc:
        return CertificationResult(
            verdict=CertificationVerdict.ERROR,
            errors=[f"project_create_failed: {exc}"],
        )

    # 2. Structure claim
    try:
        structuring = await engine.run_claim_structuring(project_id, claim_text)
    except Exception as exc:
        errors.append(f"claim_structuring_failed: {exc}")
        return CertificationResult(
            verdict=CertificationVerdict.ERROR,
            project_id=project_id,
            errors=errors,
        )

    # Find the first claim ID from the structured graph
    claims = list(structuring.claim_graph.get("claims", []))
    if not claims:
        errors.append("no_claims_extracted: claim structuring produced no claims")
        return CertificationResult(
            verdict=CertificationVerdict.INCONCLUSIVE,
            project_id=project_id,
            errors=errors,
        )
    claim_id = canonical_artifact_id(claims[0].get("claim_id", ""))

    # 3. Formalize + Verify + Audit (phase2)
    build_results: dict[str, Any] = {}
    audit_output: dict[str, Any] = {}
    profile_data: dict[str, Any] = {}
    dual_formalization_data: dict[str, Any] = {}
    gate = ""

    try:
        audit_result = await engine.run_audit(project_id, claim_id)
        build_results = dict(audit_result.build_results or {})
        audit_output = dict(audit_result.audit_output or {})
        profile_data = dict(audit_result.profile or {})
        dual_formalization_data = dict(audit_result.audit_workflow or {})
        gate = str(profile_data.get("gate", ""))
    except Exception as exc:
        errors.append(f"audit_pipeline_failed: {exc}")
        tb = traceback.format_exc()
        errors.append(f"traceback: {tb}")

    # 4. Recompute profile if we have audit data
    if audit_output and not profile_data:
        try:
            recomputed = engine.recompute_profile(project_id, claim_id, {
                "verifier_results": build_results,
                "audit_output": audit_output,
            })
            profile_data = dict(recomputed.profile or {})
            gate = str(profile_data.get("gate", ""))
        except Exception as exc:
            errors.append(f"profile_recompute_failed: {exc}")

    verdict = _derive_verdict(profile_data, build_results, audit_output)
    if errors and verdict != CertificationVerdict.REFUTED:
        verdict = CertificationVerdict.ERROR if not profile_data else verdict

    return CertificationResult(
        verdict=verdict,
        claim_id=claim_id,
        project_id=project_id,
        gate=gate,
        assurance_profile=profile_data,
        dual_formalization=dual_formalization_data,
        audit=audit_output,
        verification_a=_extract_verification(build_results, "A"),
        verification_b=_extract_verification(build_results, "B"),
        errors=errors,
    )


async def verify_only(
    proof_source: str,
    *,
    backend: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> VerificationResult:
    """Run a standalone proof build -- no LLM calls, no audit.

    *proof_source* is the raw theory text. The backend is resolved from
    ``verification.toml`` (or *backend* override).
    """
    start = time.monotonic()
    try:
        uc = load_config()
    except FileNotFoundError:
        uc = None

    if uc is not None:
        proof_config = to_proof_protocol_config(uc)
        pipeline_config = to_pipeline_config(uc)
    else:
        from .config import ProofProtocolConfig

        proof_config = ProofProtocolConfig()
        pipeline_config = PipelineConfig()

    if config_overrides:
        if "data_dir" in config_overrides:
            from dataclasses import replace

            pipeline_config = replace(
                pipeline_config,
                data_dir=str(config_overrides["data_dir"]),
            )

    target_backend = backend or proof_config.target_backend_id or "lean-local"

    adapter = FilesystemProofAdapter(config=pipeline_config)
    session_name = f"verify-{uuid.uuid4().hex[:8]}"
    theory_name = "Main"

    try:
        adapter.prepare_theory_session(
            session_dir=session_name,
            session_name=session_name,
            theory_name=theory_name,
            theory_body=proof_source,
        )
        result = adapter.build_session(
            session_name=session_name,
            session_dir=session_name,
            target_theory=theory_name,
            target_theorem="",
        )
    except Exception as exc:
        elapsed = time.monotonic() - start
        return VerificationResult(
            success=False,
            diagnostics=[f"build_failed: {exc}"],
            backend_id=target_backend,
            duration_seconds=elapsed,
            raw={"error": str(exc), "traceback": traceback.format_exc()},
        )

    elapsed = time.monotonic() - start

    success = bool(getattr(result, "success", False))
    sorry_count = int(getattr(result, "sorry_count", 0))
    oops_count = int(getattr(result, "oops_count", 0))

    diagnostics: list[str] = []
    stderr = str(getattr(result, "stderr", ""))
    if stderr:
        diagnostics.append(stderr)

    raw_dict: dict[str, Any] = {}
    if hasattr(result, "__dict__"):
        for k, v in result.__dict__.items():
            try:
                raw_dict[k] = v
            except Exception:
                raw_dict[k] = str(v)

    return VerificationResult(
        success=success and sorry_count == 0 and oops_count == 0,
        sorry_count=sorry_count,
        oops_count=oops_count,
        diagnostics=diagnostics,
        backend_id=target_backend,
        duration_seconds=elapsed,
        raw=raw_dict,
    )
