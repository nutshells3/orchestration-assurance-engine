"""Versioned response schema registry for engine agents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import jsonschema


def _normalize_role(role: str) -> str:
    if role.startswith("formalizer_"):
        return "formalizer"
    return role


@dataclass(frozen=True)
class ResponseSchemaSpec:
    schema_id: str
    version: str
    schema: dict[str, Any]


RESPONSE_SCHEMAS: dict[str, ResponseSchemaSpec] = {
    "planner": ResponseSchemaSpec(
        schema_id="formal_claim_engine.agent.planner",
        version="1.0.0",
        schema={
            "type": "object",
            "required": ["action", "rationale"],
            "properties": {
                "action": {"type": "string"},
                "rationale": {"type": "string"},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "claim_graph_update": {"type": ["object", "null"]},
                "promotion_decisions": {},
                "work_requests": {},
            },
            "additionalProperties": True,
        },
    ),
    "claim_graph_agent": ResponseSchemaSpec(
        schema_id="formal_claim_engine.agent.claim_graph_agent",
        version="1.0.0",
        schema={
            "type": "object",
            "properties": {
                "graph_id": {"type": "string"},
                "claims": {"type": "array"},
                "relations": {"type": "array"},
            },
            "additionalProperties": True,
        },
    ),
    "formalizer": ResponseSchemaSpec(
        schema_id="formal_claim_engine.agent.formalizer",
        version="1.0.0",
        schema={
            "type": "object",
            "required": [
                "claim_id",
                "formalizer",
                "proof_source",
                "session_name",
                "module_name",
                "primary_target",
                "assumptions_used",
                "back_translation",
                "divergence_notes",
                "open_obligation_locations",
                "confidence",
            ],
            "properties": {
                "claim_id": {"type": "string"},
                "formalizer": {"type": "string"},
                "proof_language": {"type": "string"},
                "proof_source": {"type": "string"},
                "module_name": {"type": "string"},
                "primary_target": {"type": "string"},
                "theorem_statement": {"type": "string"},
                "definition_names": {"type": "array", "items": {"type": "string"}},
                "context_name": {"type": ["string", "null"]},
                "open_obligation_locations": {"type": "array", "items": {"type": "string"}},
                "assumptions_used": {"type": "array"},
                "back_translation": {"type": "string"},
                "divergence_notes": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "additionalProperties": True,
        },
    ),
    "proof_verifier": ResponseSchemaSpec(
        schema_id="formal_claim_engine.agent.proof_verifier",
        version="1.0.0",
        schema={
            "type": "object",
            "required": [
                "claim_id",
                "formalizer",
                "proof_language",
                "build_success",
                "build_log_summary",
                "errors",
                "warnings",
                "targets_found",
                "definitions_found",
                "contexts_found",
                "open_obligation_count",
                "open_obligation_locations",
                "dependency_count",
                "proof_status",
                "formal_artifact",
            ],
            "properties": {
                "claim_id": {"type": "string"},
                "formalizer": {"type": "string"},
                "proof_language": {"type": "string"},
                "build_success": {"type": "boolean"},
                "build_log_summary": {"type": "string"},
                "errors": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "targets_found": {"type": "array", "items": {"type": "string"}},
                "contexts_found": {"type": "array", "items": {"type": "string"}},
                "open_obligation_count": {"type": "integer"},
                "open_obligation_locations": {"type": "array", "items": {"type": "string"}},
                "definitions_found": {"type": "array", "items": {"type": "string"}},
                "dependency_count": {"type": "integer"},
                "session_fingerprint": {"type": ["string", "null"]},
                "proof_status": {"type": "string"},
                "formal_artifact": {"type": "object"},
            },
            "additionalProperties": True,
        },
    ),
    "auditor": ResponseSchemaSpec(
        schema_id="formal_claim_engine.agent.auditor",
        version="1.0.0",
        schema={
            "type": "object",
            "required": [
                "claim_id",
                "audit_kind",
                "trust_frontier",
                "conservativity",
                "model_health",
                "intent_alignment",
                "blocking_issues",
                "warnings",
                "recommendation",
            ],
            "properties": {
                "claim_id": {"type": "string"},
                "audit_kind": {"type": "string"},
                "trust_frontier": {"type": "object"},
                "conservativity": {"type": "object"},
                "model_health": {"type": "object"},
                "intent_alignment": {"type": "object"},
                "blocking_issues": {"type": "array", "items": {"type": "string"}},
                "warnings": {"type": "array", "items": {"type": "string"}},
                "recommendation": {"type": "string"},
            },
            "additionalProperties": True,
        },
    ),
    "research_agent": ResponseSchemaSpec(
        schema_id="formal_claim_engine.agent.research_agent",
        version="1.0.0",
        schema={
            "type": "object",
            "required": [
                "claim_id",
                "evidence_items",
                "edges",
                "overall_assessment",
                "recommended_support_status",
            ],
            "properties": {
                "claim_id": {"type": "string"},
                "evidence_items": {"type": "array"},
                "edges": {"type": "array"},
                "overall_assessment": {"type": "string"},
                "recommended_support_status": {"type": "string"},
            },
            "additionalProperties": True,
        },
    ),
    "dev_agent": ResponseSchemaSpec(
        schema_id="formal_claim_engine.agent.dev_agent",
        version="1.0.0",
        schema={
            "type": "object",
            "required": ["claim_id", "action", "contract_ref", "implementation_summary"],
            "properties": {
                "claim_id": {"type": "string"},
                "action": {"type": "string"},
                "contract_ref": {"type": "string"},
                "implementation_summary": {"type": "string"},
                "test_evidence": {"type": ["array", "null"]},
                "change_requests": {"type": ["array", "null"]},
                "runtime_guards": {"type": ["array", "null"]},
                "blockers": {"type": ["array", "null"]},
            },
            "additionalProperties": True,
        },
    ),
    "policy_engine": ResponseSchemaSpec(
        schema_id="formal_claim_engine.agent.policy_engine",
        version="1.0.0",
        schema={
            "type": "object",
            "required": ["decision_rationale", "required_actions_summary"],
            "properties": {
                "decision_rationale": {"type": "string"},
                "required_actions_summary": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "additionalProperties": True,
        },
    ),
}


def get_response_schema_spec(role: str) -> ResponseSchemaSpec:
    try:
        return RESPONSE_SCHEMAS[_normalize_role(role)]
    except KeyError as exc:
        raise KeyError(f"Unknown response schema role: {role}") from exc


def load_response_schema(role: str) -> dict[str, Any]:
    return get_response_schema_spec(role).schema


def response_schema_hash(role: str) -> str:
    schema = load_response_schema(role)
    serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def response_schema_metadata(role: str) -> dict[str, str]:
    spec = get_response_schema_spec(role)
    return {
        "response_schema_id": spec.schema_id,
        "response_schema_version": spec.version,
        "response_schema_sha256": response_schema_hash(role),
    }


def validate_response_output(role: str, payload: Any) -> None:
    jsonschema.Draft202012Validator(load_response_schema(role)).validate(payload)


__all__ = [
    "RESPONSE_SCHEMAS",
    "ResponseSchemaSpec",
    "get_response_schema_spec",
    "load_response_schema",
    "response_schema_hash",
    "response_schema_metadata",
    "validate_response_output",
]
