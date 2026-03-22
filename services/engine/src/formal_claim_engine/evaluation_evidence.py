"""Engine-owned evaluation-evidence read models and artifact helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from .external_reference_registry import (
    ExternalReferenceArtifactPreview,
    ExternalReferenceDocument,
)
from .store import canonical_artifact_id

EVALUATION_EVIDENCE_SCHEMA_VERSION = "1.0.0"
_STATUS_ORDER = {
    "resolved": 0,
    "ambiguous": 1,
    "unresolved": 2,
    "stale": 3,
}


def evaluation_evidence_artifact_id(document_id: str) -> str:
    return canonical_artifact_id(document_id)


def evaluation_evidence_signature(payload: dict[str, Any]) -> str:
    normalized = dict(payload)
    normalized.pop("generated_at", None)
    return hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_evidence_link_id(
    evidence_id: str,
    subject_kind: str,
    subject_id: str,
    relation: str,
) -> str:
    basis = "|".join([evidence_id, subject_kind, subject_id, relation])
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
    return f"evidence-link.{digest}"


class EvaluationEvidenceRecord(BaseModel):
    evidence_id: str
    status: str
    source_document: ExternalReferenceDocument
    source_mapping_ref: dict[str, Any] = Field(default_factory=dict)
    citation_anchor: dict[str, Any] = Field(default_factory=dict)
    title: str
    summary: str
    evidence_kind: str
    dataset: str | None = None
    metric_name: str | None = None
    split: str | None = None
    comparison_target: str | None = None
    baseline_value: float | None = None
    baseline_value_text: str | None = None
    reported_value: float | None = None
    reported_value_text: str | None = None
    delta_value: float | None = None
    unit: str | None = None
    direction: str | None = None
    table_figure_anchor: str | None = None
    cited_table_label: str | None = None
    cited_figure_label: str | None = None
    confidence_interval: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_reference_ids: list[str] = Field(default_factory=list)


class EvaluationEvidenceLink(BaseModel):
    link_id: str
    evidence_id: str
    status: str
    subject_kind: str
    subject_id: str
    relation: str
    claim_id: str | None = None
    reference_id: str | None = None
    source_document: ExternalReferenceDocument
    artifact_preview: ExternalReferenceArtifactPreview


class EvaluationEvidenceBundle(BaseModel):
    schema_version: str = EVALUATION_EVIDENCE_SCHEMA_VERSION
    bundle_id: str
    project_id: str
    document_id: str
    generated_at: str
    evidence_count: int
    link_count: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    items: list[EvaluationEvidenceRecord] = Field(default_factory=list)
    links: list[EvaluationEvidenceLink] = Field(default_factory=list)


def pick_evidence_status(*statuses: str) -> str:
    filtered = [status for status in statuses if status]
    if not filtered:
        return "resolved"
    return max(filtered, key=lambda item: _STATUS_ORDER.get(item, 0))


__all__ = [
    "EVALUATION_EVIDENCE_SCHEMA_VERSION",
    "EvaluationEvidenceBundle",
    "EvaluationEvidenceLink",
    "EvaluationEvidenceRecord",
    "build_evidence_link_id",
    "evaluation_evidence_artifact_id",
    "evaluation_evidence_signature",
    "pick_evidence_status",
]
