"""Assembles phase2 and phase3 per-claim sections from OAE workflow outputs.

Reads from the ArtifactStore and PromotionStateMachine to populate the
PipelineTraceV1 trace.json structure.  All data is read-only -- no workflow
logic is mutated.
"""

from __future__ import annotations

import logging
from typing import Any

from .dual_formalization_workflow import (
    FormalizationAttemptStatus,
    normalize_assumptions,
    output_sha256,
)
from .models import AssuranceProfile
from .store import ArtifactStore, canonical_artifact_id

log = logging.getLogger(__name__)


def _safe_model_dump(model: Any) -> dict[str, Any]:
    """Return a JSON-serialisable dict from a Pydantic model or passthrough dict."""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", exclude_none=True)
    if isinstance(model, dict):
        return dict(model)
    return {}


# ---------------------------------------------------------------------------
# Phase-2 helpers
# ---------------------------------------------------------------------------


def _extract_dual_formalization(
    store: ArtifactStore,
    project_id: str,
    claim_id: str,
) -> dict[str, Any] | None:
    """Extract dual formalization results for a claim from stored workflow state.

    Searches review events for the dual_formalization workflow state that was
    persisted as opaque artifact metadata, falling back to scanning
    ``evaluation_evidence_bundles`` keyed by claim.
    """
    # Strategy: review events tagged as "dual_formalization" carry the full
    # workflow state in their metadata.  We scan for these first.
    events = store.query_review_events(claim_id)
    workflow_state: dict[str, Any] | None = None
    for event in reversed(events):
        meta = event.get("metadata") or {}
        if event.get("event_type") in {
            "dual_formalization",
            "formalization_attempt",
        } and "attempts" in meta:
            workflow_state = meta
            break
        if "dual_formalization_workflow" in meta:
            workflow_state = meta["dual_formalization_workflow"]
            break

    if workflow_state is None:
        return None

    attempts = list(workflow_state.get("attempts") or [])
    attempt_a: dict[str, Any] | None = None
    attempt_b: dict[str, Any] | None = None
    for attempt in attempts:
        label = str(attempt.get("formalizer_label") or "")
        status = str(attempt.get("status") or "")
        if status != FormalizationAttemptStatus.succeeded.value:
            continue
        output = attempt.get("output") or {}
        entry = {
            "output": str(output.get("proof_source") or ""),
            "sha256": str(output_sha256(output) or ""),
            "assumptions": normalize_assumptions(output),
            "back_translation": str(attempt.get("back_translation") or ""),
        }
        if label == "A" and attempt_a is None:
            attempt_a = entry
        elif label == "B" and attempt_b is None:
            attempt_b = entry

    divergence_raw = workflow_state.get("divergence") or {}
    divergence = {
        "classification": str(divergence_raw.get("classification") or ""),
        "primary_target_match": divergence_raw.get("primary_target_match"),
        "back_translation_match": divergence_raw.get("back_translation_match"),
        "code_sha_match": divergence_raw.get("code_sha_match"),
        "assumptions_only_in_a": list(
            divergence_raw.get("assumptions_only_in_a") or []
        ),
        "assumptions_only_in_b": list(
            divergence_raw.get("assumptions_only_in_b") or []
        ),
    }

    return {
        "attempt_a": attempt_a,
        "attempt_b": attempt_b,
        "divergence": divergence,
    }


def _extract_audit(
    profile: AssuranceProfile | None,
) -> dict[str, Any]:
    """Build the audit sub-section from an AssuranceProfile snapshot."""
    if profile is None:
        return {
            "trust_frontier": None,
            "model_health": None,
            "intent_alignment": None,
            "blocking_issues": [],
        }

    profile_data = _safe_model_dump(profile)
    return {
        "trust_frontier": profile_data.get("trust_frontier"),
        "model_health": profile_data.get("model_health"),
        "intent_alignment": profile_data.get("intent_alignment"),
        "blocking_issues": list(
            (profile_data.get("obligations") or {}).get("blocking_obligations") or []
        ),
    }


def _extract_promotion_transitions(
    store: ArtifactStore,
    claim_id: str,
    profile_id: str | None,
) -> list[dict[str, Any]]:
    """Return the list of promotion transitions for a claim from review events."""
    events = store.query_review_events(claim_id)
    transitions: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "promotion_transition":
            continue
        meta = event.get("metadata") or {}
        if profile_id and canonical_artifact_id(
            meta.get("profile_id", "")
        ) != canonical_artifact_id(profile_id):
            continue
        transitions.append(
            {
                "event_id": event.get("event_id"),
                "from_gate": meta.get("from_gate"),
                "to_gate": meta.get("to_gate"),
                "actor": event.get("actor"),
                "actor_role": str(event.get("actor_role") or meta.get("actor_role") or ""),
                "override": bool(meta.get("override")),
                "rationale": str(meta.get("rationale") or ""),
                "created_at": event.get("created_at"),
            }
        )
    return transitions


# ---------------------------------------------------------------------------
# Phase-3 helpers
# ---------------------------------------------------------------------------


def _extract_research_output(
    store: ArtifactStore,
    project_id: str,
    claim_id: str,
) -> dict[str, Any] | None:
    """Retrieve the latest research output for a claim from evaluation evidence."""
    bundles = store.list_latest_artifacts(
        "evaluation_evidence_bundles",
        project_id=project_id,
        claim_id=claim_id,
    )
    if bundles:
        latest = bundles[-1]
        return dict(latest.get("payload") or {})

    # Fallback: scan review events for research_output metadata
    events = store.query_review_events(claim_id)
    for event in reversed(events):
        meta = event.get("metadata") or {}
        if "research_output" in meta:
            return dict(meta["research_output"])
    return None


# ---------------------------------------------------------------------------
# PhaseAssembler
# ---------------------------------------------------------------------------


class PhaseAssembler:
    """Assembles phase2 and phase3 per-claim data from OAE workflow outputs."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    # -- phase 2 per-claim --------------------------------------------------

    def assemble_phase2_claim(
        self,
        project_id: str,
        claim_id: str,
    ) -> dict[str, Any]:
        """Assemble phase2 data for a single claim.

        Gathers:
        - dual_formalization: attempt_a, attempt_b, divergence
        - build_results from proof protocol
        - verifier_results
        - audit: trust_frontier, model_health, intent_alignment, blocking_issues
        - profile: current AssuranceProfile snapshot
        - promotion_transitions: list from PromotionStateMachine
        """
        canonical_claim = canonical_artifact_id(claim_id)

        # --- dual formalization ---
        dual_formalization = _extract_dual_formalization(
            self.store, project_id, canonical_claim
        )
        if dual_formalization is None:
            dual_formalization = {
                "attempt_a": None,
                "attempt_b": None,
                "divergence": {
                    "classification": "",
                    "primary_target_match": None,
                    "back_translation_match": None,
                    "code_sha_match": None,
                    "assumptions_only_in_a": [],
                    "assumptions_only_in_b": [],
                },
            }

        # --- build & verifier results ---
        build_results = self._extract_build_results(canonical_claim)
        verifier_results = self._extract_verifier_results(canonical_claim)

        # --- profile ---
        profile: AssuranceProfile | None = None
        profile_data: dict[str, Any] | None = None
        profile_id: str | None = None
        try:
            profile = self.store.load_assurance_profile_for_claim(canonical_claim)
            profile_data = _safe_model_dump(profile)
            profile_id = canonical_artifact_id(profile.profile_id)
        except FileNotFoundError:
            pass

        # --- audit ---
        audit = _extract_audit(profile)

        # --- promotion transitions ---
        promotion_transitions = _extract_promotion_transitions(
            self.store, canonical_claim, profile_id
        )

        return {
            "dual_formalization": dual_formalization,
            "build_results": build_results,
            "verifier_results": verifier_results,
            "audit": audit,
            "profile": profile_data,
            "promotion_transitions": promotion_transitions,
        }

    # -- phase 2 full -------------------------------------------------------

    def assemble_phase2(
        self,
        project_id: str,
        claim_ids: list[str],
    ) -> dict[str, Any]:
        """Assemble full phase2 section with per_claim and phase2_flags."""
        per_claim: dict[str, Any] = {}
        any_executed = False
        all_certification_eligible = True

        for claim_id in claim_ids:
            canonical_claim = canonical_artifact_id(claim_id)
            claim_data = self.assemble_phase2_claim(project_id, canonical_claim)
            per_claim[canonical_claim] = claim_data

            # Track execution state
            if claim_data["dual_formalization"]["attempt_a"] is not None:
                any_executed = True

            # Certification eligibility: must have a profile with non-draft
            # gate and no blocking issues
            profile = claim_data.get("profile")
            if profile is None:
                all_certification_eligible = False
            else:
                gate = str(profile.get("gate") or "draft")
                blocking = list(
                    claim_data.get("audit", {}).get("blocking_issues") or []
                )
                if gate in ("draft", "blocked", "rejected") or blocking:
                    all_certification_eligible = False

        certification_eligible = any_executed and all_certification_eligible

        # PH2-002: phase2_flags with unavailable_reason when not executed
        phase2_flags: dict[str, Any] = {
            "phase2_executed": any_executed,
            "certification_eligible": certification_eligible,
        }
        if not any_executed:
            phase2_flags["unavailable_reason"] = "runtime_not_captured"

        return {
            "per_claim": per_claim,
            "phase2_flags": phase2_flags,
        }

    # -- phase 3 per-claim --------------------------------------------------

    def assemble_phase3_claim(
        self,
        project_id: str,
        claim_id: str,
    ) -> dict[str, Any]:
        """Assemble phase3 data for a single claim.

        Gathers:
        - research_output from evidence connectors
        - updated_profile after evidence recompute
        """
        canonical_claim = canonical_artifact_id(claim_id)

        research_output = _extract_research_output(
            self.store, project_id, canonical_claim
        )

        # The "updated_profile" is the latest profile revision for the claim,
        # which may have been recomputed after phase3 evidence was applied.
        updated_profile: dict[str, Any] | None = None
        try:
            profile = self.store.load_assurance_profile_for_claim(canonical_claim)
            updated_profile = _safe_model_dump(profile)
        except FileNotFoundError:
            pass

        return {
            "research_output": research_output,
            "updated_profile": updated_profile,
        }

    # -- phase 3 full -------------------------------------------------------

    def assemble_phase3(
        self,
        project_id: str,
        claim_ids: list[str],
    ) -> dict[str, Any]:
        """Assemble full phase3 section with phase3_flags (PH2-002)."""
        evidence: dict[str, Any] = {}
        any_executed = False
        for claim_id in claim_ids:
            canonical_claim = canonical_artifact_id(claim_id)
            claim_evidence = self.assemble_phase3_claim(
                project_id, canonical_claim
            )
            evidence[canonical_claim] = claim_evidence
            if claim_evidence.get("research_output") is not None:
                any_executed = True

        # PH2-002: phase3_flags with unavailable_reason
        phase3_flags: dict[str, Any] = {
            "phase3_executed": any_executed,
        }
        if not any_executed:
            phase3_flags["unavailable_reason"] = "runtime_not_captured"

        return {
            "evidence": evidence,
            "phase3_flags": phase3_flags,
        }

    # -- internal helpers ---------------------------------------------------

    def _extract_build_results(self, claim_id: str) -> dict[str, Any]:
        """Pull build results from review events for the claim."""
        events = self.store.query_review_events(claim_id)
        for event in reversed(events):
            meta = event.get("metadata") or {}
            if "build_results" in meta:
                return dict(meta["build_results"])
            if event.get("event_type") == "audit_workflow":
                audit_meta = meta
                if "build_results" in audit_meta:
                    return dict(audit_meta["build_results"])
        return {}

    def _extract_verifier_results(self, claim_id: str) -> dict[str, Any]:
        """Pull verifier results from review events for the claim."""
        events = self.store.query_review_events(claim_id)
        for event in reversed(events):
            meta = event.get("metadata") or {}
            if "verifier_results" in meta:
                return dict(meta["verifier_results"])
            if event.get("event_type") == "audit_workflow":
                audit_meta = meta
                if "verifier_results" in audit_meta:
                    return dict(audit_meta["verifier_results"])
        return {}
