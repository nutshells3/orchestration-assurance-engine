"""Integration tests for TRC-004 / TRC-005 mutation capture wiring.

Verifies that hidden-assumption additions, relation mutations, gap detection,
and promotion transitions produce valid PipelineEventV1 events with
deterministic before/after state hashes.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from mutation capture test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine import (  # noqa: E402
    ArtifactStore,
    AssuranceProfile,
    EventNormalizer,
    Gate,
    PipelineEventV1,
    PromotionStateMachine,
    ReviewActorRole,
    StateHasher,
    validate_event,
    validate_event_stream,
)
from formal_claim_engine.claim_trace_service import ClaimTraceService  # noqa: E402
from formal_claim_engine.claim_trace_types import (  # noqa: E402
    Claim,
    ClaimRole,
    ClaimStatus,
    Domain,
    TraceProjectRecord,
)
from formal_claim_engine.promotion_state_machine import PromotionStateError  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_PROFILE_PATH = REPO_ROOT / "examples" / "theorem-audit" / "assurance-profile.json"


def load_profile() -> AssuranceProfile:
    return AssuranceProfile.model_validate(
        json.loads(FIXTURE_PROFILE_PATH.read_text(encoding="utf-8"))
    )


def _make_project(project_id: str = "proj.test1") -> TraceProjectRecord:
    return TraceProjectRecord(
        id=project_id,
        name="Test Project",
        domain=Domain.general,
        description="Unit test project",
    )


def _make_graph_data(project: TraceProjectRecord) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "graph_id": f"tracer.{project.id}",
        "project_id": project.id,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "description": project.description,
        "claims": [
            {
                "claim_id": "c.root1",
                "title": "Root claim",
                "statement": "The root premise holds.",
                "tags": ["tracer_role:premise", "tracer_status:stated", "tracer_depth:0", "tracer_domain:general"],
            }
        ],
        "relations": [],
        "root_claim_ids": ["c.root1"],
        "graph_policy": {},
    }


# ---------------------------------------------------------------------------
# TRC-004: Hidden assumption and gap mutation logging
# ---------------------------------------------------------------------------

def _build_service_with_mock_llm(llm_response: dict[str, Any]) -> ClaimTraceService:
    """Build a ClaimTraceService whose LLM returns a canned JSON response."""
    service = ClaimTraceService.__new__(ClaimTraceService)
    from formal_claim_engine.config import PipelineConfig

    service.config = PipelineConfig()
    service.llm = AsyncMock()
    service.repository = None  # type: ignore[assignment]
    service._event_normalizer = None
    return service


def test_trace_forward_emits_hidden_assumption_and_relation_events() -> None:
    """trace_forward must emit add_hidden_assumption + relation_proposal events."""
    project = _make_project()
    graph_data = _make_graph_data(project)

    llm_result = {
        "trace": ["c.root1"],
        "new_hidden_assumptions": [
            {
                "title": "Implicit continuity assumption",
                "statement": "We assume the function is continuous on [a,b].",
                "needed_for": "c.root1",
            }
        ],
        "gaps": [
            {
                "kind": "logical_leap",
                "description": "Gap between premise and conclusion",
                "between": ["c.root1"],
                "severity": "major",
            }
        ],
        "summary": "Forward trace completed.",
    }

    service = _build_service_with_mock_llm(llm_result)

    # Patch internal methods to control state
    service._load_project = lambda pid: (project, graph_data)  # type: ignore[attr-defined]
    service._save_project = lambda p, gd: None  # type: ignore[attr-defined]

    claim_index = {}
    for cd in graph_data["claims"]:
        c = Claim(
            id=cd["claim_id"],
            title=cd["title"],
            statement=cd["statement"],
            role=ClaimRole.premise,
            status=ClaimStatus.stated,
            domain=Domain.general,
        )
        claim_index[cd["claim_id"]] = c
    service._claims_index = lambda p, gd: claim_index  # type: ignore[attr-defined]

    async def mock_llm_call(system: str, user: str) -> str:
        return json.dumps(llm_result)

    service._llm_call = mock_llm_call  # type: ignore[attr-defined]

    def mock_append_claim(proj, gd, claim):
        gd = dict(gd)
        gd["claims"] = [*gd.get("claims", []), {"claim_id": claim.id, "title": claim.title}]
        return gd

    service._append_claim = mock_append_claim  # type: ignore[attr-defined]

    with patch("formal_claim_engine.claim_trace_service._forward_trace", return_value=[]):
        with patch.object(
            service, "_build_graph_context", return_value="mock context"
        ):
            result = asyncio.get_event_loop().run_until_complete(
                service.trace_forward("proj.test1", "c.root1")
            )

    normalizer = service._event_normalizer
    assert normalizer is not None, "EventNormalizer should have been created"
    assert len(normalizer.events) >= 2, f"Expected at least 2 events, got {len(normalizer.events)}"

    # Check hidden assumption event
    hidden_events = [e for e in normalizer.events if e.event_type == "add_hidden_assumption"]
    assert len(hidden_events) == 1, f"Expected 1 hidden assumption event, got {len(hidden_events)}"
    ha_event = hidden_events[0]
    assert ha_event.phase == "trace_forward"
    assert ha_event.accepted is True
    assert ha_event.before_hash != ""
    assert ha_event.after_hash != ""
    assert ha_event.proposal["assumption_text"] == "We assume the function is continuous on [a,b]."
    assert ha_event.proposal["attaches_to"] == "c.root1"
    assert len(ha_event.changed_ids) == 2  # claim id + relation id

    # Check relation proposal event
    rel_events = [e for e in normalizer.events if e.event_type == "relation_proposal"]
    assert len(rel_events) == 1, f"Expected 1 relation event, got {len(rel_events)}"
    rel_event = rel_events[0]
    assert rel_event.phase == "trace_forward"
    assert rel_event.accepted is True
    assert rel_event.before_hash != ""
    assert rel_event.after_hash != ""
    assert rel_event.proposal["relation_type"] == "assumes"

    # Validate entire event stream
    errors = validate_event_stream(normalizer.events)
    assert errors == [], f"Event stream validation errors: {errors}"

    # Step ordering must be monotonic
    steps = [e.step for e in normalizer.events]
    assert steps == sorted(steps), f"Steps not monotonic: {steps}"
    assert len(set(steps)) == len(steps), f"Duplicate steps found: {steps}"


def test_trace_backward_emits_hidden_assumption_events() -> None:
    """trace_backward must emit add_hidden_assumption + relation_proposal events."""
    project = _make_project()
    graph_data = _make_graph_data(project)

    llm_result = {
        "trace": ["c.root1"],
        "new_hidden_assumptions": [
            {
                "title": "Unstated axiom",
                "statement": "Axiom of choice is implicitly invoked.",
                "needed_for": "c.root1",
            }
        ],
        "gaps": [],
        "foundation_completeness": "partial",
        "summary": "Backward trace completed.",
    }

    service = _build_service_with_mock_llm(llm_result)

    service._load_project = lambda pid: (project, graph_data)  # type: ignore[attr-defined]
    service._save_project = lambda p, gd: None  # type: ignore[attr-defined]

    claim_index = {}
    for cd in graph_data["claims"]:
        c = Claim(
            id=cd["claim_id"],
            title=cd["title"],
            statement=cd["statement"],
            role=ClaimRole.premise,
            status=ClaimStatus.stated,
            domain=Domain.general,
        )
        claim_index[cd["claim_id"]] = c
    service._claims_index = lambda p, gd: claim_index  # type: ignore[attr-defined]

    async def mock_llm_call(system: str, user: str) -> str:
        return json.dumps(llm_result)

    service._llm_call = mock_llm_call  # type: ignore[attr-defined]

    def mock_append_claim(proj, gd, claim):
        gd = dict(gd)
        gd["claims"] = [*gd.get("claims", []), {"claim_id": claim.id, "title": claim.title}]
        return gd

    service._append_claim = mock_append_claim  # type: ignore[attr-defined]

    with patch("formal_claim_engine.claim_trace_service._backward_trace", return_value=[]):
        with patch.object(
            service, "_build_graph_context", return_value="mock context"
        ):
            result = asyncio.get_event_loop().run_until_complete(
                service.trace_backward("proj.test1", "c.root1")
            )

    normalizer = service._event_normalizer
    assert normalizer is not None
    assert len(normalizer.events) >= 2

    hidden_events = [e for e in normalizer.events if e.event_type == "add_hidden_assumption"]
    assert len(hidden_events) == 1
    assert hidden_events[0].phase == "trace_backward"
    assert hidden_events[0].accepted is True
    assert hidden_events[0].proposal["assumption_text"] == "Axiom of choice is implicitly invoked."

    rel_events = [e for e in normalizer.events if e.event_type == "relation_proposal"]
    assert len(rel_events) == 1
    assert rel_events[0].phase == "trace_backward"

    errors = validate_event_stream(normalizer.events)
    assert errors == [], f"Validation errors: {errors}"


def test_find_gaps_emits_gap_detection_event() -> None:
    """find_gaps must emit a gap_detection generic event."""
    project = _make_project()
    graph_data = _make_graph_data(project)

    llm_result = {
        "gaps": [
            {"kind": "missing_support", "description": "No evidence for premise", "affected_claim_ids": ["c.root1"], "severity": "major"},
            {"kind": "circularity", "description": "Circular argument detected", "affected_claim_ids": ["c.root1"], "severity": "minor"},
        ],
        "structural_issues": [],
        "summary": "Gap analysis completed.",
    }

    service = _build_service_with_mock_llm(llm_result)

    service._load_project = lambda pid: (project, graph_data)  # type: ignore[attr-defined]
    service._save_project = lambda p, gd: None  # type: ignore[attr-defined]

    async def mock_llm_call(system: str, user: str) -> str:
        return json.dumps(llm_result)

    service._llm_call = mock_llm_call  # type: ignore[attr-defined]

    with patch("formal_claim_engine.claim_trace_service._claim_ids", return_value=["c.root1"]):
        with patch.object(
            service, "_build_graph_context", return_value="mock context"
        ):
            result = asyncio.get_event_loop().run_until_complete(
                service.find_gaps("proj.test1")
            )

    normalizer = service._event_normalizer
    assert normalizer is not None
    assert len(normalizer.events) == 1

    gap_event = normalizer.events[0]
    assert gap_event.event_type == "gap_detection"
    assert gap_event.phase == "find_gaps"
    assert gap_event.accepted is True
    assert gap_event.proposal["gap_count"] == 2
    assert gap_event.before_hash != ""
    assert gap_event.after_hash != ""
    # Gaps were added so hashes should differ
    assert gap_event.before_hash != gap_event.after_hash

    errors = validate_event(gap_event)
    assert errors == [], f"Event validation errors: {errors}"


# ---------------------------------------------------------------------------
# TRC-005: Promotion state machine event capture
# ---------------------------------------------------------------------------

def test_promotion_success_emits_accepted_event() -> None:
    """Successful promotion transition must emit an accepted promotion event."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        profile = load_profile()
        store.save_assurance_profile(
            profile,
            actor="auditor",
            reason="fixture_import",
            metadata={"source": "theorem-audit"},
        )

        normalizer = EventNormalizer("test-promotion-success")
        machine = PromotionStateMachine(store, event_normalizer=normalizer)

        # draft -> queued (valid linear step)
        queued = machine.transition(
            profile,
            target_gate="queued",
            actor="human.reviewer",
            actor_role=ReviewActorRole.reviewer,
            notes="Queue for review.",
        )
        assert queued.current_gate == Gate.queued

        assert len(normalizer.events) == 1
        evt = normalizer.events[0]
        assert evt.event_type == "promotion_transition"
        assert evt.phase == "promotion"
        assert evt.accepted is True
        assert evt.reject_reason == ""
        assert evt.proposal["target_gate"] == "queued"
        assert evt.before_hash != ""
        assert evt.after_hash != ""
        # State changed, so hashes must differ
        assert evt.before_hash != evt.after_hash

        errors = validate_event(evt)
        assert errors == [], f"Validation errors: {errors}"


def test_promotion_rejection_structural_emits_rejected_event() -> None:
    """Illegal structural transition must emit a rejected event with reject_reason."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        profile = load_profile()
        store.save_assurance_profile(
            profile,
            actor="auditor",
            reason="fixture_import",
            metadata={"source": "theorem-audit"},
        )

        normalizer = EventNormalizer("test-promotion-rejection")
        machine = PromotionStateMachine(store, event_normalizer=normalizer)

        # Try to skip gates: draft -> research_only (should fail)
        try:
            machine.transition(
                profile,
                target_gate="research_only",
                actor="human.reviewer",
                actor_role=ReviewActorRole.reviewer,
            )
            assert False, "Should have raised PromotionStateError"
        except PromotionStateError:
            pass

        assert len(normalizer.events) == 1
        evt = normalizer.events[0]
        assert evt.event_type == "promotion_transition"
        assert evt.accepted is False
        assert evt.reject_reason != "", "reject_reason must be non-empty for rejections"
        assert "may not be skipped" in evt.reject_reason
        # State unchanged on rejection, so hashes must be equal
        assert evt.before_hash == evt.after_hash

        errors = validate_event(evt)
        assert errors == [], f"Validation errors: {errors}"


def test_promotion_rejection_override_required_emits_event() -> None:
    """Override-required rejection must emit a rejected event."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        profile_payload = load_profile().model_dump(mode="json", exclude_none=True)
        profile_payload["gate"] = "blocked"
        profile_payload["required_actions"] = ["Resolve issue"]
        profile = AssuranceProfile.model_validate(profile_payload)
        store.save_assurance_profile(
            profile,
            actor="auditor",
            reason="fixture_import",
        )

        normalizer = EventNormalizer("test-override-rejection")
        machine = PromotionStateMachine(store, event_normalizer=normalizer)

        try:
            machine.transition(
                profile,
                target_gate="queued",
                actor="human.reviewer",
                actor_role=ReviewActorRole.reviewer,
            )
            assert False, "Should have raised PromotionStateError"
        except PromotionStateError:
            pass

        assert len(normalizer.events) == 1
        evt = normalizer.events[0]
        assert evt.accepted is False
        assert "requires override" in evt.reject_reason
        assert evt.before_hash == evt.after_hash


def test_promotion_mixed_stream_validates() -> None:
    """A mixed sequence of accepted/rejected promotion events must form a valid stream."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        profile = load_profile()
        store.save_assurance_profile(
            profile,
            actor="auditor",
            reason="fixture_import",
            metadata={"source": "theorem-audit"},
        )

        normalizer = EventNormalizer("test-mixed-stream")
        machine = PromotionStateMachine(store, event_normalizer=normalizer)

        # 1. Rejected: try to skip
        try:
            machine.transition(
                profile,
                target_gate="research_only",
                actor="human.reviewer",
                actor_role=ReviewActorRole.reviewer,
            )
        except PromotionStateError:
            pass

        # 2. Accepted: draft -> queued
        machine.transition(
            profile,
            target_gate="queued",
            actor="human.reviewer",
            actor_role=ReviewActorRole.reviewer,
        )

        # 3. Accepted: queued -> research_only
        machine.transition(
            profile,
            target_gate="research_only",
            actor="human.reviewer",
            actor_role=ReviewActorRole.reviewer,
        )

        assert len(normalizer.events) == 3

        # Verify stream: 1 rejected + 2 accepted
        rejected = [e for e in normalizer.events if not e.accepted]
        accepted = [e for e in normalizer.events if e.accepted]
        assert len(rejected) == 1
        assert len(accepted) == 2

        # All rejected events must have non-empty reject_reason
        for evt in rejected:
            assert evt.reject_reason != ""

        # Full stream validation
        errors = validate_event_stream(normalizer.events)
        assert errors == [], f"Stream validation errors: {errors}"

        # Step ordering
        steps = [e.step for e in normalizer.events]
        assert steps == sorted(steps)
        assert len(set(steps)) == len(steps)


# ---------------------------------------------------------------------------
# Cross-cutting: StateHasher and EventNormalizer unit checks
# ---------------------------------------------------------------------------

def test_state_hasher_deterministic() -> None:
    """StateHasher must produce the same hash for equivalent state dicts."""
    state_a = {"claims": [{"id": "c.1"}], "relations": []}
    state_b = {"claims": [{"id": "c.1"}], "relations": []}
    assert StateHasher.compute_state_hash(state_a) == StateHasher.compute_state_hash(state_b)

    state_c = {"claims": [{"id": "c.2"}], "relations": []}
    assert StateHasher.compute_state_hash(state_a) != StateHasher.compute_state_hash(state_c)


def test_state_hasher_key_order_independent() -> None:
    """StateHasher must produce the same hash regardless of key insertion order."""
    state_a = {"b": 2, "a": 1}
    state_b = {"a": 1, "b": 2}
    assert StateHasher.compute_state_hash(state_a) == StateHasher.compute_state_hash(state_b)


def test_event_normalizer_step_counter() -> None:
    """EventNormalizer step counter must auto-increment."""
    normalizer = EventNormalizer("trace.test")
    normalizer.record_generic_event(
        phase="test", event_type="test_a", actor="system",
        before_state={}, after_state={"x": 1},
    )
    normalizer.record_generic_event(
        phase="test", event_type="test_b", actor="system",
        before_state={"x": 1}, after_state={"x": 2},
    )
    assert normalizer.events[0].step == 1
    assert normalizer.events[1].step == 2


def test_event_normalizer_reuse_across_session() -> None:
    """ClaimTraceService.get_event_normalizer should reuse the same instance for
    the same trace_id and create a new one when the id changes."""
    service = ClaimTraceService.__new__(ClaimTraceService)
    service._event_normalizer = None

    norm_a = service.get_event_normalizer("trace.a")
    norm_a2 = service.get_event_normalizer("trace.a")
    assert norm_a is norm_a2, "Same trace_id should reuse instance"

    norm_b = service.get_event_normalizer("trace.b")
    assert norm_b is not norm_a, "Different trace_id should create new instance"


def test_rejected_event_requires_reject_reason() -> None:
    """A rejected event with empty reject_reason must fail validation."""
    from formal_claim_engine.event_validation import validate_event as _validate

    evt = PipelineEventV1(
        trace_id="t.1",
        step=1,
        phase="test",
        event_type="test",
        actor="system",
        before_hash="abc",
        after_hash="abc",
        accepted=False,
        reject_reason="",  # intentionally empty
    )
    errors = _validate(evt)
    assert any("reject_reason" in e for e in errors), f"Expected reject_reason error, got: {errors}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> None:
    test_state_hasher_deterministic()
    print("PASS: test_state_hasher_deterministic")

    test_state_hasher_key_order_independent()
    print("PASS: test_state_hasher_key_order_independent")

    test_event_normalizer_step_counter()
    print("PASS: test_event_normalizer_step_counter")

    test_event_normalizer_reuse_across_session()
    print("PASS: test_event_normalizer_reuse_across_session")

    test_rejected_event_requires_reject_reason()
    print("PASS: test_rejected_event_requires_reject_reason")

    test_trace_forward_emits_hidden_assumption_and_relation_events()
    print("PASS: test_trace_forward_emits_hidden_assumption_and_relation_events")

    test_trace_backward_emits_hidden_assumption_events()
    print("PASS: test_trace_backward_emits_hidden_assumption_events")

    test_find_gaps_emits_gap_detection_event()
    print("PASS: test_find_gaps_emits_gap_detection_event")

    test_promotion_success_emits_accepted_event()
    print("PASS: test_promotion_success_emits_accepted_event")

    test_promotion_rejection_structural_emits_rejected_event()
    print("PASS: test_promotion_rejection_structural_emits_rejected_event")

    test_promotion_rejection_override_required_emits_event()
    print("PASS: test_promotion_rejection_override_required_emits_event")

    test_promotion_mixed_stream_validates()
    print("PASS: test_promotion_mixed_stream_validates")

    print("\nAll TRC-004/TRC-005 mutation capture tests passed.")


if __name__ == "__main__":
    main()
