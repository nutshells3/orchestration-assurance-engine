"""Tests for Sub-bundle 05a: API-001, API-002, API-003 trace export surfaces.

Covers:
- CLI export-trace produces 3 files
- CLI export-prefix produces JSONL output
- MCP trace.export tool returns correct paths
- MCP trace:// resource returns model-safe JSON (no source_domain)
- MCP sidecar:// resource is separate from trace
- Generated bindings can decode exported files
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixture: resolve paths
# ---------------------------------------------------------------------------

def _resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "packages" / "contracts" / "schemas").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root.")


REPO_ROOT = _resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"
CONTRACTS_PY_SRC = REPO_ROOT / "packages" / "contracts-py" / "src"
MCP_SERVER_SRC = REPO_ROOT / "services" / "mcp-server" / "src"

for src_dir in [ENGINE_SRC, CONTRACTS_PY_SRC, MCP_SERVER_SRC]:
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

MOCK_PROJECT_ID = "project.test.trace"
MOCK_DOMAIN = "mathematics"

MOCK_CLAIM_GRAPH: dict[str, Any] = {
    "graph_id": "cg.test.1",
    "claims": [
        {"claim_id": "claim.1", "title": "Test Claim", "statement": "X holds"},
        {"claim_id": "claim.2", "title": "Test Claim 2", "statement": "Y holds"},
    ],
    "relations": [],
}

MOCK_REVIEW_EVENTS: dict[str, list[dict[str, Any]]] = {
    "claim.1": [
        {
            "event_type": "audit_workflow",
            "artifact_id": "audit.claim.1",
            "actor": "engine_api",
            "actor_role": "system",
            "timestamp": "2026-01-15T10:00:00Z",
            "notes": "Initial audit",
        },
    ],
    "claim.2": [
        {
            "event_type": "claim_analysis",
            "artifact_id": "analysis.claim.2",
            "actor": "engine_api",
            "actor_role": "system",
            "timestamp": "2026-01-15T11:00:00Z",
            "notes": "Best-effort analysis",
        },
    ],
}

MOCK_ASSURANCE_PROFILES: list[dict[str, Any]] = [
    {"profile_id": "profile.1", "claim_id": "claim.1", "gate": "draft"},
]

MOCK_PROMOTION_STATES: dict[str, dict[str, Any]] = {
    "claim.1": {"claim_id": "claim.1", "current_gate": "draft"},
}


# ---------------------------------------------------------------------------
# Test: trace_export module
# ---------------------------------------------------------------------------

class TestTraceExportModule:
    """Direct tests for the trace_export.py module."""

    def test_trace_export_builder_strips_source_domain(self):
        from formal_claim_engine.trace_export import TraceExportBuilder

        builder = TraceExportBuilder()
        trace = builder.build_trace(
            project_id=MOCK_PROJECT_ID,
            claim_graph={**MOCK_CLAIM_GRAPH, "source_domain": "should_be_stripped"},
            assurance_profiles=MOCK_ASSURANCE_PROFILES,
            review_events=MOCK_REVIEW_EVENTS,
            promotion_states=MOCK_PROMOTION_STATES,
        )
        raw = json.dumps(trace)
        assert "source_domain" not in raw, "trace must NOT contain source_domain"
        assert trace["schema"] == "PipelineTraceV1"
        assert trace["project_id"] == MOCK_PROJECT_ID

    def test_transition_log_writer_produces_sorted_entries(self):
        from formal_claim_engine.trace_export import TransitionLogWriter

        writer = TransitionLogWriter()
        lines = writer.build_lines(MOCK_REVIEW_EVENTS)
        assert len(lines) == 2
        # Should be chronologically sorted
        assert lines[0]["timestamp"] <= lines[1]["timestamp"]
        assert lines[0]["claim_id"] == "claim.1"
        assert lines[1]["claim_id"] == "claim.2"

    def test_sidecar_meta_contains_source_domain(self):
        from formal_claim_engine.trace_export import SidecarMetaWriter

        writer = SidecarMetaWriter()
        sidecar = writer.build_sidecar(
            project_id=MOCK_PROJECT_ID,
            source_domain=MOCK_DOMAIN,
        )
        assert sidecar["source_domain"] == MOCK_DOMAIN
        assert sidecar["schema"] == "SidecarMetaV1"
        assert "OPERATOR-ONLY" in sidecar["warning"]

    def test_export_trace_to_dir_produces_three_files(self):
        from formal_claim_engine.trace_export import export_trace_to_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            result = export_trace_to_dir(
                output_dir=Path(tmpdir),
                project_id=MOCK_PROJECT_ID,
                claim_graph=MOCK_CLAIM_GRAPH,
                assurance_profiles=MOCK_ASSURANCE_PROFILES,
                review_events=MOCK_REVIEW_EVENTS,
                promotion_states=MOCK_PROMOTION_STATES,
                source_domain=MOCK_DOMAIN,
            )
            assert result.trace_path.exists()
            assert result.transition_log_path.exists()
            assert result.sidecar_path.exists()
            assert result.validation_ok is True

            # Verify trace does not contain source_domain
            trace_raw = result.trace_path.read_text()
            assert "source_domain" not in trace_raw

            # Verify sidecar does contain source_domain
            sidecar_raw = json.loads(result.sidecar_path.read_text())
            assert sidecar_raw["source_domain"] == MOCK_DOMAIN


# ---------------------------------------------------------------------------
# Test: prefix_slice_builder module
# ---------------------------------------------------------------------------

class TestPrefixSliceBuilder:
    """Direct tests for the prefix_slice_builder.py module."""

    def test_extract_slices_from_events(self):
        from formal_claim_engine.prefix_slice_builder import PrefixSliceBuilder

        builder = PrefixSliceBuilder()
        trace_data = {"claim_graph": MOCK_CLAIM_GRAPH}
        slices = builder.extract_slices(
            trace_data=trace_data,
            review_events=MOCK_REVIEW_EVENTS,
        )
        assert len(slices) == 2
        assert slices[0].claim_count == 1
        assert slices[1].claim_count == 2

    def test_to_jsonl_output(self):
        from formal_claim_engine.prefix_slice_builder import PrefixSliceBuilder

        builder = PrefixSliceBuilder()
        trace_data = {"claim_graph": MOCK_CLAIM_GRAPH}
        slices = builder.extract_slices(
            trace_data=trace_data,
            review_events=MOCK_REVIEW_EVENTS,
        )
        jsonl = builder.to_jsonl(slices)
        lines = jsonl.strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert "slice_index" in parsed
            assert "claim_ids" in parsed

    def test_write_jsonl_file(self):
        from formal_claim_engine.prefix_slice_builder import PrefixSliceBuilder

        builder = PrefixSliceBuilder()
        trace_data = {"claim_graph": MOCK_CLAIM_GRAPH}
        slices = builder.extract_slices(
            trace_data=trace_data,
            review_events=MOCK_REVIEW_EVENTS,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefix_slices.jsonl"
            count = builder.write_to_file(slices, path, format="jsonl")
            assert count == 2
            assert path.exists()

    def test_write_json_file(self):
        from formal_claim_engine.prefix_slice_builder import PrefixSliceBuilder

        builder = PrefixSliceBuilder()
        trace_data = {"claim_graph": MOCK_CLAIM_GRAPH}
        slices = builder.extract_slices(
            trace_data=trace_data,
            review_events=MOCK_REVIEW_EVENTS,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefix_slices.json"
            count = builder.write_to_file(slices, path, format="json")
            assert count == 2
            parsed = json.loads(path.read_text())
            assert isinstance(parsed, list)
            assert len(parsed) == 2


# ---------------------------------------------------------------------------
# Test: model_safe_serializer
# ---------------------------------------------------------------------------

class TestModelSafeSerializer:
    """Tests for the model-safe serializer boundary enforcement."""

    def test_strips_source_domain(self):
        from formal_claim_engine.model_safe_serializer import ModelSafeSerializer

        serializer = ModelSafeSerializer()
        data = {
            "project_id": "test",
            "source_domain": "should_be_stripped",
            "nested": {"source_domain": "also_stripped", "keep": "this"},
        }
        result = serializer.sanitize(data)
        assert "source_domain" not in result
        assert "source_domain" not in result["nested"]
        assert result["nested"]["keep"] == "this"

    def test_strips_operator_notes(self):
        from formal_claim_engine.model_safe_serializer import ModelSafeSerializer

        serializer = ModelSafeSerializer()
        data = {"operator_notes": "secret", "claim_id": "keep"}
        result = serializer.sanitize(data)
        assert "operator_notes" not in result
        assert result["claim_id"] == "keep"

    def test_does_not_modify_original(self):
        from formal_claim_engine.model_safe_serializer import ModelSafeSerializer

        serializer = ModelSafeSerializer()
        data = {"source_domain": "math", "keep": "this"}
        result = serializer.sanitize(data)
        assert "source_domain" in data  # original unchanged
        assert "source_domain" not in result


# ---------------------------------------------------------------------------
# Test: engine API export_trace / export_prefix_slices
# ---------------------------------------------------------------------------

class TestEngineAPITraceExport:
    """Tests for FormalClaimEngineAPI.export_trace and export_prefix_slices."""

    def _mock_engine_api(self, tmpdir: str):
        """Create a mock engine API with stubbed internals."""
        from formal_claim_engine.engine_api import FormalClaimEngineAPI

        api = MagicMock(spec=FormalClaimEngineAPI)

        # Wire through the real methods
        from formal_claim_engine.engine_api import (
            TraceExportResult,
            PrefixSliceRunResult,
        )
        from formal_claim_engine.phase_assembler import PhaseAssembler
        from formal_claim_engine.config import PipelineConfig

        config = PipelineConfig(data_dir=tmpdir)

        # Mock _collect_bundle_data
        bundle_data = {
            "claim_graph": MOCK_CLAIM_GRAPH,
            "assurance_profiles": MOCK_ASSURANCE_PROFILES,
            "review_events": MOCK_REVIEW_EVENTS,
            "promotion_states": MOCK_PROMOTION_STATES,
            "evaluation_evidence": [],
        }

        mock_project = MagicMock()
        mock_project.domain = MOCK_DOMAIN

        api.config = config
        api._collect_bundle_data = MagicMock(return_value=bundle_data)
        api.open_project = MagicMock(return_value=mock_project)

        # Bind real methods
        api.export_trace = FormalClaimEngineAPI.export_trace.__get__(api)
        api.export_prefix_slices = FormalClaimEngineAPI.export_prefix_slices.__get__(api)
        api.get_trace_data = FormalClaimEngineAPI.get_trace_data.__get__(api)
        api.get_transition_log = FormalClaimEngineAPI.get_transition_log.__get__(api)
        api.get_sidecar_meta = FormalClaimEngineAPI.get_sidecar_meta.__get__(api)

        return api

    def test_export_trace_produces_three_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = self._mock_engine_api(tmpdir)
            output_dir = str(Path(tmpdir) / "export_output")
            result = api.export_trace(MOCK_PROJECT_ID, output_dir)

            assert Path(result.trace_path).exists()
            assert Path(result.transition_log_path).exists()
            assert Path(result.sidecar_meta_path).exists()
            assert result.validation_ok is True
            assert result.project_id == MOCK_PROJECT_ID
            assert result.export_version == "v2"

    def test_export_prefix_produces_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = self._mock_engine_api(tmpdir)
            output_path = str(Path(tmpdir) / "slices.jsonl")
            result = api.export_prefix_slices(MOCK_PROJECT_ID, output_path)

            assert result.slice_count > 0
            assert result.output_path == output_path
            assert Path(output_path).exists()

    def test_get_trace_data_is_model_safe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = self._mock_engine_api(tmpdir)
            trace = api.get_trace_data(MOCK_PROJECT_ID)

            raw = json.dumps(trace)
            assert "source_domain" not in raw
            assert trace["schema"] == "PipelineTraceV1"

    def test_get_transition_log_returns_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = self._mock_engine_api(tmpdir)
            log_content = api.get_transition_log(MOCK_PROJECT_ID)

            lines = log_content.strip().splitlines()
            assert len(lines) == 2
            for line in lines:
                parsed = json.loads(line)
                assert "claim_id" in parsed

    def test_get_sidecar_meta_is_operator_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            api = self._mock_engine_api(tmpdir)
            sidecar = api.get_sidecar_meta(MOCK_PROJECT_ID)

            assert sidecar["source_domain"] == MOCK_DOMAIN
            assert "OPERATOR-ONLY" in sidecar["warning"]

    def test_sidecar_separate_from_trace(self):
        """Verify sidecar contains fields that trace does NOT."""
        with tempfile.TemporaryDirectory() as tmpdir:
            api = self._mock_engine_api(tmpdir)
            trace = api.get_trace_data(MOCK_PROJECT_ID)
            sidecar = api.get_sidecar_meta(MOCK_PROJECT_ID)

            trace_raw = json.dumps(trace)
            assert "source_domain" not in trace_raw
            assert "source_domain" in sidecar


# ---------------------------------------------------------------------------
# Test: MCP tool and resource surfaces
# ---------------------------------------------------------------------------

class TestMCPTraceSurface:
    """Tests for MCP trace.export tool and trace:// resource."""

    def _mock_engine_api(self, tmpdir: str):
        """Same mock as engine API tests."""
        from formal_claim_engine.engine_api import FormalClaimEngineAPI
        from formal_claim_engine.config import PipelineConfig

        api = MagicMock(spec=FormalClaimEngineAPI)
        config = PipelineConfig(data_dir=tmpdir)

        bundle_data = {
            "claim_graph": MOCK_CLAIM_GRAPH,
            "assurance_profiles": MOCK_ASSURANCE_PROFILES,
            "review_events": MOCK_REVIEW_EVENTS,
            "promotion_states": MOCK_PROMOTION_STATES,
            "evaluation_evidence": [],
        }

        mock_project = MagicMock()
        mock_project.domain = MOCK_DOMAIN

        api.config = config
        api._collect_bundle_data = MagicMock(return_value=bundle_data)
        api.open_project = MagicMock(return_value=mock_project)

        api.export_trace = FormalClaimEngineAPI.export_trace.__get__(api)
        api.export_prefix_slices = FormalClaimEngineAPI.export_prefix_slices.__get__(api)
        api.get_trace_data = FormalClaimEngineAPI.get_trace_data.__get__(api)
        api.get_transition_log = FormalClaimEngineAPI.get_transition_log.__get__(api)
        api.get_sidecar_meta = FormalClaimEngineAPI.get_sidecar_meta.__get__(api)

        return api

    def test_trace_export_tool_returns_paths(self):
        from formal_claim_mcp_server.server import (
            _tool_ok,
            _tool_error,
            _map_exception,
            _request_id,
            runtime,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            old_api = runtime.engine_api
            try:
                runtime.engine_api = self._mock_engine_api(tmpdir)
                from formal_claim_mcp_server.server import tool_trace_export

                output_dir = str(Path(tmpdir) / "mcp_export")
                result = tool_trace_export(MOCK_PROJECT_ID, output_dir)
                assert result["ok"] is True
                export_data = result["data"]["export"]
                assert "trace_path" in export_data
                assert "transition_log_path" in export_data
                assert "sidecar_meta_path" in export_data
                assert export_data.get("export_version") == "v2"

                resource_refs = result["data"]["resource_refs"]
                assert f"trace://{MOCK_PROJECT_ID}" in resource_refs
                assert f"transition-log://{MOCK_PROJECT_ID}" in resource_refs
                assert f"sidecar://{MOCK_PROJECT_ID}" in resource_refs
            finally:
                runtime.engine_api = old_api

    def test_trace_resource_returns_model_safe_json(self):
        from formal_claim_mcp_server.server import runtime

        with tempfile.TemporaryDirectory() as tmpdir:
            old_api = runtime.engine_api
            try:
                runtime.engine_api = self._mock_engine_api(tmpdir)
                from formal_claim_mcp_server.server import resource_trace

                result = resource_trace(MOCK_PROJECT_ID)
                assert result["uri"] == f"trace://{MOCK_PROJECT_ID}"
                assert result["meta"]["model_safe"] is True
                payload = result["payload"]
                raw = json.dumps(payload)
                assert "source_domain" not in raw
            finally:
                runtime.engine_api = old_api

    def test_sidecar_resource_is_operator_only(self):
        from formal_claim_mcp_server.server import runtime

        with tempfile.TemporaryDirectory() as tmpdir:
            old_api = runtime.engine_api
            try:
                runtime.engine_api = self._mock_engine_api(tmpdir)
                from formal_claim_mcp_server.server import resource_sidecar

                result = resource_sidecar(MOCK_PROJECT_ID)
                assert result["uri"] == f"sidecar://{MOCK_PROJECT_ID}"
                assert result["meta"]["operator_only"] is True
                assert "OPERATOR-ONLY" in result["meta"]["warning"]
                payload = result["payload"]
                assert payload["source_domain"] == MOCK_DOMAIN
            finally:
                runtime.engine_api = old_api

    def test_transition_log_resource(self):
        from formal_claim_mcp_server.server import runtime

        with tempfile.TemporaryDirectory() as tmpdir:
            old_api = runtime.engine_api
            try:
                runtime.engine_api = self._mock_engine_api(tmpdir)
                from formal_claim_mcp_server.server import resource_transition_log

                result = resource_transition_log(MOCK_PROJECT_ID)
                assert result["uri"] == f"transition-log://{MOCK_PROJECT_ID}"
                content = result["payload"]["content"]
                lines = content.strip().splitlines()
                assert len(lines) == 2
            finally:
                runtime.engine_api = old_api


# ---------------------------------------------------------------------------
# Test: Generated bindings decode exported files
# ---------------------------------------------------------------------------

class TestBindingDecoder:
    """Tests that generated bindings + decoder can validate exported artifacts."""

    def test_decode_trace_json(self):
        from formal_claim_engine.trace_export import export_trace_to_dir
        from formal_claim_contracts.trace_decoder import decode_trace_json

        with tempfile.TemporaryDirectory() as tmpdir:
            result = export_trace_to_dir(
                output_dir=Path(tmpdir),
                project_id=MOCK_PROJECT_ID,
                claim_graph=MOCK_CLAIM_GRAPH,
                assurance_profiles=MOCK_ASSURANCE_PROFILES,
                review_events=MOCK_REVIEW_EVENTS,
                promotion_states=MOCK_PROMOTION_STATES,
                source_domain=MOCK_DOMAIN,
            )
            decode_result = decode_trace_json(result.trace_path)
            assert decode_result.valid, f"Decode errors: {decode_result.errors}"

    def test_decode_transition_log(self):
        from formal_claim_engine.trace_export import export_trace_to_dir
        from formal_claim_contracts.trace_decoder import decode_transition_log

        with tempfile.TemporaryDirectory() as tmpdir:
            result = export_trace_to_dir(
                output_dir=Path(tmpdir),
                project_id=MOCK_PROJECT_ID,
                claim_graph=MOCK_CLAIM_GRAPH,
                assurance_profiles=MOCK_ASSURANCE_PROFILES,
                review_events=MOCK_REVIEW_EVENTS,
                promotion_states=MOCK_PROMOTION_STATES,
                source_domain=MOCK_DOMAIN,
            )
            decode_result = decode_transition_log(result.transition_log_path)
            assert decode_result.valid, f"Decode errors: {decode_result.errors}"
            assert len(decode_result.data) == 2

    def test_decode_sidecar_meta(self):
        from formal_claim_engine.trace_export import export_trace_to_dir
        from formal_claim_contracts.trace_decoder import decode_sidecar_meta

        with tempfile.TemporaryDirectory() as tmpdir:
            result = export_trace_to_dir(
                output_dir=Path(tmpdir),
                project_id=MOCK_PROJECT_ID,
                claim_graph=MOCK_CLAIM_GRAPH,
                assurance_profiles=MOCK_ASSURANCE_PROFILES,
                review_events=MOCK_REVIEW_EVENTS,
                promotion_states=MOCK_PROMOTION_STATES,
                source_domain=MOCK_DOMAIN,
            )
            decode_result = decode_sidecar_meta(result.sidecar_path)
            assert decode_result.valid, f"Decode errors: {decode_result.errors}"

    def test_decode_prefix_slices_jsonl(self):
        from formal_claim_engine.prefix_slice_builder import PrefixSliceBuilder
        from formal_claim_contracts.trace_decoder import decode_prefix_slices

        builder = PrefixSliceBuilder()
        slices = builder.extract_slices(
            trace_data={"claim_graph": MOCK_CLAIM_GRAPH},
            review_events=MOCK_REVIEW_EVENTS,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefix_slices.jsonl"
            builder.write_to_file(slices, path, format="jsonl")
            decode_result = decode_prefix_slices(path, format="jsonl")
            assert decode_result.valid, f"Decode errors: {decode_result.errors}"
            assert len(decode_result.data) == 2

    def test_decode_prefix_slices_json(self):
        from formal_claim_engine.prefix_slice_builder import PrefixSliceBuilder
        from formal_claim_contracts.trace_decoder import decode_prefix_slices

        builder = PrefixSliceBuilder()
        slices = builder.extract_slices(
            trace_data={"claim_graph": MOCK_CLAIM_GRAPH},
            review_events=MOCK_REVIEW_EVENTS,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "prefix_slices.json"
            builder.write_to_file(slices, path, format="json")
            decode_result = decode_prefix_slices(path, format="json")
            assert decode_result.valid, f"Decode errors: {decode_result.errors}"
            assert len(decode_result.data) == 2


# ===================================================================
# B60/VRF-001: Real artifact surface-level regression tests
# ===================================================================

_EXPORT_DIR = Path(__file__).resolve().parents[2].parent / "_push" / "e2e-run-test-doc" / "export-current"


class TestB60SurfaceArtifactRegression:
    """B60 real-artifact surface parity tests."""

    @staticmethod
    def _skip_if_no_artifacts():
        if not _EXPORT_DIR.exists():
            import pytest
            pytest.skip("Export artifacts not available at expected path")

    def test_three_file_export_exists(self):
        """Surface parity: all 3 canonical export files must exist."""
        self._skip_if_no_artifacts()
        for name in ("trace.json", "transition_log.jsonl", "sidecar_meta.json"):
            path = _EXPORT_DIR / name
            assert path.exists(), f"Missing export file: {name}"

    def test_prefix_files_exist(self):
        """Surface parity: both prefix files must exist."""
        self._skip_if_no_artifacts()
        for name in ("prefix_slices.jsonl", "prefix_graph_slices.jsonl"):
            path = _EXPORT_DIR / name
            assert path.exists(), f"Missing prefix file: {name}"

    def test_sidecar_contains_source_domain(self):
        """Sidecar must contain source_domain."""
        self._skip_if_no_artifacts()
        sidecar = json.loads((_EXPORT_DIR / "sidecar_meta.json").read_text(encoding="utf-8"))
        assert "source_domain" in sidecar, "sidecar_meta missing source_domain"

    def test_trace_json_no_source_domain(self):
        """trace.json must not contain source_domain (model-safe)."""
        self._skip_if_no_artifacts()
        raw = (_EXPORT_DIR / "trace.json").read_text(encoding="utf-8")
        assert "source_domain" not in raw, "source_domain leaked into trace.json"

    def test_prefix_text_graph_row_count_parity(self):
        """Text and graph prefix files must have the same row count."""
        self._skip_if_no_artifacts()
        text_lines = [l for l in (_EXPORT_DIR / "prefix_slices.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        graph_lines = [l for l in (_EXPORT_DIR / "prefix_graph_slices.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(text_lines) == len(graph_lines), (
            f"Row count mismatch: text={len(text_lines)}, graph={len(graph_lines)}"
        )
