"""FWP proof runtime lineage for OAE trace export (BRG-001 / BRG-002).

Captures workspace, session, backend, and artifact references from proof
protocol execution results so that proof runtime provenance is available in
trace.json and sidecar_meta.json without promoting raw payloads to canonical
artifact status.

OAE remains the assurance semantics owner; this module provides provenance
references only.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_of(data: Any) -> str:
    """Deterministic SHA-256 of a JSON-serialisable value."""
    serialized = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# BRG-001 — ProofLineageCollector
# ---------------------------------------------------------------------------


class ProofLineageCollector:
    """Collects FWP proof runtime lineage for trace export.

    Captures workspace, session, backend, and artifact references
    from proof protocol execution results.
    """

    def extract_lineage(self, build_result: Any) -> dict[str, Any]:
        """Extract provenance metadata from a BuildSessionResult.

        Returns dict with:
        - workspace_id: str | None
        - session_id: str | None
        - backend_type: str | None (e.g., 'filesystem', 'docker', 'remote')
        - artifact_refs: list[dict] -- each with {artifact_id, path, kind, sha256}
        - session_fingerprint: str | None
        - duration_seconds: float | None
        - return_code: int | None
        - canonical: bool -- True for primary results, False for fallbacks
        """
        if build_result is None:
            return self._empty_lineage()

        # Support both dataclass-style (BuildSessionResult) and dict payloads
        if isinstance(build_result, dict):
            return self._extract_lineage_from_dict(build_result)

        workspace_id = getattr(build_result, "workspace_dir", None)
        session_id = getattr(build_result, "session_dir", None)
        session_fingerprint = getattr(build_result, "session_fingerprint", None)
        duration_seconds = getattr(build_result, "duration_seconds", None)
        return_code = getattr(build_result, "return_code", None)
        success = getattr(build_result, "success", False)

        raw_artifact_paths: dict[str, str] = dict(
            getattr(build_result, "artifact_paths", None) or {}
        )
        artifact_refs = self._build_artifact_refs(raw_artifact_paths)

        command = list(getattr(build_result, "command", None) or [])
        backend_type = self._infer_backend_type(command, workspace_id)

        return {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "backend_type": backend_type,
            "artifact_refs": artifact_refs,
            "session_fingerprint": session_fingerprint,
            "duration_seconds": float(duration_seconds) if duration_seconds is not None else None,
            "return_code": int(return_code) if return_code is not None else None,
            "canonical": bool(success),
        }

    def extract_verifier_lineage(self, verifier_result: Any) -> dict[str, Any]:
        """Extract verifier execution provenance.

        Returns reference-only metadata about the verifier interpretation.
        """
        if verifier_result is None:
            return {
                "verifier_ref": None,
                "proof_status": None,
                "sorry_count": None,
                "oops_count": None,
                "canonical": False,
            }

        if isinstance(verifier_result, dict):
            data = verifier_result
        else:
            data = {}

        proof_status = data.get("proof_status")
        sorry_count = data.get("sorry_count")
        oops_count = data.get("oops_count")

        # Build a deterministic reference hash from the verifier output
        # WITHOUT including the full raw payload
        ref_material = {
            "proof_status": proof_status,
            "sorry_count": sorry_count,
            "oops_count": oops_count,
        }
        verifier_ref = _sha256_of(ref_material)

        canonical = proof_status in {"proof_complete", "built"}

        return {
            "verifier_ref": verifier_ref,
            "proof_status": proof_status,
            "sorry_count": int(sorry_count) if sorry_count is not None else None,
            "oops_count": int(oops_count) if oops_count is not None else None,
            "canonical": canonical,
        }

    def extract_contract_pack_provenance(self, contract_pack: Any) -> dict[str, Any]:
        """Extract contract-pack references without promoting raw payload.

        Returns reference-only metadata: hash, session, backend refs.
        Raw contract-pack content goes to sidecar_meta.json, never trace.json.
        """
        if contract_pack is None:
            return {
                "contract_pack_ref": None,
                "contract_pack_hash": None,
                "session_ref": None,
                "backend_ref": None,
                "canonical": False,
            }

        if isinstance(contract_pack, dict):
            data = contract_pack
        else:
            data = {}

        contract_pack_ref = data.get("contract_pack_ref")
        session_ref = data.get("workspace_ref") or data.get("session_name")
        backend_ref = (
            data.get("target_backend")
            or data.get("backend")
            or data.get("proof_backend")
        )

        # Hash the entire contract pack for integrity verification
        # but do NOT include the raw content in the reference
        contract_pack_hash = _sha256_of(data)

        return {
            "contract_pack_ref": contract_pack_ref,
            "contract_pack_hash": contract_pack_hash,
            "session_ref": session_ref,
            "backend_ref": backend_ref,
            "canonical": bool(data.get("success", False)),
        }

    # --- internal helpers ---

    def _empty_lineage(self) -> dict[str, Any]:
        return {
            "workspace_id": None,
            "session_id": None,
            "backend_type": None,
            "artifact_refs": [],
            "session_fingerprint": None,
            "duration_seconds": None,
            "return_code": None,
            "canonical": False,
        }

    def _extract_lineage_from_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        raw_artifact_paths: dict[str, str] = dict(data.get("artifact_paths") or {})
        artifact_refs = self._build_artifact_refs(raw_artifact_paths)
        command = list(data.get("command") or [])
        workspace_id = data.get("workspace_dir") or data.get("workspace_id")
        backend_type = self._infer_backend_type(command, workspace_id)

        return {
            "workspace_id": workspace_id,
            "session_id": data.get("session_dir") or data.get("session_id"),
            "backend_type": backend_type,
            "artifact_refs": artifact_refs,
            "session_fingerprint": data.get("session_fingerprint"),
            "duration_seconds": (
                float(data["duration_seconds"])
                if data.get("duration_seconds") is not None
                else None
            ),
            "return_code": (
                int(data["return_code"])
                if data.get("return_code") is not None
                else None
            ),
            "canonical": bool(data.get("success", False)),
        }

    @staticmethod
    def _build_artifact_refs(
        raw_paths: dict[str, str],
    ) -> list[dict[str, Any]]:
        """Build reference-only artifact entries (no raw content)."""
        refs: list[dict[str, Any]] = []
        for artifact_id, path in raw_paths.items():
            ref: dict[str, Any] = {
                "artifact_id": str(artifact_id),
                "path": str(path),
            }
            # Classify the artifact kind from the path or id
            kind = "unknown"
            lower_path = str(path).lower()
            if ".thy" in lower_path:
                kind = "theory"
            elif ".lean" in lower_path:
                kind = "lean_source"
            elif ".v" in lower_path:
                kind = "coq_source"
            elif "log" in lower_path:
                kind = "build_log"
            ref["kind"] = kind
            # Hash the artifact reference itself, NOT the content
            ref["sha256"] = _sha256_of(ref)
            refs.append(ref)
        return refs

    @staticmethod
    def _infer_backend_type(
        command: list[str],
        workspace_id: str | None,
    ) -> str | None:
        """Infer the proof backend type from execution context."""
        if command:
            cmd_str = " ".join(command).lower()
            if "fwp" in cmd_str:
                return "fwp"
            if "docker" in cmd_str:
                return "docker"
            if "filesystem" in cmd_str or "local" in cmd_str:
                return "filesystem"
        if workspace_id:
            ws_lower = str(workspace_id).lower()
            if "fwp" in ws_lower or "workspace://" in ws_lower:
                return "fwp"
        return None


# ---------------------------------------------------------------------------
# BRG-002 — ProofAuditProvenance
# ---------------------------------------------------------------------------


class ProofAuditProvenance:
    """Preserves proof-audit request/response provenance.

    Captures what was sent to the proof system and what came back,
    as references (not raw payloads).
    """

    def capture_request_ref(self, request: Any) -> dict[str, Any]:
        """Capture a reference to the proof-audit request.

        Returns: {request_hash, theory_session_id, claim_id, timestamp}
        NOT the raw request payload.
        """
        if request is None:
            return {
                "request_hash": None,
                "theory_session_id": None,
                "claim_id": None,
                "timestamp": _now_iso(),
            }

        if isinstance(request, dict):
            data = request
        else:
            # Support dataclass / object-style requests
            data = {}
            for attr in (
                "session_name",
                "session_dir",
                "target_theory",
                "target_theorem",
                "claim_id",
                "subject_id",
                "request_id",
            ):
                val = getattr(request, attr, None)
                if val is not None:
                    data[attr] = val

        # Build a hash of the request for traceability
        request_hash = _sha256_of(data)

        theory_session_id = (
            data.get("session_name")
            or data.get("session_dir")
        )
        claim_id = (
            data.get("claim_id")
            or data.get("subject_id")
        )

        return {
            "request_hash": request_hash,
            "theory_session_id": str(theory_session_id) if theory_session_id else None,
            "claim_id": str(claim_id) if claim_id else None,
            "timestamp": _now_iso(),
        }

    def capture_response_ref(self, response: Any) -> dict[str, Any]:
        """Capture a reference to the proof-audit response.

        Returns: {response_hash, success, sorry_count, artifact_count, timestamp}
        NOT the raw response payload.
        """
        if response is None:
            return {
                "response_hash": None,
                "success": None,
                "sorry_count": None,
                "artifact_count": None,
                "timestamp": _now_iso(),
            }

        if isinstance(response, dict):
            data = response
        else:
            data = {}

        # Count artifacts from artifact_paths if available
        artifact_paths = data.get("artifact_paths") or {}
        artifact_count = len(artifact_paths)

        # Build a reference hash of the response (not the response itself)
        # Include only structural metadata, never raw stdout/stderr/content
        ref_material = {
            "success": data.get("success"),
            "sorry_count": data.get("sorry_count", 0),
            "artifact_count": artifact_count,
            "session_fingerprint": data.get("session_fingerprint"),
        }
        response_hash = _sha256_of(ref_material)

        return {
            "response_hash": response_hash,
            "success": bool(data.get("success", False)),
            "sorry_count": int(data.get("sorry_count", 0)),
            "artifact_count": artifact_count,
            "timestamp": _now_iso(),
        }


# ---------------------------------------------------------------------------
# Trace-export integration helpers
# ---------------------------------------------------------------------------


def enrich_build_results_with_lineage(
    build_results: dict[str, Any],
    build_session_results: dict[str, Any],
) -> dict[str, Any]:
    """Enrich per-label build_results with FWP lineage for trace export.

    Parameters
    ----------
    build_results:
        The existing per-label build results dict as built by the orchestrator.
    build_session_results:
        Raw BuildSessionResult objects (or dicts) keyed by label.

    Returns
    -------
    The same dict, augmented with a ``proof_lineage`` key per label.
    """
    collector = ProofLineageCollector()
    enriched = dict(build_results)
    for label, raw_result in build_session_results.items():
        if label in enriched:
            enriched[label] = dict(enriched[label])
            enriched[label]["proof_lineage"] = collector.extract_lineage(raw_result)
    return enriched


def enrich_verifier_results_with_lineage(
    verifier_results: dict[str, Any],
) -> dict[str, Any]:
    """Enrich per-label verifier_results with provenance refs.

    Parameters
    ----------
    verifier_results:
        The existing per-label verifier results dict.

    Returns
    -------
    The same dict, augmented with a ``verifier_lineage`` key per label.
    """
    collector = ProofLineageCollector()
    enriched = dict(verifier_results)
    for label, result in verifier_results.items():
        enriched[label] = dict(result) if isinstance(result, dict) else result
        if isinstance(enriched[label], dict):
            enriched[label]["verifier_lineage"] = collector.extract_verifier_lineage(
                result
            )
    return enriched


def build_proof_audit_provenance(
    request: Any,
    response: Any,
) -> dict[str, Any]:
    """Build proof-audit provenance record for sidecar_meta.json.

    Returns a reference-only dict suitable for the sidecar_meta provenance
    section. Raw payloads are never included.
    """
    provenance = ProofAuditProvenance()
    return {
        "request_ref": provenance.capture_request_ref(request),
        "response_ref": provenance.capture_response_ref(response),
    }


def split_lineage_for_export(
    build_results: dict[str, Any],
    verifier_results: dict[str, Any],
    proof_audit: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split lineage into trace-safe and sidecar portions.

    Returns
    -------
    (trace_lineage, sidecar_lineage)
        trace_lineage: model-safe references for trace.json
        sidecar_lineage: extended provenance for sidecar_meta.json
    """
    collector = ProofLineageCollector()

    trace_lineage: dict[str, Any] = {
        "build_lineage": {},
        "verifier_lineage": {},
    }
    sidecar_lineage: dict[str, Any] = {
        "build_lineage_extended": {},
        "verifier_lineage_extended": {},
        "contract_pack_provenance": None,
        "proof_audit_provenance": None,
    }

    for label, result in build_results.items():
        if not isinstance(result, dict):
            continue
        lineage = result.get("proof_lineage")
        if lineage is None:
            lineage = collector.extract_lineage(result)
        # trace.json gets only IDs and fingerprint
        trace_lineage["build_lineage"][label] = {
            "workspace_id": lineage.get("workspace_id"),
            "session_id": lineage.get("session_id"),
            "session_fingerprint": lineage.get("session_fingerprint"),
            "backend_type": lineage.get("backend_type"),
            "canonical": lineage.get("canonical", False),
        }
        # sidecar gets the full lineage including artifact_refs
        sidecar_lineage["build_lineage_extended"][label] = lineage

    for label, result in verifier_results.items():
        if not isinstance(result, dict):
            continue
        v_lineage = result.get("verifier_lineage")
        if v_lineage is None:
            v_lineage = collector.extract_verifier_lineage(result)
        trace_lineage["verifier_lineage"][label] = {
            "verifier_ref": v_lineage.get("verifier_ref"),
            "proof_status": v_lineage.get("proof_status"),
            "canonical": v_lineage.get("canonical", False),
        }
        sidecar_lineage["verifier_lineage_extended"][label] = v_lineage

    if proof_audit is not None:
        sidecar_lineage["contract_pack_provenance"] = (
            collector.extract_contract_pack_provenance(proof_audit)
        )

    return trace_lineage, sidecar_lineage
