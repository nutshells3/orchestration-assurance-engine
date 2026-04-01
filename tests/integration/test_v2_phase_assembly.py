"""Integration tests for bundle-05 v2 phase assembly (PH2-001, PH2-002, PH3-001, TRC-001).

Validates that build_v2() produces pipeline-trace-v2.schema.json compliant
phase2, phase3, and trace_results sections with proper unavailable_reason
semantics, structured per-claim records, and ModelSafeSerializer redaction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.trace_export import (  # noqa: E402
    PIPELINE_TRACE_V2_SCHEMA_VERSION,
    TraceExportBuilder,
    _normalize_dual_formalization,
    _normalize_promotion_transition,
    _normalize_propagation_trace,
    _normalize_vector_score_delta,
)
from formal_claim_engine.model_safe_serializer import ModelSafeSerializer  # noqa: E402
from formal_claim_engine.propagation_capture import PropagationCapture  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_engine_state(**overrides: Any) -> dict[str, Any]:
    """Build a minimal engine_state with sensible defaults."""
    state: dict[str, Any] = {
        "source_text": "All swans are white. No counterexamples exist.",
        "claim_graph": {
            "claims": [
                {
                    "claim_id": "claim.001",
                    "type": "claim",
                    "text": "All swans are white.",
                    "status": "accepted",
                }
            ],
            "relations": [],
        },
        "per_claim_results": {
            "claim.001": {
                "dual_formalization": {
                    "attempt_a": {
                        "output": "theory Swan begin end",
                        "sha256": "abc123",
                        "assumptions": ["finite_population"],
                        "back_translation": "All swans are white.",
                    },
                    "attempt_b": {
                        "output": "theory Swan_B begin end",
                        "sha256": "def456",
                        "assumptions": ["finite_population", "northern_hemisphere"],
                        "back_translation": "Swans are white in the north.",
                    },
                    "divergence": {
                        "classification": "minor",
                        "primary_target_match": True,
                        "back_translation_match": False,
                        "code_sha_match": False,
                        "assumptions_only_in_a": [],
                        "assumptions_only_in_b": ["northern_hemisphere"],
                    },
                },
                "build_results": {
                    "A": {"success": True, "sorry_count": 0},
                },
                "verifier_results": {
                    "A": {"proof_status": "verified", "sorry_free": True},
                },
                "audit": {
                    "trust_frontier": {"level": "high"},
                    "model_health": {"status": "healthy"},
                    "intent_alignment": {"score": 0.95},
                    "blocking_issues": [],
                },
                "profile": {
                    "gate": "certified",
                    "profile_id": "prof.001",
                },
                "promotion_transitions": [
                    {
                        "event_id": "evt.001",
                        "from_gate": "draft",
                        "to_gate": "queued",
                        "actor": "reviewer",
                        "actor_role": "reviewer",
                        "override": False,
                        "rationale": "Ready for queue.",
                        "created_at": "2025-01-01T00:00:00Z",
                    },
                ],
            },
        },
        "oae_commit": "abc123",
        "audit_rules_version": "1.0.0",
        "promotion_fsm_version": "1.0.0",
        "verifier_versions": {"isabelle": "2024"},
    }
    state.update(overrides)
    return state


def _make_evidence_state() -> dict[str, Any]:
    """Add evidence data to engine state."""
    return {
        "evidence": {
            "evidence": {
                "claim.001": {
                    "research_output": {
                        "findings": "Confirmed by experiment.",
                        "confidence": 0.99,
                    },
                    "updated_profile": {
                        "gate": "certified",
                        "profile_id": "prof.001.v2",
                    },
                },
            },
        },
    }


def _make_trace_results_state() -> dict[str, Any]:
    """Add trace_results data to engine state."""
    return {
        "forward_traces": {"claim.001": {"path": ["claim.001"]}},
        "backward_traces": {"claim.001": {"path": ["claim.001"]}},
        "gap_analysis": {"coverage_gaps": ["missing edge evidence"]},
        "soundness": {
            "score": 0.85,
            "method": "vector_aggregate",
            "notes": ["Minor assumption gap."],
        },
    }


# ---------------------------------------------------------------------------
# PH2-001: per-claim phase2 record
# ---------------------------------------------------------------------------


def test_build_phase2_v2_per_claim_structure() -> None:
    """PH2-001: build_phase2_v2 produces per-claim records with all required fields."""
    state = _make_engine_state()
    builder = TraceExportBuilder(run_id="test-ph2", engine_state=state)
    phase2 = builder.build_phase2_v2(state["per_claim_results"])

    assert "per_claim" in phase2
    assert "phase2_flags" in phase2

    record = phase2["per_claim"]["claim.001"]
    required_keys = {
        "dual_formalization",
        "build_results",
        "verifier_results",
        "audit",
        "profile",
        "promotion_transitions",
    }
    assert set(record.keys()) == required_keys, f"Missing keys: {required_keys - set(record.keys())}"


def test_build_phase2_v2_dual_formalization() -> None:
    """PH2-001: dual_formalization has attempt_a, attempt_b, divergence."""
    state = _make_engine_state()
    builder = TraceExportBuilder(run_id="test-ph2-df", engine_state=state)
    phase2 = builder.build_phase2_v2(state["per_claim_results"])

    df = phase2["per_claim"]["claim.001"]["dual_formalization"]
    assert df is not None
    assert df["attempt_a"]["output"] == "theory Swan begin end"
    assert df["attempt_a"]["sha256"] == "abc123"
    assert isinstance(df["attempt_a"]["assumptions"], list)
    assert df["attempt_b"]["output"] == "theory Swan_B begin end"
    assert df["divergence"]["classification"] == "minor"
    assert df["divergence"]["primary_target_match"] is True
    assert df["divergence"]["back_translation_match"] is False
    assert "northern_hemisphere" in df["divergence"]["assumptions_only_in_b"]


def test_build_phase2_v2_audit() -> None:
    """PH2-001: audit section has trust_frontier, model_health, intent_alignment, blocking_issues."""
    state = _make_engine_state()
    builder = TraceExportBuilder(run_id="test-ph2-audit", engine_state=state)
    phase2 = builder.build_phase2_v2(state["per_claim_results"])

    audit = phase2["per_claim"]["claim.001"]["audit"]
    assert audit is not None
    assert "trust_frontier" in audit
    assert "model_health" in audit
    assert "intent_alignment" in audit
    assert "blocking_issues" in audit
    assert isinstance(audit["blocking_issues"], list)


def test_build_phase2_v2_promotion_transitions() -> None:
    """PH2-001: promotion_transitions conforms to promotionTransition shape."""
    state = _make_engine_state()
    builder = TraceExportBuilder(run_id="test-ph2-promo", engine_state=state)
    phase2 = builder.build_phase2_v2(state["per_claim_results"])

    transitions = phase2["per_claim"]["claim.001"]["promotion_transitions"]
    assert len(transitions) == 1
    t = transitions[0]
    assert t["transition_id"] == "evt.001"
    assert t["gate_before"] == "draft"
    assert t["gate_after"] == "queued"
    assert t["actor_role"] == "reviewer"
    assert isinstance(t["allowed"], bool)
    assert isinstance(t["override"], bool)
    assert t["rationale"] == "Ready for queue."
    assert t["timestamp"] == "2025-01-01T00:00:00Z"


def test_build_phase2_v2_profile_redacted() -> None:
    """PH2-001: profile is redacted of domain fields."""
    state = _make_engine_state()
    state["per_claim_results"]["claim.001"]["profile"]["source_domain"] = "legal"
    builder = TraceExportBuilder(run_id="test-ph2-redact", engine_state=state)
    phase2 = builder.build_phase2_v2(state["per_claim_results"])

    profile = phase2["per_claim"]["claim.001"]["profile"]
    assert "source_domain" not in profile


def test_build_phase2_v2_no_data() -> None:
    """PH2-001: When claim has no phase2 data, fields are null/empty."""
    builder = TraceExportBuilder(
        run_id="test-ph2-empty",
        engine_state={"per_claim_results": {"claim.empty": {}}},
    )
    phase2 = builder.build_phase2_v2({"claim.empty": {}})

    record = phase2["per_claim"]["claim.empty"]
    assert record["dual_formalization"] is None
    assert record["build_results"] == {}
    assert record["verifier_results"] == {}
    assert record["audit"] is None
    assert record["profile"] is None
    assert record["promotion_transitions"] == []


# ---------------------------------------------------------------------------
# PH2-002: phase2_flags and phase3_flags with unavailable semantics
# ---------------------------------------------------------------------------


def test_phase2_flags_executed() -> None:
    """PH2-002: phase2_flags.phase2_executed=True when formalization present."""
    state = _make_engine_state()
    builder = TraceExportBuilder(run_id="test-flags", engine_state=state)
    phase2 = builder.build_phase2_v2(state["per_claim_results"])

    flags = phase2["phase2_flags"]
    assert flags["phase2_executed"] is True
    assert isinstance(flags["certification_eligible"], bool)
    assert "unavailable_reason" not in flags


def test_phase2_flags_not_executed_unavailable_reason() -> None:
    """PH2-002: unavailable_reason present when phase2 not executed."""
    builder = TraceExportBuilder(
        run_id="test-flags-empty",
        engine_state={"per_claim_results": {"claim.001": {}}},
    )
    phase2 = builder.build_phase2_v2({"claim.001": {}})

    flags = phase2["phase2_flags"]
    assert flags["phase2_executed"] is False
    assert flags["certification_eligible"] is False
    assert flags["unavailable_reason"] == "runtime_not_captured"


def test_phase3_flags_executed() -> None:
    """PH2-002: phase3_flags.phase3_executed=True when evidence present."""
    evidence = _make_evidence_state()
    builder = TraceExportBuilder(run_id="test-ph3-flags", engine_state={})
    phase3 = builder.build_phase3_v2(evidence["evidence"])

    flags = phase3["phase3_flags"]
    assert flags["phase3_executed"] is True
    assert "unavailable_reason" not in flags


def test_phase3_flags_not_executed_unavailable_reason() -> None:
    """PH2-002: phase3_flags.unavailable_reason when phase3 not executed."""
    builder = TraceExportBuilder(run_id="test-ph3-flags-empty", engine_state={})
    phase3 = builder.build_phase3_v2(None)

    flags = phase3["phase3_flags"]
    assert flags["phase3_executed"] is False
    assert flags["unavailable_reason"] == "runtime_not_captured"


# ---------------------------------------------------------------------------
# PH3-001: per-claim evidence map
# ---------------------------------------------------------------------------


def test_build_phase3_v2_evidence_map() -> None:
    """PH3-001: phase3 evidence map has research_output and updated_profile."""
    evidence = _make_evidence_state()
    builder = TraceExportBuilder(run_id="test-ph3", engine_state={})
    phase3 = builder.build_phase3_v2(evidence["evidence"])

    assert "evidence" in phase3
    assert "claim.001" in phase3["evidence"]

    record = phase3["evidence"]["claim.001"]
    assert "research_output" in record
    assert "updated_profile" in record
    assert record["research_output"]["findings"] == "Confirmed by experiment."
    assert record["updated_profile"]["gate"] == "certified"


def test_build_phase3_v2_evidence_redacted() -> None:
    """PH3-001: evidence records are redacted of domain fields."""
    evidence: dict[str, Any] = {
        "evidence": {
            "claim.001": {
                "research_output": {
                    "findings": "Test",
                    "source_domain": "legal",
                },
                "updated_profile": None,
            },
        },
    }
    builder = TraceExportBuilder(run_id="test-ph3-redact", engine_state={})
    phase3 = builder.build_phase3_v2(evidence)

    ro = phase3["evidence"]["claim.001"]["research_output"]
    assert "source_domain" not in ro


def test_build_phase3_v2_empty_evidence() -> None:
    """PH3-001: phase3 evidence is empty dict when no evidence data."""
    builder = TraceExportBuilder(run_id="test-ph3-empty", engine_state={})
    phase3 = builder.build_phase3_v2(None)

    assert phase3["evidence"] == {}


# ---------------------------------------------------------------------------
# TRC-001: trace_results
# ---------------------------------------------------------------------------


def test_build_trace_results_v2_full() -> None:
    """TRC-001: trace_results has all required sections when data present."""
    tr_state = _make_trace_results_state()
    builder = TraceExportBuilder(run_id="test-trc", engine_state={})
    result = builder.build_trace_results_v2(
        forward_traces=tr_state["forward_traces"],
        backward_traces=tr_state["backward_traces"],
        gap_analysis=tr_state["gap_analysis"],
        soundness=tr_state["soundness"],
    )

    assert result["forward_traces"] == {"claim.001": {"path": ["claim.001"]}}
    assert result["backward_traces"] == {"claim.001": {"path": ["claim.001"]}}
    assert "forward_traces_unavailable_reason" not in result
    assert "backward_traces_unavailable_reason" not in result

    assert result["gap_analysis"]["coverage_gaps"] == ["missing edge evidence"]
    assert "gap_analysis_unavailable_reason" not in result

    assert result["soundness"]["score"] == 0.85
    assert result["soundness"]["method"] == "vector_aggregate"
    assert result["soundness"]["notes"] == ["Minor assumption gap."]


def test_build_trace_results_v2_unavailable_reasons() -> None:
    """TRC-001: unavailable_reason emitted for all empty sections."""
    builder = TraceExportBuilder(run_id="test-trc-empty", engine_state={})
    result = builder.build_trace_results_v2()

    assert result["forward_traces"] == {}
    assert result["forward_traces_unavailable_reason"] == "runtime_not_captured"
    assert result["backward_traces"] == {}
    assert result["backward_traces_unavailable_reason"] == "runtime_not_captured"
    assert result["propagation_traces"] == []
    assert result["propagation_traces_unavailable_reason"] == "runtime_not_captured"
    assert result["vector_score_deltas"] == []
    assert result["vector_score_deltas_unavailable_reason"] == "runtime_not_captured"
    assert result["gap_analysis"] == {}
    assert result["gap_analysis_unavailable_reason"] == "runtime_not_captured"
    assert result["soundness"]["score"] == 0.0


def test_build_trace_results_v2_propagation_capture_integration() -> None:
    """TRC-001: PropagationCapture data is merged into trace_results."""
    pc = PropagationCapture()
    pc.capture_propagation("claim.001", ["claim.002"], "gate_change")
    pc.capture_vector_score_delta(
        "claim.001",
        {"trust_base_integrity": 0.5, "intent_alignment": 0.6,
         "evidence_support": 0.7, "coverage": 0.8, "robustness": 0.9},
        {"trust_base_integrity": 0.6, "intent_alignment": 0.7,
         "evidence_support": 0.8, "coverage": 0.9, "robustness": 1.0},
    )

    builder = TraceExportBuilder(run_id="test-trc-pc", engine_state={})
    result = builder.build_trace_results_v2(propagation_capture=pc)

    assert len(result["propagation_traces"]) >= 1
    assert "propagation_traces_unavailable_reason" not in result

    pt = result["propagation_traces"][0]
    assert pt["trigger_claim_id"] == "claim.001"
    assert len(pt["path"]) >= 1

    assert len(result["vector_score_deltas"]) >= 1
    assert "vector_score_deltas_unavailable_reason" not in result

    vsd = result["vector_score_deltas"][0]
    assert vsd["claim_id"] == "claim.001"
    assert vsd["before"]["trust_base_integrity"] == 0.5
    assert vsd["after"]["trust_base_integrity"] == 0.6


def test_build_trace_results_v2_soundness_default() -> None:
    """TRC-001: soundness defaults to {score: 0.0, method: '', notes: []}."""
    builder = TraceExportBuilder(run_id="test-trc-snd", engine_state={})
    result = builder.build_trace_results_v2()

    snd = result["soundness"]
    assert snd["score"] == 0.0
    assert snd["method"] == ""
    assert snd["notes"] == []


# ---------------------------------------------------------------------------
# build_v2() end-to-end
# ---------------------------------------------------------------------------


def test_build_v2_full_pipeline() -> None:
    """build_v2() produces all v2 sections with correct schema_version."""
    state = _make_engine_state(**_make_evidence_state(), **_make_trace_results_state())
    builder = TraceExportBuilder(run_id="test-e2e", engine_state=state)
    trace = builder.build_v2()

    assert trace["schema_version"] == PIPELINE_TRACE_V2_SCHEMA_VERSION
    assert "meta" in trace
    assert "source" in trace
    assert "phase1" in trace
    assert "phase2" in trace
    assert "phase3" in trace
    assert "trace_results" in trace

    # Verify phase2 is v2 shape
    phase2 = trace["phase2"]
    assert "per_claim" in phase2
    assert "phase2_flags" in phase2
    assert phase2["phase2_flags"]["phase2_executed"] is True

    record = phase2["per_claim"]["claim.001"]
    assert "dual_formalization" in record
    assert "build_results" in record
    assert "verifier_results" in record
    assert "audit" in record
    assert "profile" in record
    assert "promotion_transitions" in record

    # Verify phase3 is v2 shape
    phase3 = trace["phase3"]
    assert "evidence" in phase3
    assert "phase3_flags" in phase3

    # Verify trace_results is v2 shape
    tr = trace["trace_results"]
    assert "forward_traces" in tr
    assert "backward_traces" in tr
    assert "gap_analysis" in tr
    assert "soundness" in tr
    assert tr["soundness"]["score"] == 0.85


def test_build_v2_model_safe() -> None:
    """build_v2() output passes ModelSafeSerializer validation after redaction."""
    state = _make_engine_state()
    state["per_claim_results"]["claim.001"]["profile"]["source_domain"] = "legal"
    builder = TraceExportBuilder(run_id="test-redact-e2e", engine_state=state)
    trace = builder.build_v2()

    redacted = ModelSafeSerializer.redact(trace)
    violations = ModelSafeSerializer.validate_model_safe(redacted)
    assert violations == [], f"Domain leak in v2 trace: {violations}"


def test_build_v2_empty_state() -> None:
    """build_v2() with empty engine state produces valid skeleton."""
    builder = TraceExportBuilder(run_id="test-empty-e2e", engine_state={})
    trace = builder.build_v2()

    assert trace["schema_version"] == PIPELINE_TRACE_V2_SCHEMA_VERSION
    assert trace["phase2"]["per_claim"] == {}
    assert trace["phase2"]["phase2_flags"]["phase2_executed"] is False
    assert trace["phase2"]["phase2_flags"]["unavailable_reason"] == "runtime_not_captured"
    assert trace["phase3"]["evidence"] == {}
    assert trace["phase3"]["phase3_flags"]["phase3_executed"] is False
    assert trace["phase3"]["phase3_flags"]["unavailable_reason"] == "runtime_not_captured"
    assert trace["trace_results"]["forward_traces_unavailable_reason"] == "runtime_not_captured"


def test_build_v2_v1_build_untouched() -> None:
    """v1 build() path remains unmodified by bundle-05 changes."""
    state = _make_engine_state()
    builder = TraceExportBuilder(run_id="test-v1", engine_state=state)
    v1 = builder.build()

    # v1 uses different structure -- no phase2_flags at top level of phase2
    assert "meta" in v1
    assert v1["meta"]["schema_version"] == "PipelineTraceV1"

    # v1 phase2 has the old per_claim shape (no promotion_transitions key)
    if "phase2" in v1:
        phase2 = v1["phase2"]
        assert "per_claim" in phase2
        record = phase2["per_claim"]["claim.001"]
        # v1 has "promotion" not "promotion_transitions"
        assert "promotion" in record or "dual_formalization" in record


# ---------------------------------------------------------------------------
# Normalization helper tests
# ---------------------------------------------------------------------------


def test_normalize_dual_formalization_null_attempts() -> None:
    """_normalize_dual_formalization handles None attempts gracefully."""
    result = _normalize_dual_formalization({
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
    })
    assert result["attempt_a"] is None
    assert result["attempt_b"] is None
    assert result["divergence"]["classification"] == ""


def test_normalize_promotion_transition_from_gate() -> None:
    """_normalize_promotion_transition maps from_gate -> gate_before."""
    result = _normalize_promotion_transition({
        "event_id": "evt.001",
        "from_gate": "draft",
        "to_gate": "queued",
        "actor": "reviewer",
        "actor_role": "reviewer",
        "override": False,
        "rationale": "Ready.",
        "created_at": "2025-01-01T00:00:00Z",
    })
    assert result["transition_id"] == "evt.001"
    assert result["gate_before"] == "draft"
    assert result["gate_after"] == "queued"


def test_normalize_propagation_trace_from_capture_format() -> None:
    """_normalize_propagation_trace converts PropagationCapture format."""
    result = _normalize_propagation_trace({
        "source": "claim.001",
        "affected": ["claim.002", "claim.003"],
        "type": "gate_change",
        "timestamp": "2025-01-01T00:00:00Z",
        "depth": 2,
    })
    assert result["trigger_claim_id"] == "claim.001"
    assert len(result["path"]) == 2
    assert result["path"][0]["claim_id"] == "claim.002"
    assert result["path"][0]["reason"] == "gate_change"


def test_normalize_propagation_trace_v2_passthrough() -> None:
    """_normalize_propagation_trace passes through v2-shaped data."""
    input_data = {
        "trigger_claim_id": "claim.001",
        "trigger_event_id": "evt.001",
        "path": [
            {
                "claim_id": "claim.002",
                "status_before": "draft",
                "status_after": "queued",
                "reason": "gate_change",
            },
        ],
    }
    result = _normalize_propagation_trace(input_data)
    assert result["trigger_claim_id"] == "claim.001"
    assert result["trigger_event_id"] == "evt.001"
    assert len(result["path"]) == 1


def test_normalize_vector_score_delta() -> None:
    """_normalize_vector_score_delta produces all five dimension keys."""
    result = _normalize_vector_score_delta({
        "claim_id": "claim.001",
        "before": {"trust_base_integrity": 0.5, "intent_alignment": 0.6},
        "after": {"trust_base_integrity": 0.6, "coverage": 0.8},
    })
    assert result["claim_id"] == "claim.001"
    # All five dims present
    for dim in ("trust_base_integrity", "intent_alignment", "evidence_support",
                "coverage", "robustness"):
        assert dim in result["before"]
        assert dim in result["after"]
    assert result["before"]["trust_base_integrity"] == 0.5
    assert result["after"]["trust_base_integrity"] == 0.6
    # Missing dims default to 0.0
    assert result["before"]["coverage"] == 0.0
    assert result["after"]["intent_alignment"] == 0.0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    # PH2-001
    test_build_phase2_v2_per_claim_structure()
    test_build_phase2_v2_dual_formalization()
    test_build_phase2_v2_audit()
    test_build_phase2_v2_promotion_transitions()
    test_build_phase2_v2_profile_redacted()
    test_build_phase2_v2_no_data()

    # PH2-002
    test_phase2_flags_executed()
    test_phase2_flags_not_executed_unavailable_reason()
    test_phase3_flags_executed()
    test_phase3_flags_not_executed_unavailable_reason()

    # PH3-001
    test_build_phase3_v2_evidence_map()
    test_build_phase3_v2_evidence_redacted()
    test_build_phase3_v2_empty_evidence()

    # TRC-001
    test_build_trace_results_v2_full()
    test_build_trace_results_v2_unavailable_reasons()
    test_build_trace_results_v2_propagation_capture_integration()
    test_build_trace_results_v2_soundness_default()

    # E2E
    test_build_v2_full_pipeline()
    test_build_v2_model_safe()
    test_build_v2_empty_state()
    test_build_v2_v1_build_untouched()

    # Normalization helpers
    test_normalize_dual_formalization_null_attempts()
    test_normalize_promotion_transition_from_gate()
    test_normalize_propagation_trace_from_capture_format()
    test_normalize_propagation_trace_v2_passthrough()
    test_normalize_vector_score_delta()

    print("All v2 phase assembly tests passed (PH2-001, PH2-002, PH3-001, TRC-001).")


if __name__ == "__main__":
    main()
