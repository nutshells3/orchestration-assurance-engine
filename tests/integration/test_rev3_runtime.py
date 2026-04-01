"""REV3 runtime integration tests.

Covers:
- Stable event_id generation and uniqueness
- Causal linking (cause_event_id references valid event_id)
- TRC-007/008: proposal_id / candidate_id on all ledger entries
- TRC-009: controllable vs automatic event classification
- Absence semantics (unavailable_reason on empty fields)
- StateHasher determinism
- TransitionLogWriter event recording
- PhaseAssembler store-based assembly (constructor shape)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from REV3 runtime test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.candidate_registry import CandidateRegistry  # noqa: E402
from formal_claim_engine.event_normalizer import (  # noqa: E402
    AUTOMATIC_EVENTS,
    CONTROLLABLE_EVENTS,
    EventNormalizer,
    PipelineEventV1,
    StateHasher,
)
from formal_claim_engine.event_validation import (  # noqa: E402
    EventValidationError,
    EventValidator,
    VALID_UNAVAILABLE_REASONS,
    validate_event,
    validate_event_stream,
)
from formal_claim_engine.trace_export import (  # noqa: E402
    TraceExportBuilder,
    TransitionLogWriter,
)


# ======================================================================
# EventNormalizer -- stable event_id
# ======================================================================


class TestEventNormalizerStableIds:
    """event_id MUST be present and unique across events in a session."""

    def test_event_id_present(self):
        n = EventNormalizer("trace-abc")
        evt = n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={"x": 1},
            after_state={"x": 2},
        )
        assert evt.event_id  # non-empty

    def test_event_id_monotonic_step(self):
        n = EventNormalizer("t1")
        events = [
            n.record_generic_event(
                phase="ingestion",
                event_type="add_hidden_assumption",
                actor="user",
                before_state={},
                after_state={"i": i},
            )
            for i in range(3)
        ]
        steps = [e.step for e in events]
        assert steps == [1, 2, 3]

    def test_event_id_unique(self):
        n = EventNormalizer("t2")
        seen = set()
        for i in range(100):
            evt = n.record_generic_event(
                phase="ingestion",
                event_type="add_hidden_assumption",
                actor="user",
                before_state={},
                after_state={"i": i},
            )
            assert evt.event_id not in seen, f"Duplicate event_id: {evt.event_id}"
            seen.add(evt.event_id)

    def test_events_list_populated(self):
        """All emitted events appear in the normalizer's events list."""
        n = EventNormalizer("t3")
        n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={"a": 1},
        )
        n.record_relation_proposal(
            phase="ingestion",
            actor="user",
            before_state={},
            after_state={"b": 2},
        )
        n.record_promotion_proposal(
            actor="user",
            before_state={},
            after_state={"c": 3},
            claim_id="c1",
            target_gate="certified",
        )
        assert len(n.events) == 3
        for event in n.events:
            assert event.event_id
            assert event.trace_id == "t3"

    def test_get_events_returns_copy(self):
        n = EventNormalizer("t4")
        n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={},
        )
        copy = n.get_events()
        assert len(copy) == 1
        # Mutating the copy must not affect the original
        copy.clear()
        assert len(n.events) == 1


# ======================================================================
# Causal linking -- cause_event_id
# ======================================================================


class TestCausalLinking:
    """cause_event_id MUST reference a real event_id via emit_consequence."""

    def test_automatic_event_with_cause(self):
        n = EventNormalizer("causal-1")
        cause_evt = n.record_promotion_proposal(
            actor="user",
            before_state={},
            after_state={"promoted": True},
            claim_id="c1",
            target_gate="certified",
        )
        consequence = n.emit_consequence(
            cause=cause_evt,
            phase="recompute",
            event_type="profile_recomputation",
            before_state={},
            after_state={"recomputed": True},
        )
        assert consequence.cause_event_id == cause_evt.event_id
        assert consequence.event_class == "automatic_consequence"

    def test_controllable_event_cause_is_none(self):
        n = EventNormalizer("causal-4")
        evt = n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={},
        )
        assert evt.cause_event_id is None

    def test_causal_chain(self):
        """Build a multi-step causal chain and verify the links."""
        n = EventNormalizer("chain-1")
        e1 = n.record_promotion_proposal(
            actor="user",
            before_state={},
            after_state={"promoted": True},
            claim_id="c1",
            target_gate="certified",
        )
        e2 = n.emit_consequence(
            cause=e1,
            phase="recompute",
            event_type="profile_recomputation",
            before_state={},
            after_state={"recomputed": True},
        )
        e3 = n.emit_consequence(
            cause=e1,
            phase="gate",
            event_type="gate_update",
            before_state={},
            after_state={"gate": "blocked"},
        )

        assert e2.cause_event_id == e1.event_id
        assert e3.cause_event_id == e1.event_id

    def test_dict_access_via_getitem(self):
        """PipelineEventV1 supports dict-like access via __getitem__."""
        n = EventNormalizer("getitem-1")
        evt = n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={},
        )
        assert evt["trace_id"] == "getitem-1"
        assert evt["event_class"] == "controllable_action"
        assert evt["schema"] == "PipelineEventV1"


# ======================================================================
# TRC-007/008: proposal_id / candidate_id
# ======================================================================


class TestCandidateRegistry:
    """proposal_id/candidate_id MUST NOT be optional on ledger entries."""

    def test_mint_proposal_id_format(self):
        r = CandidateRegistry("abcdef1234567890")
        pid = r.mint_proposal_id()
        assert pid == "prop-abcdef12-0001"

    def test_mint_candidate_id_format(self):
        r = CandidateRegistry("abcdef1234567890")
        cid = r.mint_candidate_id("claim")
        assert cid == "cand-claim-abcdef12-0001"

    def test_mint_candidate_id_relation(self):
        r = CandidateRegistry("abcdef1234567890")
        cid = r.mint_candidate_id("relation")
        assert cid == "cand-relation-abcdef12-0001"

    def test_enrich_ledger_entry_adds_both_ids(self):
        r = CandidateRegistry("test1234")
        entry = {"claim_id": "c1", "title": "Claim One"}
        enriched = r.enrich_ledger_entry(entry, kind="claim")
        assert "proposal_id" in enriched
        assert "candidate_id" in enriched
        assert enriched["proposal_id"].startswith("prop-")
        assert enriched["candidate_id"].startswith("cand-claim-")

    def test_enrich_ledger_entry_idempotent(self):
        r = CandidateRegistry("test5678")
        entry = {
            "claim_id": "c1",
            "proposal_id": "existing-prop",
            "candidate_id": "existing-cand",
        }
        enriched = r.enrich_ledger_entry(entry, kind="claim")
        assert enriched["proposal_id"] == "existing-prop"
        assert enriched["candidate_id"] == "existing-cand"

    def test_all_ledger_entries_have_ids(self):
        r = CandidateRegistry("batch-test")
        entries = [{"claim_id": f"c{i}"} for i in range(10)]
        for entry in entries:
            r.enrich_ledger_entry(entry)
        for entry in entries:
            assert "proposal_id" in entry, f"Missing proposal_id in {entry}"
            assert "candidate_id" in entry, f"Missing candidate_id in {entry}"

    def test_counter_is_monotonic(self):
        r = CandidateRegistry("mono-test")
        ids = []
        for _ in range(5):
            ids.append(r.mint_proposal_id())
        for i in range(1, len(ids)):
            # Extract the counter from prop-{hash}-{counter}
            prev_counter = int(ids[i - 1].split("-")[-1])
            curr_counter = int(ids[i].split("-")[-1])
            assert curr_counter > prev_counter

    def test_create_candidate_entry(self):
        r = CandidateRegistry("create-test")
        entry = r.create_candidate_entry(
            kind="claim",
            canonical_text="test claim",
            reject_reason=None,
        )
        assert "candidate_id" in entry
        assert "proposal_id" in entry
        assert entry["canonical_text"] == "test claim"


# ======================================================================
# TRC-009: controllable vs automatic classification
# ======================================================================


class TestEventClassification:
    """Controllable vs automatic classification MUST be correct."""

    def test_controllable_events_default_class(self):
        n = EventNormalizer("class-1")
        evt = n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={},
        )
        assert evt.event_class == "controllable_action"

    def test_automatic_events_via_emit_consequence(self):
        n = EventNormalizer("class-2")
        cause = n.record_promotion_proposal(
            actor="user",
            before_state={},
            after_state={},
            claim_id="c1",
            target_gate="certified",
        )
        consequence = n.emit_consequence(
            cause=cause,
            phase="recompute",
            event_type="profile_recomputation",
            before_state={},
            after_state={},
        )
        assert consequence.event_class == "automatic_consequence"

    def test_all_controllable_kinds_in_set(self):
        """All CONTROLLABLE_EVENTS entries are known controllable types."""
        for kind in CONTROLLABLE_EVENTS:
            assert kind not in AUTOMATIC_EVENTS, (
                f"{kind} should not be in AUTOMATIC_EVENTS"
            )

    def test_all_automatic_kinds_in_set(self):
        """All AUTOMATIC_EVENTS entries are known automatic types."""
        for kind in AUTOMATIC_EVENTS:
            assert kind not in CONTROLLABLE_EVENTS, (
                f"{kind} should not be in CONTROLLABLE_EVENTS"
            )

    def test_classification_sets_no_overlap(self):
        overlap = CONTROLLABLE_EVENTS & AUTOMATIC_EVENTS
        assert overlap == frozenset(), f"Overlap between controllable and automatic: {overlap}"

    def test_event_class_on_generic_event(self):
        n = EventNormalizer("class-5")
        evt = n.record_generic_event(
            phase="gate",
            event_type="gate_update",
            actor="system",
            before_state={},
            after_state={},
            event_class="automatic_consequence",
            cause_event_id="some-cause",
        )
        assert evt.event_class == "automatic_consequence"
        assert evt.cause_event_id == "some-cause"


# ======================================================================
# Absence semantics
# ======================================================================


class TestAbsenceSemantics:
    """VALID_UNAVAILABLE_REASONS captures the canonical absence reasons."""

    def test_valid_reasons_are_frozenset(self):
        assert isinstance(VALID_UNAVAILABLE_REASONS, frozenset)

    def test_known_reasons_present(self):
        assert "not_applicable" in VALID_UNAVAILABLE_REASONS
        assert "computation_failed" in VALID_UNAVAILABLE_REASONS
        assert "runtime_not_captured" in VALID_UNAVAILABLE_REASONS
        assert "exporter_not_implemented" in VALID_UNAVAILABLE_REASONS

    def test_reasons_count(self):
        assert len(VALID_UNAVAILABLE_REASONS) == 4

    def test_invalid_reason_not_in_set(self):
        assert "not_computed" not in VALID_UNAVAILABLE_REASONS
        assert "bogus_reason" not in VALID_UNAVAILABLE_REASONS

    def test_trace_export_builder_requires_run_id(self):
        """TraceExportBuilder requires a run_id argument."""
        b = TraceExportBuilder("test-run")
        assert b.run_id == "test-run"

    def test_trace_export_builder_build_meta(self):
        b = TraceExportBuilder("run-1")
        meta = b.build_meta()
        assert meta["run_id"] == "run-1"
        assert meta["schema_version"] == "PipelineTraceV1"
        assert "trace_id" in meta

    def test_trace_export_builder_build_trace_results(self):
        b = TraceExportBuilder("run-2")
        results = b.build_trace_results(
            forward_traces=[{"step": 1}],
            soundness={"overall": 0.8},
        )
        assert results["forward_traces"] == [{"step": 1}]
        assert results["soundness"] == {"overall": 0.8}
        assert results["backward_traces"] == []

    def test_trace_export_builder_build_empty(self):
        b = TraceExportBuilder("run-3")
        trace = b.build()
        assert "meta" in trace
        assert trace["meta"]["run_id"] == "run-3"

    def test_trace_export_builder_build_with_state(self):
        state = {
            "forward_traces": [{"step": 1}],
            "backward_traces": [],
            "soundness": {"overall": 0.9},
        }
        b = TraceExportBuilder("run-4", engine_state=state)
        trace = b.build()
        assert "trace_results" in trace
        assert trace["trace_results"]["forward_traces"] == [{"step": 1}]
        assert trace["trace_results"]["soundness"] == {"overall": 0.9}


# ======================================================================
# EventValidator integration
# ======================================================================


class TestEventValidator:
    """Full validation pipeline for event streams."""

    def test_valid_stream_passes(self):
        n = EventNormalizer("valid-1")
        cause = n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={"created": True},
        )
        n.emit_consequence(
            cause=cause,
            phase="recompute",
            event_type="profile_recomputation",
            before_state={},
            after_state={"recomputed": True},
        )
        errors = validate_event_stream(n.events)
        assert errors == []

    def test_valid_stream_via_instance(self):
        n = EventNormalizer("valid-2")
        n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={},
        )
        v = EventValidator()
        errors = v.validate_event_stream(n.events)
        assert errors == []

    def test_duplicate_event_id_detected(self):
        evt1 = PipelineEventV1(
            event_id="evt-dup-0001",
            trace_id="t1",
            step=1,
            step_id="step-0001",
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="aaaa",
            after_hash="bbbb",
        )
        evt2 = PipelineEventV1(
            event_id="evt-dup-0001",
            trace_id="t1",
            step=2,
            step_id="step-0002",
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="cccc",
            after_hash="dddd",
        )
        errors = validate_event_stream([evt1, evt2])
        assert any("duplicate" in e for e in errors)

    def test_missing_event_id_detected(self):
        events = [{"event_type": "add_hidden_assumption", "trace_id": "t1",
                    "phase": "x", "actor": "u", "before_hash": "a",
                    "after_hash": "b", "step": 1}]
        # Remove event_id to trigger validation error
        errors = validate_event_stream(events)
        assert any("event_id" in e.lower() for e in errors)

    def test_orphan_cause_event_id_detected(self):
        evt = PipelineEventV1(
            event_id="evt-x-0001",
            trace_id="t1",
            step=1,
            step_id="step-0001",
            phase="recompute",
            event_type="profile_recomputation",
            event_class="automatic_consequence",
            actor="system",
            before_hash="aaaa",
            after_hash="bbbb",
            cause_event_id="evt-nonexistent-9999",
        )
        errors = validate_event_stream([evt])
        assert any("unknown" in e.lower() or "reference" in e.lower() for e in errors)

    def test_automatic_without_cause_detected(self):
        evt = PipelineEventV1(
            event_id="evt-a-0001",
            trace_id="t1",
            step=1,
            step_id="step-0001",
            phase="recompute",
            event_type="profile_recomputation",
            event_class="automatic_consequence",
            actor="system",
            before_hash="aaaa",
            after_hash="bbbb",
            cause_event_id=None,
        )
        errors = validate_event(evt)
        assert any("cause_event_id" in e for e in errors)

    def test_controllable_with_cause_detected(self):
        """controllable_action must NOT have cause_event_id set."""
        evt = PipelineEventV1(
            event_id="evt-m-0001",
            trace_id="t1",
            step=1,
            step_id="step-0001",
            phase="ingestion",
            event_type="add_hidden_assumption",
            event_class="controllable_action",
            actor="user",
            before_hash="aaaa",
            after_hash="bbbb",
            cause_event_id="evt-some-cause",
        )
        errors = validate_event(evt)
        assert any("cause_event_id" in e for e in errors)

    def test_validate_single_event(self):
        v = EventValidator()
        n = EventNormalizer("single-1")
        evt = n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={},
        )
        errors = v.validate(evt)
        assert errors == []

    def test_validate_tracks_seen_ids(self):
        """Instance-level validate() tracks duplicate event_ids."""
        v = EventValidator()
        n = EventNormalizer("dup-track")
        evt = n.record_generic_event(
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_state={},
            after_state={},
        )
        errors1 = v.validate(evt)
        assert errors1 == []
        # Validating the same event again should detect the duplicate
        errors2 = v.validate(evt)
        assert any("duplicate" in e for e in errors2)

    def test_missing_required_fields_detected(self):
        """Dict events missing required fields produce errors."""
        event = {"event_id": "evt-1"}  # missing trace_id, phase, event_type, etc.
        errors = validate_event(event)
        assert len(errors) > 0
        error_text = " ".join(errors)
        assert "trace_id" in error_text

    def test_reject_reason_required_when_not_accepted(self):
        evt = PipelineEventV1(
            event_id="evt-rej-0001",
            trace_id="t1",
            step=1,
            step_id="step-0001",
            phase="promotion",
            event_type="promotion_transition",
            actor="user",
            before_hash="aaaa",
            after_hash="bbbb",
            accepted=False,
            reject_reason=None,
        )
        errors = validate_event(evt)
        assert any("reject_reason" in e for e in errors)


# ======================================================================
# StateHasher
# ======================================================================


class TestStateHasher:
    """StateHasher produces deterministic digests."""

    def test_same_input_same_hash(self):
        state = {"a": 1, "b": [2, 3]}
        h1 = StateHasher.compute_state_hash(state)
        h2 = StateHasher.compute_state_hash(state)
        assert h1 == h2

    def test_different_input_different_hash(self):
        h1 = StateHasher.compute_state_hash({"a": 1})
        h2 = StateHasher.compute_state_hash({"a": 2})
        assert h1 != h2

    def test_key_order_independent(self):
        h1 = StateHasher.compute_state_hash({"b": 2, "a": 1})
        h2 = StateHasher.compute_state_hash({"a": 1, "b": 2})
        assert h1 == h2

    def test_hash_truncated_to_16(self):
        h = StateHasher.compute_state_hash({"x": 42})
        assert len(h) == 16

    def test_compute_alias(self):
        state = {"a": 1}
        h1 = StateHasher.compute_state_hash(state)
        h2 = StateHasher.compute(state)
        assert h1 == h2

    def test_hash_claim_graph(self):
        graph = {"claims": [{"id": "c1"}]}
        h = StateHasher.hash_claim_graph(graph)
        assert len(h) == 16
        assert h == StateHasher.compute_state_hash(graph)

    def test_hash_combined_pipeline(self):
        h = StateHasher.hash_combined_pipeline(a=1, b=2)
        assert len(h) == 16

    def test_normalizer_compute_state_hash_alias(self):
        """EventNormalizer.compute_state_hash is an alias for StateHasher."""
        state = {"test": True}
        h1 = EventNormalizer.compute_state_hash(state)
        h2 = StateHasher.compute_state_hash(state)
        assert h1 == h2


# ======================================================================
# TransitionLogWriter
# ======================================================================


class TestTransitionLogWriter:
    """TransitionLogWriter records events for replay audits."""

    def test_record_event_and_get_events(self):
        w = TransitionLogWriter("test-trace")
        w.record_event(
            step_id="step-0001",
            phase="ingestion",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="aaaa",
            after_hash="bbbb",
        )
        w.record_event(
            step_id="step-0002",
            phase="promotion",
            event_type="promotion_transition",
            actor="user",
            before_hash="cccc",
            after_hash="dddd",
        )
        events = w.get_events()
        assert len(events) == 2
        for event in events:
            assert event["trace_id"] == "test-trace"
            assert "step_id" in event
            assert "event_seq" in event

    def test_event_seq_increments(self):
        w = TransitionLogWriter("seq-trace")
        w.record_event(
            step_id="step-0001",
            phase="p1",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="a",
            after_hash="b",
        )
        w.record_event(
            step_id="step-0002",
            phase="p2",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="c",
            after_hash="d",
        )
        events = w.get_events()
        assert events[0]["event_seq"] == 1
        assert events[1]["event_seq"] == 2

    def test_record_event_with_proposal(self):
        w = TransitionLogWriter("prop-trace")
        w.record_event(
            step_id="step-0001",
            phase="promotion",
            event_type="promotion_transition",
            actor="user",
            before_hash="a",
            after_hash="b",
            proposal={"claim_id": "c1", "target_gate": "certified"},
            accepted=True,
            changed_ids=["c1"],
        )
        events = w.get_events()
        assert len(events) == 1
        assert events[0]["proposal"] == {"claim_id": "c1", "target_gate": "certified"}
        assert events[0]["accepted"] is True
        assert events[0]["changed_ids"] == ["c1"]

    def test_get_events_returns_copy(self):
        w = TransitionLogWriter("copy-trace")
        w.record_event(
            step_id="step-0001",
            phase="p1",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="a",
            after_hash="b",
        )
        copy = w.get_events()
        assert len(copy) == 1
        copy.clear()
        assert len(w.get_events()) == 1

    def test_events_are_json_serializable(self):
        w = TransitionLogWriter("json-trace")
        w.record_event(
            step_id="step-0001",
            phase="p1",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="a",
            after_hash="b",
            verifier_delta={"score": 0.5},
        )
        for event in w.get_events():
            text = json.dumps(event, default=str)
            parsed = json.loads(text)
            assert parsed["trace_id"] == "json-trace"


# ======================================================================
# PipelineEventV1 model
# ======================================================================


class TestPipelineEventV1:
    """PipelineEventV1 model shape and defaults."""

    def test_default_schema(self):
        evt = PipelineEventV1(
            trace_id="t1",
            phase="p1",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="a",
            after_hash="b",
        )
        assert evt.schema == "PipelineEventV1"

    def test_default_event_class(self):
        evt = PipelineEventV1(
            trace_id="t1",
            phase="p1",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="a",
            after_hash="b",
        )
        assert evt.event_class == "controllable_action"

    def test_step_id_field(self):
        evt = PipelineEventV1(
            trace_id="t1",
            step_id="step-0042",
            phase="p1",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="a",
            after_hash="b",
        )
        assert evt.step_id == "step-0042"

    def test_getitem_access(self):
        evt = PipelineEventV1(
            trace_id="t1",
            phase="p1",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="a",
            after_hash="b",
        )
        assert evt["trace_id"] == "t1"
        assert evt["event_class"] == "controllable_action"

    def test_reject_reason_nullable(self):
        evt = PipelineEventV1(
            trace_id="t1",
            phase="p1",
            event_type="add_hidden_assumption",
            actor="user",
            before_hash="a",
            after_hash="b",
            reject_reason=None,
        )
        assert evt.reject_reason is None

    def test_rejected_events_have_empty_changed_ids(self):
        """When accepted=False, _emit produces empty changed_ids."""
        n = EventNormalizer("rej-1")
        evt = n.record_promotion_proposal(
            actor="user",
            before_state={},
            after_state={},
            claim_id="c1",
            target_gate="certified",
            accepted=False,
            reject_reason="insufficient evidence",
        )
        assert evt.changed_ids == []
        assert evt.accepted is False
        assert evt.reject_reason == "insufficient evidence"


# ======================================================================
# Domain-specific record helpers
# ======================================================================


class TestDomainHelpers:
    """Test the domain-specific record_* helper methods on EventNormalizer."""

    def test_record_hidden_assumption(self):
        n = EventNormalizer("domain-1")
        evt = n.record_hidden_assumption(
            phase="ingestion",
            actor="user",
            before_state={},
            after_state={"assumption": "A1"},
            assumption_text="hidden assumption text",
            attaches_to="c1",
        )
        assert evt.event_type == "add_hidden_assumption"
        assert evt.proposal["assumption_text"] == "hidden assumption text"

    def test_record_relation_proposal(self):
        n = EventNormalizer("domain-2")
        evt = n.record_relation_proposal(
            phase="ingestion",
            actor="user",
            before_state={},
            after_state={"rel": True},
            proposal={"source": "c1", "target": "c2", "kind": "supports"},
        )
        assert evt.event_type == "relation_proposal"
        assert evt.proposal["source"] == "c1"

    def test_record_promotion_proposal(self):
        n = EventNormalizer("domain-3")
        evt = n.record_promotion_proposal(
            actor="user",
            before_state={},
            after_state={"promoted": True},
            claim_id="c1",
            target_gate="certified",
        )
        assert evt.event_type == "promotion_transition"
        assert evt.proposal["claim_id"] == "c1"
        assert evt.proposal["target_gate"] == "certified"
        assert evt.changed_ids == ["c1"]

    def test_record_recheck_request(self):
        n = EventNormalizer("domain-4")
        evt = n.record_recheck_request(
            phase="recheck",
            actor="auditor",
            before_state={},
            after_state={"rechecked": True},
            claim_id="c1",
        )
        assert evt.event_type == "recheck_request"
        assert evt.proposal["claim_id"] == "c1"

    def test_record_formalization_selection(self):
        n = EventNormalizer("domain-5")
        evt = n.record_formalization_selection(
            actor="user",
            before_state={},
            after_state={"formalized": True},
            claim_id="c1",
            attempt=1,
        )
        assert evt.event_type == "formalization_selection"
        assert evt.proposal["claim_id"] == "c1"
        assert evt.proposal["attempt"] == 1

    def test_record_profile_finalization(self):
        n = EventNormalizer("domain-6")
        evt = n.record_profile_finalization(
            actor="system",
            before_state={},
            after_state={"finalized": True},
            claim_id="c1",
        )
        assert evt.event_type == "profile_finalization"


# ======================================================================
# pytest runner support
# ======================================================================


def _run_all_tests() -> int:
    """Manual test runner for environments without pytest."""
    failures = 0
    test_classes = [
        TestEventNormalizerStableIds,
        TestCausalLinking,
        TestCandidateRegistry,
        TestEventClassification,
        TestAbsenceSemantics,
        TestEventValidator,
        TestStateHasher,
        TestTransitionLogWriter,
        TestPipelineEventV1,
        TestDomainHelpers,
    ]

    for cls in test_classes:
        instance = cls()
        for name in dir(instance):
            if not name.startswith("test_"):
                continue
            try:
                getattr(instance, name)()
                print(f"  PASS  {cls.__name__}.{name}")
            except Exception as exc:
                failures += 1
                print(f"  FAIL  {cls.__name__}.{name}: {exc}")
    return failures


if __name__ == "__main__":
    count = _run_all_tests()
    if count:
        print(f"\n{count} test(s) FAILED")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
