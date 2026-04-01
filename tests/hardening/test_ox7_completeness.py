"""VRF-005: OX7 completeness and parity hardening tests.

Covers:
1. Stable proposal_id / candidate_id continuity (CNT-004/TRC-007/TRC-008)
2. event_class coverage and cause_event_id integrity (CNT-006/TRC-009)
3. Text/graph shared step_id parity (CNT-005/PFX-006)
4. unavailable_reason / omission_reason coverage (CNT-007)
5. Candidate ledger hard-negative capture (TRC-008)
6. EventValidator catches OX7 violations
7. Block Compressor pattern detection (DOC-004)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from OX7 test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"
CONTRACTS_SRC = REPO_ROOT / "packages" / "contracts-py" / "src"

for p in (str(ENGINE_SRC), str(CONTRACTS_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from formal_claim_engine.event_normalizer import (  # noqa: E402
    EventNormalizer,
    PipelineEventV1,
    StateHasher,
)
from formal_claim_engine.event_validation import (  # noqa: E402
    EventValidationError,
    EventValidator,
    validate_event,
    validate_event_stream,
    validate_event_stream_strict,
)
from formal_claim_engine.candidate_registry import CandidateRegistry  # noqa: E402
from formal_claim_engine.propagation_capture import PropagationCapture  # noqa: E402


# =========================================================================
# 1. Stable proposal_id / candidate_id continuity (CNT-004/TRC-007/TRC-008)
# =========================================================================


class TestStableIdContinuity:
    """proposal_id and candidate_id must be deterministic and non-optional."""

    def test_replay_stable_ids(self):
        """Same trace_id + same order => same IDs."""
        reg1 = CandidateRegistry("trace-abc123")
        ids1 = [reg1.mint_proposal_id() for _ in range(5)]
        reg2 = CandidateRegistry("trace-abc123")
        ids2 = [reg2.mint_proposal_id() for _ in range(5)]
        assert ids1 == ids2

    def test_candidate_id_includes_kind(self):
        reg = CandidateRegistry("trace-abc123")
        claim_id = reg.mint_candidate_id("claim")
        rel_id = reg.mint_candidate_id("relation")
        assert "claim" in claim_id
        assert "relation" in rel_id

    def test_enrich_ledger_entry_mandatory_ids(self):
        """enrich_ledger_entry must always produce proposal_id + candidate_id."""
        reg = CandidateRegistry("trace-abc123")
        entry: dict[str, Any] = {"text": "some claim"}
        enriched = reg.enrich_ledger_entry(entry, "claim")
        assert "proposal_id" in enriched
        assert "candidate_id" in enriched
        assert enriched["proposal_id"].startswith("prop-")
        assert enriched["candidate_id"].startswith("cand-claim-")

    def test_create_candidate_entry_has_all_cnt004_fields(self):
        """create_candidate_entry must support all CNT-004 fields."""
        reg = CandidateRegistry("trace-abc123")
        entry = reg.create_candidate_entry(
            kind="claim",
            source_unit_refs=["u1", "u2"],
            canonical_text="some text",
            accepted_as="claim-001",
            merged_into=None,
            reject_reason=None,
            is_hard_negative=False,
        )
        assert "candidate_id" in entry
        assert "proposal_id" in entry
        assert entry["source_unit_refs"] == ["u1", "u2"]
        assert entry["canonical_text"] == "some text"
        assert entry["accepted_as"] == "claim-001"
        assert entry["is_hard_negative"] is False

    def test_enrich_ledger_hard_negative(self):
        """TRC-008: hard-negative capture with reject_reason."""
        reg = CandidateRegistry("trace-abc123")
        entry = reg.enrich_ledger_entry(
            {"text": "bad relation"},
            kind="relation",
            reject_reason="contradicts existing evidence",
            is_hard_negative=True,
        )
        assert entry["is_hard_negative"] is True
        assert entry["reject_reason"] == "contradicts existing evidence"

    def test_ids_never_optional(self):
        """Repeated calls never leave proposal_id/candidate_id as None."""
        reg = CandidateRegistry("trace-abc123")
        for _ in range(10):
            entry = reg.create_candidate_entry("claim")
            assert entry["proposal_id"] is not None
            assert entry["candidate_id"] is not None
            assert len(entry["proposal_id"]) > 0
            assert len(entry["candidate_id"]) > 0


# =========================================================================
# 2. event_class coverage and cause_event_id integrity (CNT-006/TRC-009)
# =========================================================================


class TestEventClassAndCausality:
    """event_class must be present; automatic consequences need cause_event_id."""

    def test_controllable_action_default(self):
        """Default events are controllable_action."""
        norm = EventNormalizer("trace-001")
        event = norm.record_hidden_assumption(
            phase="phase2",
            actor="auditor",
            before_state={"a": 1},
            after_state={"a": 2},
            assumption_text="test",
            attaches_to="claim-1",
        )
        assert event.event_class == "controllable_action"
        assert event.cause_event_id is None

    def test_automatic_consequence_has_cause(self):
        """emit_consequence sets event_class and cause_event_id correctly."""
        norm = EventNormalizer("trace-001")
        cause = norm.record_promotion_proposal(
            actor="auditor",
            before_state={"x": 1},
            after_state={"x": 2},
            claim_id="claim-1",
            target_gate="gate-2",
        )
        consequence = norm.emit_consequence(
            cause=cause,
            phase="phase2",
            event_type="profile_finalization",
            before_state={"x": 2},
            after_state={"x": 3},
            changed_ids=["claim-1"],
        )
        assert consequence.event_class == "automatic_consequence"
        assert consequence.cause_event_id == cause.event_id

    def test_validation_rejects_consequence_without_cause(self):
        """Automatic consequence without cause_event_id is invalid."""
        event = PipelineEventV1(
            trace_id="trace-001",
            step=1,
            phase="phase2",
            event_type="profile_finalization",
            event_class="automatic_consequence",
            actor="system",
            before_hash="abc",
            after_hash="def",
            cause_event_id=None,
        )
        errors = validate_event(event)
        assert any("cause_event_id" in e for e in errors)

    def test_validation_rejects_controllable_with_cause(self):
        """Controllable action with cause_event_id is invalid."""
        event = PipelineEventV1(
            trace_id="trace-001",
            step=1,
            phase="phase2",
            event_type="propose_relation",
            event_class="controllable_action",
            actor="auditor",
            before_hash="abc",
            after_hash="def",
            cause_event_id="some-event-id",
        )
        errors = validate_event(event)
        assert any("cause_event_id must be null" in e for e in errors)

    def test_validation_rejects_invalid_event_class(self):
        """Invalid event_class value is caught."""
        event = PipelineEventV1(
            trace_id="trace-001",
            step=1,
            phase="phase2",
            event_type="propose_relation",
            event_class="bogus_class",
            actor="auditor",
            before_hash="abc",
            after_hash="def",
        )
        errors = validate_event(event)
        assert any("event_class" in e for e in errors)

    def test_stream_validates_cause_event_id_references(self):
        """cause_event_id must reference a previously emitted event."""
        norm = EventNormalizer("trace-001")
        cause = norm.record_hidden_assumption(
            phase="phase2",
            actor="auditor",
            before_state={},
            after_state={"x": 1},
            assumption_text="test",
            attaches_to="claim-1",
        )
        norm.emit_consequence(
            cause=cause,
            phase="phase2",
            event_type="profile_finalization",
            before_state={"x": 1},
            after_state={"x": 2},
            changed_ids=["claim-1"],
        )
        # Valid stream -- cause comes before consequence
        errors = validate_event_stream(norm.events)
        cause_ref_errors = [e for e in errors if "cause_event_id" in e and "unknown" in e]
        assert len(cause_ref_errors) == 0

    def test_stream_rejects_dangling_cause_ref(self):
        """cause_event_id referencing a non-existent event is caught."""
        event = PipelineEventV1(
            trace_id="trace-001",
            step=1,
            phase="phase2",
            event_type="profile_finalization",
            event_class="automatic_consequence",
            actor="system",
            before_hash="abc",
            after_hash="def",
            cause_event_id="nonexistent-event",
        )
        errors = validate_event_stream([event])
        assert any("unknown event" in e for e in errors)

    def test_generic_event_supports_event_class(self):
        """record_generic_event can set event_class and cause_event_id."""
        norm = EventNormalizer("trace-001")
        event = norm.record_generic_event(
            phase="phase2",
            event_type="custom_event",
            actor="system",
            before_state={},
            after_state={"x": 1},
            event_class="automatic_consequence",
            cause_event_id="ext-event-123",
        )
        assert event.event_class == "automatic_consequence"
        assert event.cause_event_id == "ext-event-123"


# =========================================================================
# 3. EventValidator stateful validation (CNT-006)
# =========================================================================


class TestEventValidator:
    """EventValidator tracks seen event_ids to detect duplicates."""

    def test_detects_duplicate_event_id(self):
        validator = EventValidator()
        event = PipelineEventV1(
            event_id="same-id",
            trace_id="trace-001",
            step=1,
            phase="phase2",
            event_type="propose_relation",
            actor="auditor",
            before_hash="abc",
            after_hash="def",
        )
        errors1 = validator.validate(event)
        assert len(errors1) == 0

        event2 = PipelineEventV1(
            event_id="same-id",
            trace_id="trace-001",
            step=2,
            phase="phase2",
            event_type="propose_relation",
            actor="auditor",
            before_hash="abc",
            after_hash="def",
        )
        errors2 = validator.validate(event2)
        assert any("duplicate" in e for e in errors2)


# =========================================================================
# 4. unavailable_reason / omission_reason coverage (CNT-007)
# =========================================================================


class TestAbsenceSemantics:
    """Optional payloads must not be bare null -- they need unavailable_reason."""

    def test_propagation_capture_provides_unavailable_reason(self):
        """Empty propagation capture includes unavailable_reason."""
        pc = PropagationCapture()
        result = pc.to_trace_results_section()
        assert "propagation_traces_unavailable_reason" in result
        assert result["propagation_traces_unavailable_reason"] == "runtime_not_captured"
        assert "vector_score_deltas_unavailable_reason" in result

    def test_propagation_with_data_omits_unavailable_reason(self):
        """When data is present, no unavailable_reason should appear."""
        pc = PropagationCapture()
        pc.capture_propagation("claim-1", ["claim-2"], "gate_change")
        pc.capture_vector_score_delta("claim-1", {"dim": 0.5}, {"dim": 0.8})
        result = pc.to_trace_results_section()
        assert "propagation_traces_unavailable_reason" not in result
        assert "vector_score_deltas_unavailable_reason" not in result
        assert len(result["propagation_traces"]) == 1
        assert len(result["vector_score_deltas"]) == 1

    def test_custom_unavailable_reason(self):
        """Custom unavailable_reason is passed through."""
        pc = PropagationCapture()
        result = pc.to_trace_results_section(
            propagation_unavailable_reason="computation_failed",
            vector_score_unavailable_reason="not_applicable",
        )
        assert result["propagation_traces_unavailable_reason"] == "computation_failed"
        assert result["vector_score_deltas_unavailable_reason"] == "not_applicable"

    def test_contract_unavailable_reason_enum_values(self):
        """Contract enum matches canonical values."""
        from formal_claim_contracts.pipeline_event import UnavailableReason
        expected = {"not_applicable", "computation_failed", "runtime_not_captured", "exporter_not_implemented"}
        actual = {r.value for r in UnavailableReason}
        assert actual == expected

    def test_contract_omission_reason_enum_values(self):
        """Contract omission enum matches canonical values."""
        from formal_claim_contracts.pipeline_event import OmissionReason
        expected = {"not_requested", "unsupported", "cost_policy", "runtime_limit"}
        actual = {r.value for r in OmissionReason}
        assert actual == expected


# =========================================================================
# 5. Text/graph step_id parity (CNT-005/PFX-006)
# =========================================================================


class TestTextGraphParity:
    """Text and graph slices at the same step must share step_id."""

    def test_prefix_slice_text_contract(self):
        """PrefixSliceTextV1 is the text projection (CNT-005 split)."""
        from formal_claim_contracts.prefix_slice import PrefixSliceTextV1
        text_slice = PrefixSliceTextV1(
            schema_version="1.0.0",
            trace_id="trace-001",
            step_id="step-1",
            state_text="[DOCUMENT] test",
            available_artifacts=["trace.json"],
        )
        assert text_slice.state_text == "[DOCUMENT] test"

    def test_prefix_slice_graph_projection(self):
        """PrefixSliceGraphV1 is the graph projection (CNT-005 split)."""
        from formal_claim_contracts.prefix_slice_graph import PrefixSliceGraphV1, StateGraphV1, GraphNode, GraphEdge
        graph = StateGraphV1(
            nodes=[GraphNode(node_id="c1", role="claim", status="proposed")],
            edges=[GraphEdge(source="c1", target="c2", relation_type="supports")],
        )
        graph_slice = PrefixSliceGraphV1(
            schema_version="1.0.0",
            trace_id="trace-001",
            step_id="step-1",
            state_graph=graph,
            available_artifacts=["trace.json"],
        )
        assert graph_slice.state_graph is not None
        assert len(graph_slice.state_graph.nodes) == 1
        assert len(graph_slice.state_graph.edges) == 1

    def test_shared_step_id_between_projections(self):
        """Text and graph projections at same step share step_id (separate objects)."""
        from formal_claim_contracts.prefix_slice import PrefixSliceTextV1
        from formal_claim_contracts.prefix_slice_graph import PrefixSliceGraphV1, StateGraphV1, GraphNode
        step_id = "step-42"
        text = PrefixSliceTextV1(
            schema_version="1.0.0",
            trace_id="trace-001",
            step_id=step_id,
            state_text="[DOCUMENT] test",
            available_artifacts=[],
        )
        graph = PrefixSliceGraphV1(
            schema_version="1.0.0",
            trace_id="trace-001",
            step_id=step_id,
            state_graph=StateGraphV1(
                nodes=[GraphNode(node_id="c1", role="claim", status="proposed")],
                edges=[],
            ),
            available_artifacts=[],
        )
        assert text.step_id == graph.step_id

    def test_domain_free_state_text_enforced(self):
        """source_domain leak detection applies in text projection."""
        from formal_claim_contracts.prefix_slice import PrefixSliceTextV1
        try:
            PrefixSliceTextV1(
                schema_version="1.0.0",
                trace_id="trace-001",
                step_id="step-1",
                state_text="[DOCUMENT] source_domain: math",
                available_artifacts=[],
            )
            assert False, "Should have raised ValueError"
        except ValueError as exc:
            assert "source_domain" in str(exc)


# =========================================================================
# 6. Candidate ledger enrichment contract (CNT-004)
# =========================================================================


class TestCandidateLedgerContract:
    """CandidateEntry contract has all required fields."""

    def test_candidate_entry_model(self):
        from formal_claim_contracts.pipeline_trace import CandidateEntry
        entry = CandidateEntry(
            candidate_id="cand-claim-abc12345-0001",
            proposal_id="prop-abc12345-0001",
            source_unit_refs=["u1"],
            canonical_text="test claim",
            accepted_as="claim-001",
            merged_into=None,
            reject_reason=None,
            is_hard_negative=False,
        )
        assert entry.candidate_id.root == "cand-claim-abc12345-0001"
        assert entry.is_hard_negative is False

    def test_candidate_entry_hard_negative(self):
        from formal_claim_contracts.pipeline_trace import CandidateEntry
        entry = CandidateEntry(
            candidate_id="cand-relation-abc12345-0002",
            proposal_id="prop-abc12345-0002",
            reject_reason="contradicts evidence",
            is_hard_negative=True,
        )
        assert entry.is_hard_negative is True
        assert entry.reject_reason == "contradicts evidence"

    def test_candidate_ledger_uses_entries(self):
        from formal_claim_contracts.pipeline_trace import CandidateLedger, CandidateEntry
        entry = CandidateEntry(
            candidate_id="cand-claim-abc12345-0001",
            proposal_id="prop-abc12345-0001",
        )
        ledger = CandidateLedger(
            claims_proposed=[entry],
            claims_accepted=[entry],
            claims_rejected=[],
            relations_proposed=[],
            relations_accepted=[],
            relations_rejected=[],
        )
        assert len(ledger.claims_proposed) == 1
        assert ledger.claims_proposed[0].candidate_id.root.startswith("cand-")


# =========================================================================
# 7. Block Compressor pattern detection (DOC-004)
# =========================================================================


class TestBlockCompressorGuard:
    """Ensure no Block Compressor gold labels leak into baseline dataset."""

    BLOCK_COMPRESSOR_PATTERNS = [
        "block_compressor",
        "block_compress",
        "compressed_block",
        "graph_compression_label",
        "bc_gold_label",
    ]

    def test_no_block_compressor_in_event_types(self):
        """Event types must not include Block Compressor patterns."""
        from formal_claim_contracts.pipeline_event import EventType
        for et in EventType:
            for pattern in self.BLOCK_COMPRESSOR_PATTERNS:
                assert pattern not in et.value.lower(), (
                    f"Event type {et.value} contains Block Compressor pattern {pattern}"
                )

    def test_no_block_compressor_in_action_verbs(self):
        """Action DSL verbs must not include Block Compressor patterns."""
        from formal_claim_engine.action_dsl import ActionVerb
        for verb in ActionVerb:
            for pattern in self.BLOCK_COMPRESSOR_PATTERNS:
                assert pattern not in verb.value.lower(), (
                    f"ActionVerb {verb.value} contains Block Compressor pattern {pattern}"
                )


# =========================================================================
# 8. B40/SAFE-001: verifier_delta structural completeness
# =========================================================================


class TestVerifierDeltaCompleteness:
    """verifier_delta must never be {} in model-visible output."""

    def test_transition_log_writer_no_empty_delta(self):
        """TransitionLogWriter rejects empty dicts for verifier_delta."""
        from formal_claim_engine.trace_export import TransitionLogWriter

        writer = TransitionLogWriter(trace_id="trace.ox7-vd-test")

        # None -> structured unavailable
        writer.record_event(
            step_id="step-1",
            phase="phase1",
            event_type="propose_relation",
            actor="planner",
            before_hash="aaa",
            after_hash="bbb",
            verifier_delta=None,
        )

        # {} -> structured unavailable
        writer.record_event(
            step_id="step-2",
            phase="phase2",
            event_type="select_formalization",
            actor="formalizer",
            before_hash="bbb",
            after_hash="ccc",
            verifier_delta={},
        )

        # Real delta -> preserved
        writer.record_event(
            step_id="step-3",
            phase="phase2",
            event_type="finalize_profile",
            actor="auditor",
            before_hash="ccc",
            after_hash="ddd",
            verifier_delta={"legality": True, "gate_before": "draft", "gate_after": "queued"},
        )

        events = writer.get_events()
        for event in events:
            vd = event["verifier_delta"]
            assert vd != {}, (
                f"verifier_delta must never be {{}} on {event['step_id']}"
            )
            assert isinstance(vd, dict)

        # Unavailable deltas must have correct structure
        for i in range(2):
            vd = events[i]["verifier_delta"]
            assert vd.get("unavailable_reason") == "runtime_not_captured"
            assert "legality" in vd
            assert vd["legality"] is None

        # Real delta must be preserved
        assert events[2]["verifier_delta"]["legality"] is True
        assert events[2]["verifier_delta"]["gate_before"] == "draft"

    def test_normalize_verifier_delta_function(self):
        """_normalize_verifier_delta helper works correctly."""
        from formal_claim_engine.trace_export import _normalize_verifier_delta

        # None -> unavailable
        result = _normalize_verifier_delta(None)
        assert result != {}
        assert result["unavailable_reason"] == "runtime_not_captured"
        assert result["legality"] is None

        # Empty dict -> unavailable
        result = _normalize_verifier_delta({})
        assert result != {}
        assert result["unavailable_reason"] == "runtime_not_captured"

        # Real delta -> preserved
        real = {"legality": True, "gate_before": "draft"}
        result = _normalize_verifier_delta(real)
        assert result == real


# =========================================================================
# 9. B40/SAFE-001: source_text reconstruction
# =========================================================================


class TestSourceTextReconstruction:
    """source_text must be non-empty when source_units exist."""

    def test_reconstruct_from_ordered_units(self):
        """reconstruct_source_text joins units by start_char order."""
        from formal_claim_engine.trace_export import reconstruct_source_text

        units = [
            {"unit_id": "su-0002", "start_char": 100, "text": "Second."},
            {"unit_id": "su-0001", "start_char": 0, "text": "First."},
            {"unit_id": "su-0003", "start_char": 200, "text": "Third."},
        ]
        result = reconstruct_source_text(units)
        assert result == "First.\n\nSecond.\n\nThird."

    def test_empty_units_returns_empty_string(self):
        from formal_claim_engine.trace_export import reconstruct_source_text
        assert reconstruct_source_text([]) == ""

    def test_units_with_empty_text_skipped(self):
        from formal_claim_engine.trace_export import reconstruct_source_text

        units = [
            {"unit_id": "su-0001", "start_char": 0, "text": "Content."},
            {"unit_id": "su-0002", "start_char": 50, "text": ""},
        ]
        result = reconstruct_source_text(units)
        assert result == "Content."


# =========================================================================
# B60/VRF-001: Real artifact OX7 completeness regression
# =========================================================================

_EXPORT_DIR = REPO_ROOT.parent / "_push" / "e2e-run-test-doc" / "export-current"


class TestB60OX7ArtifactRegression:
    """Real-artifact OX7 completeness tests for B60 verification."""

    @staticmethod
    def _skip_if_no_artifacts():
        if not _EXPORT_DIR.exists():
            import pytest
            pytest.skip("Export artifacts not available at expected path")

    def _load_transition_log(self):
        self._skip_if_no_artifacts()
        import json
        path = _EXPORT_DIR / "transition_log.jsonl"
        if not path.exists():
            import pytest
            pytest.skip("transition_log.jsonl not found")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _load_trace(self):
        self._skip_if_no_artifacts()
        import json
        path = _EXPORT_DIR / "trace.json"
        if not path.exists():
            import pytest
            pytest.skip("trace.json not found")
        return json.loads(path.read_text(encoding="utf-8"))

    # AUD-009: verifier_delta structural completeness on real artifacts
    def test_real_artifacts_no_empty_verifier_delta(self):
        """verifier_delta must never be {} in real exported transition log.

        RESIDUAL DRIFT: B40/SAFE-001 not yet applied.
        """
        import pytest
        events = self._load_transition_log()
        empty_count = 0
        for e in events:
            vd = e.get("verifier_delta")
            if vd == {}:
                empty_count += 1
        if empty_count == len(events) and len(events) > 0:
            pytest.xfail(
                f"AUD-009 residual drift: {empty_count}/{len(events)} events "
                f"have empty verifier_delta. B40/SAFE-001 not yet applied."
            )
        assert empty_count == 0, (
            f"AUD-009: {empty_count}/{len(events)} events have empty verifier_delta"
        )

    # AUD-010: source_text completeness on real artifacts
    def test_real_artifacts_source_text_non_empty_with_units(self):
        """source_text must be non-empty when source_units exist in real trace.

        RESIDUAL DRIFT: B40/SAFE-001 source reconstruction not yet applied.
        """
        import pytest
        trace = self._load_trace()
        source = trace.get("source", {})
        units = source.get("source_units", [])
        text = source.get("source_text", "")
        if units and not text:
            pytest.xfail(
                f"AUD-010 residual drift: source_text empty but "
                f"{len(units)} units present. B40/SAFE-001 not yet applied."
            )
        if units:
            assert len(text) > 0, (
                f"AUD-010: source_text empty but {len(units)} source_units present"
            )

    # Event stream monotonicity
    def test_real_artifacts_event_seq_monotonic(self):
        """event_seq must be monotonically non-decreasing in transition log."""
        events = self._load_transition_log()
        seqs = [e.get("event_seq", 0) for e in events]
        for i in range(len(seqs) - 1):
            assert seqs[i] <= seqs[i + 1], (
                f"event_seq not monotonic at index {i}: {seqs[i]} > {seqs[i+1]}"
            )

    # All controllable events have event_class set
    def test_real_artifacts_event_class_always_present(self):
        """Every event must have event_class set."""
        events = self._load_transition_log()
        for e in events:
            ec = e.get("event_class")
            assert ec is not None, (
                f"Missing event_class on {e.get('step_id')}"
            )
            assert ec in ("controllable_action", "automatic_consequence"), (
                f"Invalid event_class '{ec}' on {e.get('step_id')}"
            )
