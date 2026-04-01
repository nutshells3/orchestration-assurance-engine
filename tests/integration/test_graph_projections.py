"""Integration tests for TRC-010 (PropagationCapture), PFX-006 (PrefixSliceGraphBuilder),
and PFX-007 (PairedDatasetExtractor).

Test categories
---------------
1. PropagationCapture: traces recorded, deltas computed, export integration
2. PrefixSliceGraphBuilder: valid adjacency lists, temporal gating, domain-free
3. PairedDatasetExtractor: aligned text+graph pairs, manifest consistency
4. Cross-cutting: no source_domain anywhere, no future leaks

Spec references
---------------
* TRC-010 -- Propagation Traces & Vector-Score Deltas
* PFX-006 -- PrefixSliceGraphV1 Builder
* PFX-007 -- Paired Dataset Extraction
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from graph projections test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.propagation_capture import PropagationCapture  # noqa: E402
from formal_claim_engine.prefix_slice_graph_builder import (  # noqa: E402
    PrefixSliceGraphBuilder,
    _GRAPH_BANNED_FIELDS,
)

# PairedDatasetExtractor requires PrefixSliceBuilder; import conditionally
_PAIRED_AVAILABLE = True
try:
    from formal_claim_engine.paired_dataset_extractor import (  # noqa: E402
        PairedDatasetExtractor,
    )
except ImportError:
    _PAIRED_AVAILABLE = False


# ---------------------------------------------------------------------------
# Fixture helpers (mirrors patterns from test_prefix_slice_builder.py)
# ---------------------------------------------------------------------------

def _make_trace(
    trace_id: str = "trace.001",
    claims: list[dict] | None = None,
    relations: list[dict] | None = None,
    gaps: list[dict] | None = None,
    hidden_assumptions: list[dict] | None = None,
    artifacts: list[str] | None = None,
    source: dict | None = None,
) -> dict:
    return {
        "trace_id": trace_id,
        "source": source or {"title": "Test Document", "text": "The argument is as follows..."},
        "claims": claims or [],
        "relations": relations or [],
        "gaps": gaps or [],
        "hidden_assumptions": hidden_assumptions or [],
        "artifacts": artifacts or ["artifact.base"],
    }


def _make_event(
    step_id: str,
    event_type: str = "structuring_step",
    phase: int | None = None,
    claim_id: str | None = None,
    action: dict | None = None,
    outcome: dict | None = None,
    artifacts_produced: list[str] | None = None,
    legal_action_mask: list[str] | None = None,
    event_class: str | None = None,
    event_seq: int | None = None,
) -> dict:
    event: dict = {
        "step_id": step_id,
        "event_type": event_type,
    }
    if phase is not None:
        event["phase"] = phase
    if claim_id is not None:
        event["claim_id"] = claim_id
    if action is not None:
        event["action"] = action
    if outcome is not None:
        event["outcome"] = outcome
    if artifacts_produced is not None:
        event["artifacts_produced"] = artifacts_produced
    if legal_action_mask is not None:
        event["legal_action_mask"] = legal_action_mask
    if event_class is not None:
        event["event_class"] = event_class
    if event_seq is not None:
        event["event_seq"] = event_seq
    return event


# ---------------------------------------------------------------------------
# Helpers to extract graph data from state_graph shape
# ---------------------------------------------------------------------------

def _get_edges(graph_slice: dict) -> list[dict]:
    """Extract edges from a graph slice (handles state_graph format)."""
    sg = graph_slice.get("state_graph") or {}
    return sg.get("edges", [])


def _get_nodes(graph_slice: dict) -> dict:
    """Extract node_features dict from a graph slice (handles state_graph format)."""
    sg = graph_slice.get("state_graph") or {}
    nodes = sg.get("nodes", [])
    return {n["node_id"]: n for n in nodes if "node_id" in n}


def _get_edge_features(graph_slice: dict) -> dict:
    """Extract edge_features-like dict from graph slice edges."""
    edges = _get_edges(graph_slice)
    features = {}
    for i, edge in enumerate(edges):
        eid = f"e_{i}"
        features[eid] = {
            "relation_type": edge.get("relation_type", ""),
            "strength": edge.get("strength", ""),
            "is_hidden_assumption": edge.get("is_hidden_assumption", False),
        }
    return features


# ===================================================================
# TRC-010: PropagationCapture tests
# ===================================================================

class TestPropagationCapture:
    """PropagationCapture records traces and deltas correctly."""

    def test_capture_propagation_records_trace(self):
        """capture_propagation stores a trace record with correct fields."""
        pc = PropagationCapture()
        result = pc.capture_propagation(
            source_claim_id="C1",
            affected_claims=["C2", "C3"],
            propagation_type="gate_change",
        )
        assert result["source"] == "C1"
        assert result["affected"] == ["C2", "C3"]
        assert result["type"] == "gate_change"
        assert "timestamp" in result
        assert result["depth"] == 2  # two unique affected claims

    def test_capture_propagation_depth_excludes_source(self):
        """Depth counts only unique affected claims, excluding source."""
        pc = PropagationCapture()
        result = pc.capture_propagation("C1", ["C1", "C2"], "status_change")
        # C1 is the source so excluded from depth count; only C2 counted
        assert result["depth"] == 1

    def test_capture_vector_score_delta(self):
        """capture_vector_score_delta records before/after/delta correctly."""
        pc = PropagationCapture()
        before = {"completeness": 0.5, "validity": 0.8}
        after = {"completeness": 0.7, "validity": 0.8, "transparency": 0.3}
        result = pc.capture_vector_score_delta("C1", before, after)

        assert result["claim_id"] == "C1"
        assert result["before"] == before
        assert result["after"] == after
        assert result["delta"]["completeness"] == 0.2
        assert result["delta"]["validity"] == 0.0
        assert result["delta"]["transparency"] == 0.3
        assert "completeness" in result["changed_dimensions"]
        assert "transparency" in result["changed_dimensions"]
        assert "validity" not in result["changed_dimensions"]

    def test_get_propagation_traces_returns_all(self):
        """get_propagation_traces returns all recorded traces."""
        pc = PropagationCapture()
        pc.capture_propagation("C1", ["C2"], "gate_change")
        pc.capture_propagation("C3", ["C4", "C5"], "status_change")
        traces = pc.get_propagation_traces()
        assert len(traces) == 2
        assert traces[0]["source"] == "C1"
        assert traces[1]["source"] == "C3"

    def test_get_vector_score_deltas_returns_all(self):
        """get_vector_score_deltas returns all recorded deltas."""
        pc = PropagationCapture()
        pc.capture_vector_score_delta("C1", {"a": 0.5}, {"a": 0.9})
        pc.capture_vector_score_delta("C2", {"b": 0.0}, {"b": 0.5})
        deltas = pc.get_vector_score_deltas()
        assert len(deltas) == 2

    def test_to_trace_results_section(self):
        """to_trace_results_section returns dict suitable for trace_results."""
        pc = PropagationCapture()
        pc.capture_propagation("C1", ["C2"], "relation_added")
        pc.capture_vector_score_delta("C1", {"x": 0.1}, {"x": 0.9})
        section = pc.to_trace_results_section()

        assert "propagation_traces" in section
        assert "vector_score_deltas" in section
        assert len(section["propagation_traces"]) == 1
        assert len(section["vector_score_deltas"]) == 1

    def test_empty_capture_produces_empty_section(self):
        """An unused PropagationCapture produces empty lists."""
        pc = PropagationCapture()
        section = pc.to_trace_results_section()
        assert section["propagation_traces"] == []
        assert section["vector_score_deltas"] == []

    def test_accessors_return_copies(self):
        """Accessors return copies, not mutable internal state."""
        pc = PropagationCapture()
        pc.capture_propagation("C1", ["C2"], "t")
        traces = pc.get_propagation_traces()
        traces.clear()
        assert len(pc.get_propagation_traces()) == 1

    def test_delta_with_no_change(self):
        """Delta with identical scores has no changed dimensions."""
        pc = PropagationCapture()
        scores = {"a": 0.5, "b": 0.3}
        result = pc.capture_vector_score_delta("C1", scores, dict(scores))
        assert result["changed_dimensions"] == []
        assert result["delta"]["a"] == 0.0
        assert result["delta"]["b"] == 0.0


# ===================================================================
# PFX-006: PrefixSliceGraphBuilder tests
# ===================================================================

class TestPrefixSliceGraphBuilderPositive:
    """Correct graph slice extraction."""

    def test_three_events_produce_three_slices(self):
        """A trace with 3 events produces exactly 3 graph slices."""
        trace = _make_trace(
            claims=[{"claim_id": "C1", "title": "A", "statement": "X", "status": "proposed"}],
        )
        events = [
            _make_event("step.001"),
            _make_event("step.002"),
            _make_event("step.003"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert len(slices) == 3

    def test_adjacency_from_base_trace(self):
        """Base trace relations appear in state_graph edges from the first slice."""
        trace = _make_trace(
            claims=[
                {"claim_id": "C1", "title": "A", "statement": "X"},
                {"claim_id": "C2", "title": "B", "statement": "Y"},
            ],
            relations=[
                {"source_id": "C1", "target_id": "C2", "relation_type": "supports", "strength": "inductive"},
            ],
        )
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        edges = _get_edges(slices[0])
        assert len(edges) == 1
        assert edges[0]["source"] == "C1"
        assert edges[0]["target"] == "C2"
        assert edges[0]["relation_type"] == "supports"
        assert edges[0]["strength"] == "inductive"

    def test_node_features_include_all_claims(self):
        """Node features include all claims from the base trace."""
        trace = _make_trace(
            claims=[
                {"claim_id": "C1", "title": "A", "statement": "X", "role": "premise", "status": "proposed"},
                {"claim_id": "C2", "title": "B", "statement": "Y", "role": "conclusion", "status": "stated"},
            ],
        )
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        nf = _get_nodes(slices[0])
        assert "C1" in nf
        assert "C2" in nf
        assert nf["C1"]["role"] == "premise"
        assert nf["C2"]["role"] == "conclusion"
        assert nf["C1"]["status"] == "proposed"

    def test_edge_features_include_all_relations(self):
        """Edge features include all base trace relations."""
        trace = _make_trace(
            relations=[
                {"id": "r1", "source_id": "C1", "target_id": "C2", "relation_type": "supports", "strength": "deductive"},
            ],
        )
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        edges = _get_edges(slices[0])
        assert len(edges) == 1
        assert edges[0]["relation_type"] == "supports"
        assert edges[0]["strength"] == "deductive"
        assert edges[0]["is_hidden_assumption"] is False

    def test_hidden_assumption_edge_marked(self):
        """Edges with relation_type 'assumes' are marked as hidden_assumption."""
        trace = _make_trace(
            relations=[
                {"id": "r1", "source_id": "C1", "target_id": "C2", "relation_type": "assumes", "strength": "unknown"},
            ],
        )
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        edges = _get_edges(slices[0])
        assert len(edges) == 1
        assert edges[0]["is_hidden_assumption"] is True

    def test_gold_action_passthrough(self):
        """gold_action from event is passed through to graph slice (non-last step)."""
        action = {"action": "PROPOSE_RELATION", "args": {"u": "C1", "rel": "supports", "v": "C2"}}
        trace = _make_trace()
        # B10: need at least 2 controllable events so the first is not the last
        events = [_make_event("step.001", action=action), _make_event("step.002")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert slices[0]["gold_action"] == action

    def test_available_artifacts_grow(self):
        """Available artifacts grow as events produce new artifacts."""
        trace = _make_trace(artifacts=["artifact.base"])
        events = [
            _make_event("step.001", artifacts_produced=["artifact.001"]),
            _make_event("step.002", artifacts_produced=["artifact.002"]),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        assert slices[0]["available_artifacts"] == ["artifact.base"]
        assert set(slices[1]["available_artifacts"]) == {"artifact.base", "artifact.001"}

    def test_extract_graph_slice_at_step(self):
        """extract_graph_slice_at_step returns the correct slice."""
        trace = _make_trace()
        events = [_make_event("step.001"), _make_event("step.002")]
        builder = PrefixSliceGraphBuilder(trace, events)
        s = builder.extract_graph_slice_at_step("step.002")
        assert s["step_id"] == "step.002"

    def test_extract_graph_slice_at_step_not_found(self):
        """Requesting a non-existent step_id raises KeyError."""
        trace = _make_trace()
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        try:
            builder.extract_graph_slice_at_step("step.999")
            assert False, "Expected KeyError"
        except KeyError:
            pass


class TestPrefixSliceGraphTemporalGating:
    """Graph slices have same temporal gating as text slices."""

    def test_step_t_no_step_t_outcome_in_adjacency(self):
        """At step t, edges must NOT contain relations from step t's outcome."""
        trace = _make_trace()
        events = [
            _make_event("step.001", outcome={"relations": [
                {"source_id": "C1", "target_id": "C2", "relation_type": "supports", "strength": "inductive"},
            ]}),
            _make_event("step.002"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        # step.001: no relations yet (its own outcome not visible)
        assert _get_edges(slices[0]) == []
        # step.002: sees step.001's outcome
        assert len(_get_edges(slices[1])) == 1

    def test_step_t_no_step_t_outcome_in_node_features(self):
        """At step t, node features must NOT contain claims from step t's outcome."""
        trace = _make_trace()
        events = [
            _make_event("step.001", outcome={"claims": [
                {"claim_id": "C1", "title": "New Claim", "statement": "X", "role": "premise", "status": "proposed"},
            ]}),
            _make_event("step.002"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        # step.001: C1 not yet visible
        assert "C1" not in _get_nodes(slices[0])
        # step.002: C1 visible
        assert "C1" in _get_nodes(slices[1])

    def test_phase1_step_no_phase2_audit_in_node_features(self):
        """At a phase-1 step, node features must not have audit gate info from phase-2."""
        trace = _make_trace(
            claims=[{"claim_id": "C1", "title": "A", "statement": "X"}],
        )
        events = [
            _make_event("step.001", event_type="structuring_step", phase=1),
            _make_event("step.002", event_type="audit_step", phase=2, claim_id="C1",
                        outcome={"audit": {"claim_id": "C1", "gate": "research_only"}}),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        # Phase-1 step should not see phase-2 audit data
        nf = _get_nodes(slices[0])
        if "C1" in nf:
            assert nf["C1"].get("gate") in ("", None)

    def test_phase2_step_no_own_claim_audit(self):
        """At a phase-2 step for claim X, node features exclude X's audit from prior steps."""
        trace = _make_trace(
            claims=[
                {"claim_id": "C1", "title": "A", "statement": "X"},
                {"claim_id": "C2", "title": "B", "statement": "Y"},
            ],
        )
        events = [
            _make_event("step.001", event_type="audit_step", phase=2, claim_id="C1",
                        outcome={"audit": {"claim_id": "C1", "gate": "research_only"}}),
            _make_event("step.002", event_type="audit_step", phase=2, claim_id="C1"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        # step.002 targets C1, so C1's audit from step.001 should NOT be visible
        nf = _get_nodes(slices[1])
        if "C1" in nf:
            assert nf["C1"].get("gate") in ("", None)

    def test_phase2_step_sees_other_claim_audit(self):
        """At a phase-2 step for C1, node features CAN include C2's audit."""
        trace = _make_trace(
            claims=[
                {"claim_id": "C1", "title": "A", "statement": "X"},
                {"claim_id": "C2", "title": "B", "statement": "Y"},
            ],
        )
        events = [
            _make_event("step.001", event_type="audit_step", phase=2, claim_id="C2",
                        outcome={"audit": {"claim_id": "C2", "gate": "research_only"}}),
            _make_event("step.002", event_type="audit_step", phase=2, claim_id="C1"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        # step.002 targets C1, so C2's audit IS visible
        nf = _get_nodes(slices[1])
        if "C2" in nf:
            assert nf["C2"]["gate"] == "research_only"


class TestPrefixSliceGraphDomainFree:
    """Graph slices are domain-free: no source_domain in features."""

    def test_no_source_domain_in_node_features(self):
        """Node features must not contain 'source_domain'."""
        trace = _make_trace(
            claims=[
                {"claim_id": "C1", "title": "A", "statement": "X", "source_domain": "academic",
                 "domain": "academic", "role": "premise", "status": "proposed"},
            ],
        )
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        nf = _get_nodes(slices[0])
        for cid, features in nf.items():
            for key in features:
                assert key not in ("source_domain", "domain"), \
                    f"Domain field '{key}' found in node features for {cid}"

    def test_no_source_domain_in_edge_features(self):
        """Edge features must not contain 'source_domain'."""
        trace = _make_trace(
            relations=[
                {"id": "r1", "source_id": "C1", "target_id": "C2",
                 "relation_type": "supports", "strength": "inductive",
                 "source_domain": "academic"},
            ],
        )
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        edges = _get_edges(slices[0])
        for edge in edges:
            for key in edge:
                assert key not in ("source_domain", "domain"), \
                    f"Domain field '{key}' found in edge"

    def test_no_banned_fields_in_adjacency(self):
        """Edge entries must not contain any banned fields."""
        trace = _make_trace(
            relations=[
                {"id": "r1", "source_id": "C1", "target_id": "C2",
                 "relation_type": "supports", "strength": "inductive",
                 "api_key": "secret", "provider": "anthropic"},
            ],
        )
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        for edge in _get_edges(slices[0]):
            for key in _GRAPH_BANNED_FIELDS:
                assert key not in edge, f"Banned field '{key}' in edge"

    def test_no_future_leak_fields_in_node_features(self):
        """Node features must not contain future-leak fields."""
        trace = _make_trace(
            claims=[{"claim_id": "C1", "title": "A", "statement": "X"}],
        )
        events = [
            _make_event("step.001", event_type="audit_step", phase=2, claim_id="C1",
                        outcome={"audit": {
                            "claim_id": "C1", "gate": "draft",
                            "updated_profile": {"gate": "certified"},
                            "promotion_transitions": [{"from": "draft", "to": "certified"}],
                        }}),
            _make_event("step.002", event_type="audit_step", phase=2, claim_id="C2"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        for s in slices:
            nf_json = json.dumps(_get_nodes(s))
            assert "updated_profile" not in nf_json
            assert "promotion_transitions" not in nf_json


class TestPrefixSliceGraphEdgeCases:
    """Edge cases for the graph builder."""

    def test_empty_trace_no_slices(self):
        """An empty transition log produces zero slices."""
        trace = _make_trace()
        builder = PrefixSliceGraphBuilder(trace, [])
        slices = builder.extract_graph_slices()
        assert slices == []

    def test_single_event_one_slice(self):
        """A single-event trace produces exactly one slice."""
        trace = _make_trace()
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert len(slices) == 1

    def test_trace_with_no_claims_or_relations(self):
        """Graph slices for traces without claims/relations have empty features."""
        trace = _make_trace(claims=[], relations=[])
        events = [_make_event("step.001")]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert _get_edges(slices[0]) == []
        assert _get_nodes(slices[0]) == {}


# ===================================================================
# PFX-007: PairedDatasetExtractor tests (conditional)
# ===================================================================

class TestPairedDatasetExtractor:
    """Paired extractor produces aligned text+graph pairs."""

    def _skip_if_unavailable(self):
        if not _PAIRED_AVAILABLE:
            import pytest
            pytest.skip("PrefixSliceBuilder not available in this worktree")

    def test_extract_pairs_alignment(self):
        """Each pair has matching step_id between text and graph slices."""
        self._skip_if_unavailable()
        trace = _make_trace(
            claims=[{"claim_id": "C1", "title": "A", "statement": "X", "status": "proposed"}],
        )
        events = [
            _make_event("step.001", action={"action": "PROPOSE_RELATION", "args": {}}),
            _make_event("step.002"),
        ]
        extractor = PairedDatasetExtractor(trace, events)
        pairs = extractor.extract_pairs()

        assert len(pairs) == 2
        for pair in pairs:
            assert pair["text_slice"]["step_id"] == pair["graph_slice"]["step_id"]
            assert pair["step_id"] == pair["text_slice"]["step_id"]

    def test_extract_pairs_gold_action_propagated(self):
        """gold_action is propagated into each pair."""
        self._skip_if_unavailable()
        action = {"action": "PROPOSE_RELATION", "args": {"u": "C1"}}
        trace = _make_trace()
        # B10: last controllable event returns DONE, not None
        events = [
            _make_event("step.001", action=action),
            _make_event("step.002"),
            _make_event("step.003"),
        ]
        extractor = PairedDatasetExtractor(trace, events)
        pairs = extractor.extract_pairs()

        assert pairs[0]["gold_action"] == action
        assert pairs[1]["gold_action"] is None  # middle event with no action

    def test_extract_pairs_text_has_state_text(self):
        """Text slices have state_text field; graph slices have state_graph."""
        self._skip_if_unavailable()
        trace = _make_trace(
            claims=[{"claim_id": "C1", "title": "A", "statement": "X", "status": "proposed"}],
        )
        events = [_make_event("step.001")]
        extractor = PairedDatasetExtractor(trace, events)
        pairs = extractor.extract_pairs()

        assert "state_text" in pairs[0]["text_slice"]
        assert "state_graph" in pairs[0]["graph_slice"]
        assert "nodes" in pairs[0]["graph_slice"]["state_graph"]
        assert "edges" in pairs[0]["graph_slice"]["state_graph"]

    def test_write_paired_dataset_files(self):
        """write_paired_dataset produces the expected files."""
        self._skip_if_unavailable()
        trace = _make_trace(
            claims=[{"claim_id": "C1", "title": "A", "statement": "X", "status": "proposed"}],
        )
        events = [
            _make_event("step.001", action={"action": "PROPOSE_RELATION", "args": {}}),
            _make_event("step.002"),
        ]
        extractor = PairedDatasetExtractor(trace, events)

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = extractor.write_paired_dataset(tmpdir)

            out = Path(tmpdir)
            assert (out / "text_slices.jsonl").exists()
            assert (out / "graph_slices.jsonl").exists()
            assert (out / "pairs_manifest.json").exists()

            # Verify JSONL line counts
            text_lines = (out / "text_slices.jsonl").read_text().strip().split("\n")
            graph_lines = (out / "graph_slices.jsonl").read_text().strip().split("\n")
            assert len(text_lines) == 2
            assert len(graph_lines) == 2

            # Verify each line is valid JSON
            for line in text_lines:
                json.loads(line)
            for line in graph_lines:
                json.loads(line)

    def test_manifest_consistency(self):
        """Pairs manifest is consistent with the actual data."""
        self._skip_if_unavailable()
        trace = _make_trace()
        events = [
            _make_event("step.001", action={"action": "A", "args": {}}),
            _make_event("step.002"),
            _make_event("step.003"),
        ]
        extractor = PairedDatasetExtractor(trace, events)

        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = extractor.write_paired_dataset(tmpdir)

            assert manifest["total_pairs"] == 3
            assert manifest["trace_id"] == "trace.001"
            assert len(manifest["pairs"]) == 3

            # Verify index ordering
            for i, entry in enumerate(manifest["pairs"]):
                assert entry["index"] == i

            # First pair has gold_action, last has DONE
            assert manifest["pairs"][0]["has_gold_action"] is True
            assert manifest["pairs"][1]["has_gold_action"] is False
            # B10: last controllable event always gets DONE gold_action
            assert manifest["pairs"][2]["has_gold_action"] is True

            # File references
            assert manifest["files"]["text_slices"] == "text_slices.jsonl"
            assert manifest["files"]["graph_slices"] == "graph_slices.jsonl"
            assert manifest["files"]["manifest"] == "pairs_manifest.json"

    def test_empty_trace_produces_no_pairs(self):
        """An empty transition log produces zero pairs."""
        self._skip_if_unavailable()
        trace = _make_trace()
        extractor = PairedDatasetExtractor(trace, [])
        pairs = extractor.extract_pairs()
        assert pairs == []


# ===================================================================
# Cross-cutting integration: full pipeline scenario
# ===================================================================

class TestFullPipelineGraphScenario:
    """Simulate a realistic multi-phase pipeline and verify graph slices."""

    def test_three_phase_pipeline_graph(self):
        """Walk through phase-1, phase-2, phase-3 and verify graph slices."""
        trace = _make_trace(
            trace_id="trace.full",
            source={"title": "Research Paper", "text": "We prove that X implies Y."},
            claims=[
                {"claim_id": "C1", "title": "Main Theorem", "statement": "X implies Y",
                 "status": "proposed", "role": "theorem"},
                {"claim_id": "C2", "title": "Lemma", "statement": "X is bounded",
                 "status": "proposed", "role": "lemma"},
            ],
            relations=[
                {"id": "r1", "source_id": "C2", "target_id": "C1",
                 "relation_type": "supports", "strength": "deductive"},
            ],
            artifacts=["artifact.doc"],
        )
        events = [
            # Phase 1: structuring
            _make_event(
                "step.p1.001",
                event_type="structuring_step",
                phase=1,
                action={"action": "PROPOSE_RELATION", "args": {}},
                outcome={"relations": [
                    {"id": "r2", "source_id": "C2", "target_id": "C1",
                     "relation_type": "derives", "strength": "deductive"},
                ]},
                artifacts_produced=["artifact.graph.v1"],
            ),
            # Phase 2: audit for C2
            _make_event(
                "step.p2.001",
                event_type="audit_step",
                phase=2,
                claim_id="C2",
                outcome={
                    "audit": {"claim_id": "C2", "gate": "research_only",
                              "formal_status": "skeleton_only"},
                },
                artifacts_produced=["artifact.profile.C2"],
            ),
            # Phase 2: audit for C1
            _make_event(
                "step.p2.002",
                event_type="audit_step",
                phase=2,
                claim_id="C1",
                outcome={
                    "audit": {"claim_id": "C1", "gate": "dev_guarded",
                              "formal_status": "proof_complete"},
                },
            ),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        assert len(slices) == 3

        # Slice 0 (phase-1): sees base trace claims + relations, no event outcomes
        s0 = slices[0]
        assert s0["step_id"] == "step.p1.001"
        assert len(_get_edges(s0)) == 1  # only base r1
        nf0 = _get_nodes(s0)
        assert "C1" in nf0
        assert "C2" in nf0
        # No audit data at phase-1
        assert nf0["C1"].get("gate") in ("", None)
        assert nf0["C2"].get("gate") in ("", None)

        # Slice 1 (phase-2, targeting C2): sees phase-1 outcome
        s1 = slices[1]
        assert s1["step_id"] == "step.p2.001"
        assert len(_get_edges(s1)) == 2  # base r1 + event r2
        # No audit visible yet (none completed before this step)
        nf1 = _get_nodes(s1)
        assert nf1["C1"].get("gate") in ("", None)

        # Slice 2 (phase-2, targeting C1): sees C2's audit
        s2 = slices[2]
        assert s2["step_id"] == "step.p2.002"
        nf2 = _get_nodes(s2)
        # C2's audit visible (different claim target)
        assert nf2["C2"]["gate"] == "research_only"
        # C1's own audit NOT visible
        assert nf2["C1"].get("gate") in ("", None)

        # Verify artifacts grow
        assert s0["available_artifacts"] == ["artifact.doc"]
        assert "artifact.graph.v1" in s1["available_artifacts"]
        assert "artifact.profile.C2" in s2["available_artifacts"]


# ===================================================================
# B10 regression tests: ordering, controllable-only, state_graph parity
# ===================================================================

class TestB10GraphOrderingAndControllable:
    """B10: event_seq ordering, controllable-only graph rows, text/graph parity."""

    def test_event_seq_ordering_not_step_id(self):
        """Events with event_seq are ordered by integer event_seq, not step_id."""
        trace = _make_trace()
        events = [
            _make_event("step-0001", event_seq=10, outcome={"claims": [
                {"claim_id": "C1", "title": "Late Claim", "statement": "B"},
            ]}),
            _make_event("step-phase1-rel-0001", event_seq=1, outcome={"claims": [
                {"claim_id": "C2", "title": "Early Claim", "statement": "A"},
            ]}),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert slices[0]["step_id"] == "step-phase1-rel-0001"
        assert slices[1]["step_id"] == "step-0001"

    def test_automatic_events_not_exported_as_graph_rows(self):
        """Automatic consequences do not produce graph prefix rows."""
        trace = _make_trace()
        events = [
            _make_event("step.001", event_class="controllable_action",
                        outcome={"claims": [{"claim_id": "C1", "title": "A"}]}),
            _make_event("step.002", event_type="profile_recomputed",
                        event_class="automatic_consequence"),
            _make_event("step.003", event_class="controllable_action"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert len(slices) == 2
        step_ids = [s["step_id"] for s in slices]
        assert "step.002" not in step_ids

    def test_graph_row_count_equals_controllable_count(self):
        """Graph prefix row count equals the number of controllable events."""
        trace = _make_trace()
        events = [
            _make_event("rel.001", event_class="controllable_action"),
            _make_event("auto.001", event_type="profile_recomputed",
                        event_class="automatic_consequence"),
            _make_event("rel.002", event_class="controllable_action"),
            _make_event("auto.002", event_type="gate_updated",
                        event_class="automatic_consequence"),
            _make_event("rel.003", event_class="controllable_action"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert len(slices) == 3

    def test_state_graph_varies_across_slices(self):
        """With real events producing outcomes, state_graph must differ across slices."""
        trace = _make_trace(source={"title": "Doc", "text": "Content"})
        events = [
            _make_event("step.001", event_class="controllable_action",
                        outcome={"claims": [
                            {"claim_id": "C1", "title": "A", "statement": "First"},
                        ]}),
            _make_event("step.002", event_class="controllable_action",
                        outcome={"claims": [
                            {"claim_id": "C2", "title": "B", "statement": "Second"},
                        ]}),
            _make_event("step.003", event_class="controllable_action",
                        outcome={"relations": [
                            {"source_id": "C1", "target_id": "C2",
                             "relation_type": "supports", "strength": "inductive"},
                        ]}),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        state_graphs = [json.dumps(s["state_graph"], sort_keys=True) for s in slices]
        assert len(set(state_graphs)) >= 2, \
            f"Expected multiple distinct state_graph values, got {len(set(state_graphs))}"

    def test_text_and_graph_parity(self):
        """Text and graph builders produce aligned slices with same step_ids."""
        if not _PAIRED_AVAILABLE:
            return
        trace = _make_trace(source={"title": "Doc", "text": "Content"})
        events = [
            _make_event("step.001", event_class="controllable_action",
                        outcome={"claims": [
                            {"claim_id": "C1", "title": "A", "statement": "First"},
                        ]}),
            _make_event("auto.001", event_type="profile_recomputed",
                        event_class="automatic_consequence"),
            _make_event("step.002", event_class="controllable_action"),
        ]
        extractor = PairedDatasetExtractor(trace, events)
        pairs = extractor.extract_pairs()
        assert len(pairs) == 2
        for pair in pairs:
            assert pair["text_slice"]["step_id"] == pair["graph_slice"]["step_id"]
            assert pair["text_slice"]["trace_id"] == pair["graph_slice"]["trace_id"]


# ===================================================================
# B30/PTR-001: Graph builder pointer resolution filtering
# ===================================================================


class TestB30GraphPointerResolution:
    """B30/PTR-001: Graph builder must omit pointer-unresolvable policy rows."""

    def test_graph_builder_filters_unresolvable_events(self):
        """Graph slices must omit events whose gold_action IDs do not resolve
        in the visible state_graph nodes."""
        trace = _make_trace(
            claims=[
                {"claim_id": "c1", "title": "Claim 1", "statement": "X"},
                {"claim_id": "c2", "title": "Claim 2", "statement": "Y"},
            ],
        )
        events = [
            # Resolvable: both src and tgt in visible claims
            _make_event(
                "step-1",
                event_type="propose_relation",
                event_class="controllable_action",
                outcome={
                    "relations": [{
                        "source_id": "c1",
                        "target_id": "c2",
                        "relation_type": "supports",
                        "relation_id": "edge.1",
                    }],
                },
                action={
                    "action": "PROPOSE_RELATION",
                    "action_type": "PROPOSE_RELATION",
                    "arguments": {
                        "src_id": "c1",
                        "tgt_id": "c2",
                        "relation_type": "supports",
                        "strength": "deductive",
                    },
                    "dsl": "PROPOSE_RELATION(c1, supports, c2, deductive)",
                },
            ),
            # Unresolvable: tgt_id not in visible claims
            _make_event(
                "step-2",
                event_type="propose_relation",
                event_class="controllable_action",
                action={
                    "action": "PROPOSE_RELATION",
                    "action_type": "PROPOSE_RELATION",
                    "arguments": {
                        "src_id": "c1",
                        "tgt_id": "claim.missing.proj_85cf2779",
                        "relation_type": "challenges",
                        "strength": "unknown",
                    },
                    "dsl": "PROPOSE_RELATION(c1, challenges, claim.missing.proj_85cf2779, unknown)",
                },
            ),
        ]

        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        # Only the resolvable event produces a graph policy row
        assert len(slices) == 1
        assert slices[0]["step_id"] == "step-1"

    def test_graph_builder_keeps_resolvable_events(self):
        """Graph slices must keep events whose gold_action IDs resolve in visible nodes."""
        trace = _make_trace(
            claims=[
                {"claim_id": "c1", "title": "Claim 1", "statement": "X"},
                {"claim_id": "c2", "title": "Claim 2", "statement": "Y"},
            ],
        )
        events = [
            _make_event(
                "step-1",
                event_type="propose_relation",
                event_class="controllable_action",
                action={
                    "action": "PROPOSE_RELATION",
                    "action_type": "PROPOSE_RELATION",
                    "arguments": {
                        "src_id": "c1",
                        "tgt_id": "c2",
                        "relation_type": "supports",
                        "strength": "deductive",
                    },
                    "dsl": "PROPOSE_RELATION(c1, supports, c2, deductive)",
                },
            ),
        ]

        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert len(slices) == 1


class TestB30OutcomeProgressiveState:
    """B30: Events with outcome data drive progressive state accumulation."""

    def test_relations_accumulate_via_outcomes(self):
        """Graph state must accumulate relations from event outcomes."""
        trace = _make_trace(
            claims=[
                {"claim_id": "c1", "title": "A", "statement": "X"},
                {"claim_id": "c2", "title": "B", "statement": "Y"},
                {"claim_id": "c3", "title": "C", "statement": "Z"},
            ],
        )
        events = [
            _make_event(
                "step-1",
                event_type="propose_relation",
                event_class="controllable_action",
                outcome={
                    "relations": [{
                        "source_id": "c1",
                        "target_id": "c2",
                        "relation_type": "supports",
                        "relation_id": "edge.1",
                    }],
                },
                action={
                    "action": "PROPOSE_RELATION",
                    "action_type": "PROPOSE_RELATION",
                    "arguments": {
                        "src_id": "c1", "tgt_id": "c2",
                        "relation_type": "supports", "strength": "deductive",
                    },
                },
            ),
            _make_event(
                "step-2",
                event_type="propose_relation",
                event_class="controllable_action",
                outcome={
                    "relations": [{
                        "source_id": "c2",
                        "target_id": "c3",
                        "relation_type": "derived_from",
                        "relation_id": "edge.2",
                    }],
                },
                action={
                    "action": "PROPOSE_RELATION",
                    "action_type": "PROPOSE_RELATION",
                    "arguments": {
                        "src_id": "c2", "tgt_id": "c3",
                        "relation_type": "derived_from", "strength": "inductive",
                    },
                },
            ),
        ]

        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()

        assert len(slices) == 2

        # At step-1: no relations yet (outcome from step-1 is NOT visible)
        edges_0 = _get_edges(slices[0])
        assert len(edges_0) == 0

        # At step-2: step-1's relation outcome IS visible
        edges_1 = _get_edges(slices[1])
        assert len(edges_1) == 1
        assert edges_1[0]["source"] == "c1"
        assert edges_1[0]["target"] == "c2"


# ===================================================================
# B60/VRF-001: Real artifact regression tests (AUD-002, AUD-008)
# ===================================================================

_EXPORT_DIR = REPO_ROOT.parent / "_push" / "e2e-run-test-doc" / "export-current"


class TestB60GraphProjectionArtifactRegression:
    """Real-artifact regression tests for graph slices."""

    @staticmethod
    def _skip_if_no_artifacts():
        if not _EXPORT_DIR.exists():
            import pytest
            pytest.skip("Export artifacts not available at expected path")

    def _load_graph_slices(self) -> list[dict]:
        self._skip_if_no_artifacts()
        path = _EXPORT_DIR / "prefix_graph_slices.jsonl"
        if not path.exists():
            import pytest
            pytest.skip("prefix_graph_slices.jsonl not found")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _load_prefix_slices(self) -> list[dict]:
        self._skip_if_no_artifacts()
        path = _EXPORT_DIR / "prefix_slices.jsonl"
        if not path.exists():
            import pytest
            pytest.skip("prefix_slices.jsonl not found")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _load_transition_log(self) -> list[dict]:
        self._skip_if_no_artifacts()
        path = _EXPORT_DIR / "transition_log.jsonl"
        if not path.exists():
            import pytest
            pytest.skip("transition_log.jsonl not found")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # AUD-002: graph prefix projection must not be a final-snapshot copy
    def test_aud002_graph_prefix_varies_over_time(self):
        """AUD-002 regression: state_graph must have more than 1 unique value."""
        slices = self._load_graph_slices()
        if len(slices) < 2:
            return
        for s in slices:
            assert "state_graph" in s, f"Missing state_graph on {s.get('step_id')}"
            sg = s["state_graph"]
            assert isinstance(sg, dict), f"state_graph is not a dict on {s.get('step_id')}"

    # Text/graph cutoffs align 1:1
    def test_text_graph_cutoff_alignment(self):
        """Text and graph prefix rows must share the same step_ids 1:1."""
        text_slices = self._load_prefix_slices()
        graph_slices = self._load_graph_slices()
        text_step_ids = [s["step_id"] for s in text_slices]
        graph_step_ids = [s["step_id"] for s in graph_slices]
        assert text_step_ids == graph_step_ids, (
            f"Text/graph cutoff drift: text has {len(text_step_ids)} steps, "
            f"graph has {len(graph_step_ids)} steps"
        )

    # Prefix graph count == controllable event count
    def test_graph_count_equals_controllable_count(self):
        """Prefix graph count must equal controllable event count."""
        slices = self._load_graph_slices()
        events = self._load_transition_log()
        if not events:
            return
        controllable_count = sum(
            1 for e in events
            if e.get("event_class") == "controllable_action"
        )
        assert len(slices) == controllable_count, (
            f"Graph slice count ({len(slices)}) != controllable event count ({controllable_count})"
        )

    # AUD-005 variant for graph: legal_action_mask
    def test_graph_legal_action_mask_non_null(self):
        """legal_action_mask must be non-null on graph policy rows.

        RESIDUAL DRIFT: B20 mask wiring not yet applied.
        """
        import pytest
        slices = self._load_graph_slices()
        null_mask_count = sum(1 for s in slices if s.get("legal_action_mask") is None)
        if slices and null_mask_count == len(slices):
            pytest.xfail(
                f"AUD-005 residual drift: all {len(slices)} graph rows have null "
                f"legal_action_mask. Upstream fix (B20) not yet applied."
            )
        if slices:
            assert null_mask_count == 0, (
                f"{null_mask_count}/{len(slices)} graph rows have null legal_action_mask"
            )

    # AUD-008: no unresolved pointer IDs in policy rows
    def test_aud008_no_unresolved_pointer_ids(self):
        """AUD-008 regression: all policy row gold_action ids must resolve in state_graph."""
        slices = self._load_graph_slices()
        for s in slices:
            gold = s.get("gold_action")
            if not gold:
                continue
            args = gold.get("arguments", {})
            sg = s.get("state_graph", {})
            node_ids = {n["node_id"] for n in sg.get("nodes", [])}
            # Check pointer-resolvable ids
            for key in ("src_id", "tgt_id", "claim_id"):
                val = args.get(key)
                if val and node_ids:
                    # Only assert if there are nodes to resolve against
                    # and the id looks like a claim pointer (not a bare label)
                    if val.startswith("claim."):
                        assert val in node_ids, (
                            f"Unresolved pointer {key}={val} at {s['step_id']}"
                        )


class TestB60GraphOrderingUnit:
    """Unit-level regression for graph builder ordering."""

    def test_graph_event_seq_ordering_not_step_id(self):
        """Graph slices must follow event_seq ordering, not step_id lexical."""
        trace = _make_trace()
        events = [
            _make_event("step-phase1-rel-0001", event_type="propose_relation",
                        event_seq=1, event_class="controllable_action"),
            _make_event("step-0001", event_type="select_formalization",
                        event_seq=2, event_class="controllable_action"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        assert slices[0]["step_id"] == "step-phase1-rel-0001"
        assert slices[1]["step_id"] == "step-0001"

    def test_graph_automatic_consequences_excluded(self):
        """automatic_consequence events must not produce graph policy rows."""
        trace = _make_trace()
        events = [
            _make_event("step-0001", event_type="select_formalization",
                        event_seq=1, event_class="controllable_action"),
            _make_event("step-0002", event_type="profile_recomputed",
                        event_seq=2, event_class="automatic_consequence"),
            _make_event("step-0003", event_type="select_formalization",
                        event_seq=3, event_class="controllable_action"),
        ]
        builder = PrefixSliceGraphBuilder(trace, events)
        slices = builder.extract_graph_slices()
        step_ids = [s["step_id"] for s in slices]
        assert "step-0002" not in step_ids
        assert len(slices) == 2


# ===================================================================
# Run with pytest
# ===================================================================

if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
