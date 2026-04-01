"""Conformance tests for PipelineTraceV1, PipelineEventV1, TraceSidecarMeta, PrefixSliceV1."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "packages" / "contracts" / "schemas"
PYTHON_BINDINGS_SRC = ROOT / "packages" / "contracts-py" / "src"

if str(PYTHON_BINDINGS_SRC) not in sys.path:
    sys.path.insert(0, str(PYTHON_BINDINGS_SRC))

from formal_claim_contracts import (  # noqa: E402
    PipelineTraceV1,
    PipelineEventV1,
    TraceSidecarMeta,
    PrefixSliceV1,
)


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def schema_errors(schema: dict, data: dict) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return sorted(error.message for error in validator.iter_errors(data))


# ---------------------------------------------------------------------------
# Minimal valid fixtures
# ---------------------------------------------------------------------------

VALID_PIPELINE_TRACE = {
    "schema_version": "1.0.0",
    "meta": {
        "trace_id": "trace-001",
        "schema_version": "1.0.0",
        "oae_commit": "abc123",
        "audit_rules_version": "1.0.0",
        "promotion_fsm_version": "1.0.0",
        "verifier_versions": {"isabelle": "2024"},
        "source_sha256": "deadbeef",
    },
    "source": {
        "source_text": "All models converge in finite time.",
        "source_units": [
            {"unit_id": "u1", "start_char": 0, "end_char": 35, "text": "All models converge in finite time."}
        ],
    },
    "phase1": {
        "claim_graph": {"placeholder": True},
        "structuring_workflow": {
            "planner_action": "decompose",
            "attempts": [{}],
            "validation_errors": [],
        },
        "candidate_ledger": {
            "claims_proposed": ["c1"],
            "claims_accepted": ["c1"],
            "claims_rejected": [],
            "relations_proposed": [],
            "relations_accepted": [],
            "relations_rejected": [],
        },
    },
    "phase2": {
        "per_claim": {
            "c1": {
                "dual_formalization": {
                    "attempt_a": {
                        "output": "lemma conv: ...",
                        "sha256": "aaa",
                        "assumptions": ["bounded"],
                        "back_translation": "All bounded models converge.",
                    },
                    "attempt_b": {
                        "output": "lemma conv2: ...",
                        "sha256": "bbb",
                        "assumptions": ["bounded"],
                        "back_translation": "All bounded models converge.",
                    },
                    "divergence": {
                        "classification": "equivalent",
                        "primary_target_match": True,
                        "back_translation_match": True,
                        "code_sha_match": False,
                        "assumptions_only_in_a": [],
                        "assumptions_only_in_b": [],
                    },
                },
                "build_results": {"status": "pass"},
                "verifier_results": {"isabelle": "pass"},
                "audit": {
                    "trust_frontier": {},
                    "model_health": {},
                    "intent_alignment": {},
                    "blocking_issues": [],
                },
                "profile": {"placeholder": True},
                "promotion_transitions": [],
            }
        },
        "phase2_flags": {
            "phase2_executed": True,
            "certification_eligible": True,
        },
    },
    "phase3": {
        "evidence": {},
    },
    "trace_results": {
        "forward_traces": {},
        "backward_traces": {},
        "gap_analysis": {},
        "soundness": {
            "score": 0.95,
            "method": "weighted_average",
            "notes": [],
        },
    },
}

VALID_PIPELINE_EVENT = {
    "schema_version": "1.0.0",
    "trace_id": "trace-001",
    "step_id": "step-001",
    "phase": "phase1",
    "event_type": "propose_relation",
    "actor": "planner",
    "before_hash": "aaa",
    "after_hash": "bbb",
    "proposal": {"relation": "depends_on"},
    "accepted": True,
    "reject_reason": None,
    "changed_ids": ["rel-001"],
    "verifier_delta": {
        "legality": True,
        "profile_recomputed": False,
    },
}

VALID_TRACE_SIDECAR_META = {
    "schema_version": "1.0.0",
    "trace_id": "trace-001",
    "source_domain": "mathematics",
    "source_uri": "https://arxiv.org/abs/1234.5678",
    "corpus_name": "oae-bench-v1",
    "license": "CC-BY-4.0",
    "split": "train",
    "notes": {"annotator": "human"},
}

VALID_PREFIX_SLICE = {
    "schema_version": "1.0.0",
    "trace_id": "trace-001",
    "step_id": "step-003",
    "state_text": "claim_graph={c1: proposed}, phase=phase1",
    "available_artifacts": ["claim-graph.json"],
    "legal_action_mask": ["propose_relation", "add_hidden_assumption"],
    "gold_action": {"action": "propose_relation", "target": "c1"},
}


# =========================================================================
# Positive tests -- JSON Schema validation
# =========================================================================

class TestPipelineTraceSchema:
    schema = load_schema("pipeline-trace.schema.json")

    def test_valid_trace_passes_schema(self):
        errors = schema_errors(self.schema, VALID_PIPELINE_TRACE)
        assert errors == [], f"Valid trace fixture failed: {errors}"

    def test_valid_trace_roundtrips_through_pydantic(self):
        instance = PipelineTraceV1.model_validate(VALID_PIPELINE_TRACE)
        data = instance.model_dump(mode="json", exclude_none=True)
        errors = schema_errors(self.schema, data)
        assert errors == [], f"Round-trip failed: {errors}"


class TestPipelineEventSchema:
    schema = load_schema("pipeline-event.schema.json")

    def test_valid_event_passes_schema(self):
        errors = schema_errors(self.schema, VALID_PIPELINE_EVENT)
        assert errors == [], f"Valid event fixture failed: {errors}"

    def test_valid_event_roundtrips_through_pydantic(self):
        instance = PipelineEventV1.model_validate(VALID_PIPELINE_EVENT)
        data = instance.model_dump(mode="json", exclude_none=True)
        errors = schema_errors(self.schema, data)
        assert errors == [], f"Round-trip failed: {errors}"


class TestTraceSidecarMetaSchema:
    schema = load_schema("trace-sidecar-meta.schema.json")

    def test_valid_sidecar_passes_schema(self):
        errors = schema_errors(self.schema, VALID_TRACE_SIDECAR_META)
        assert errors == [], f"Valid sidecar fixture failed: {errors}"

    def test_valid_sidecar_roundtrips_through_pydantic(self):
        instance = TraceSidecarMeta.model_validate(VALID_TRACE_SIDECAR_META)
        data = instance.model_dump(mode="json", exclude_none=True)
        errors = schema_errors(self.schema, data)
        assert errors == [], f"Round-trip failed: {errors}"


class TestPrefixSliceSchema:
    schema = load_schema("prefix-slice.schema.json")

    def test_valid_slice_passes_schema(self):
        errors = schema_errors(self.schema, VALID_PREFIX_SLICE)
        assert errors == [], f"Valid slice fixture failed: {errors}"

    def test_valid_slice_roundtrips_through_pydantic(self):
        instance = PrefixSliceV1.model_validate(VALID_PREFIX_SLICE)
        data = instance.model_dump(mode="json", exclude_none=True)
        errors = schema_errors(self.schema, data)
        assert errors == [], f"Round-trip failed: {errors}"


# =========================================================================
# Negative tests
# =========================================================================

class TestPipelineTraceNegative:
    schema = load_schema("pipeline-trace.schema.json")

    def test_trace_with_source_domain_fails_schema(self):
        """trace.json must NOT contain source_domain -- that belongs in TraceSidecarMeta."""
        bad = copy.deepcopy(VALID_PIPELINE_TRACE)
        bad["source_domain"] = "mathematics"
        errors = schema_errors(self.schema, bad)
        assert errors, "trace.json with source_domain should fail validation"

    def test_trace_with_source_domain_fails_pydantic(self):
        bad = copy.deepcopy(VALID_PIPELINE_TRACE)
        bad["source_domain"] = "mathematics"
        with pytest.raises(Exception):
            PipelineTraceV1.model_validate(bad)

    def test_trace_missing_meta_fails(self):
        bad = copy.deepcopy(VALID_PIPELINE_TRACE)
        del bad["meta"]
        errors = schema_errors(self.schema, bad)
        assert errors, "trace without meta should fail"


class TestPipelineEventNegative:
    schema = load_schema("pipeline-event.schema.json")

    def test_event_rejected_without_reason_fails_pydantic(self):
        """When accepted=false, reject_reason is required."""
        bad = copy.deepcopy(VALID_PIPELINE_EVENT)
        bad["accepted"] = False
        bad["reject_reason"] = None
        with pytest.raises(Exception):
            PipelineEventV1.model_validate(bad)

    def test_event_missing_phase_fails_schema(self):
        bad = copy.deepcopy(VALID_PIPELINE_EVENT)
        del bad["phase"]
        errors = schema_errors(self.schema, bad)
        assert errors, "event without phase should fail"

    def test_event_invalid_phase_fails_schema(self):
        bad = copy.deepcopy(VALID_PIPELINE_EVENT)
        bad["phase"] = "phase99"
        errors = schema_errors(self.schema, bad)
        assert errors, "event with invalid phase should fail"

    def test_event_invalid_actor_fails_schema(self):
        bad = copy.deepcopy(VALID_PIPELINE_EVENT)
        bad["actor"] = "unknown_actor"
        errors = schema_errors(self.schema, bad)
        assert errors, "event with invalid actor should fail"


class TestTraceSidecarMetaNegative:
    schema = load_schema("trace-sidecar-meta.schema.json")

    def test_sidecar_missing_source_domain_fails(self):
        bad = copy.deepcopy(VALID_TRACE_SIDECAR_META)
        del bad["source_domain"]
        errors = schema_errors(self.schema, bad)
        assert errors, "sidecar without source_domain should fail"

    def test_sidecar_extra_field_fails(self):
        bad = copy.deepcopy(VALID_TRACE_SIDECAR_META)
        bad["unknown_field"] = "value"
        errors = schema_errors(self.schema, bad)
        assert errors, "sidecar with unknown field should fail"


class TestPrefixSliceNegative:
    schema = load_schema("prefix-slice.schema.json")

    def test_slice_with_source_domain_in_state_text_fails_pydantic(self):
        """state_text must not contain domain references."""
        bad = copy.deepcopy(VALID_PREFIX_SLICE)
        bad["state_text"] = 'source_domain="mathematics", claims=...'
        with pytest.raises(Exception):
            PrefixSliceV1.model_validate(bad)

    def test_slice_with_future_data_fields_fails_schema(self):
        """additionalProperties: false means future/unknown fields must be rejected."""
        bad = copy.deepcopy(VALID_PREFIX_SLICE)
        bad["future_field"] = "some_value"
        errors = schema_errors(self.schema, bad)
        assert errors, "slice with future data fields should fail"

    def test_slice_with_future_data_fields_fails_pydantic(self):
        bad = copy.deepcopy(VALID_PREFIX_SLICE)
        bad["future_field"] = "some_value"
        with pytest.raises(Exception):
            PrefixSliceV1.model_validate(bad)

    def test_slice_missing_state_text_fails(self):
        bad = copy.deepcopy(VALID_PREFIX_SLICE)
        del bad["state_text"]
        errors = schema_errors(self.schema, bad)
        assert errors, "slice without state_text should fail"


# =========================================================================
# Import smoke test
# =========================================================================

class TestSingleEntryPointImport:
    def test_all_schemas_importable_from_package(self):
        """All 4 new schemas must be importable from formal_claim_contracts."""
        from formal_claim_contracts import (
            PipelineTraceV1,
            PipelineEventV1,
            TraceSidecarMeta,
            PrefixSliceV1,
        )
        assert PipelineTraceV1 is not None
        assert PipelineEventV1 is not None
        assert TraceSidecarMeta is not None
        assert PrefixSliceV1 is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
