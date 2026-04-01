"""Typed DTOs for evidence-connector document ingest adapters."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TraceRawUnit(BaseModel):
    unit_id: str
    unit_kind: str
    source_ref: str
    text: str
    span_start: int | None = None
    span_end: int | None = None
    citation_anchor: "TraceCitationAnchor | None" = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceEvidenceItem(BaseModel):
    evidence_id: str
    title: str
    summary: str
    evidence_kind: str
    source_ref: str
    excerpt: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    citation_anchor: "TraceCitationAnchor | None" = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)


class TraceEvaluationEvidence(BaseModel):
    evidence_id: str
    title: str
    summary: str
    evidence_kind: str = "evaluation_measurement"
    source_ref: str
    source_claim_id: str | None = None
    claim_candidate_id: str | None = None
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
    citation_anchor: "TraceCitationAnchor | None" = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)


class TraceDocumentClaim(BaseModel):
    id: str
    title: str
    statement: str
    role: str
    status: str = "stated"
    source_location: str | None = None
    source_text: str | None = None
    scope: str | None = None
    depth: int = 0
    notes: list[str] = Field(default_factory=list)
    span_start: int | None = None
    span_end: int | None = None


class TraceDocumentRelation(BaseModel):
    source_id: str
    target_id: str
    relation_type: str = "derives"
    strength: str = "unknown"
    rationale: str | None = None


class TraceUnresolvedReference(BaseModel):
    reference_id: str
    reference_kind: str
    source_ref: str
    description: str
    confidence: float = 0.0
    suggested_resolution: str | None = None
    citation_anchor: "TraceCitationAnchor | None" = None


class TraceClaimMapping(BaseModel):
    source_claim_id: str
    proposed_claim_id: str
    source_role: str
    proposed_claim_class: str
    proposed_claim_kind: str
    source_anchor_ref: str
    mapping_rationale: str
    citation_anchor: "TraceCitationAnchor | None" = None
    uncertainty: dict[str, Any] = Field(default_factory=dict)


class TraceRelationMapping(BaseModel):
    source_relation_type: str
    proposed_relation_type: str
    source_claim_id: str
    target_claim_id: str
    proposed_from_claim_id: str
    proposed_to_claim_id: str
    mapping_rationale: str
    uncertainty: dict[str, Any] = Field(default_factory=dict)


class TraceDocumentMappingReport(BaseModel):
    project_id: str
    domain: str
    document_id: str
    document_ref: str
    imported_claim_count: int
    imported_relation_count: int
    source_role_counts: dict[str, int]
    proposed_class_counts: dict[str, int]
    proposed_kind_counts: dict[str, int]
    anchor_status_counts: dict[str, int] = Field(default_factory=dict)
    claim_mappings: list[TraceClaimMapping]
    relation_mappings: list[TraceRelationMapping]
    unresolved_reference_count: int = 0
    ambiguous_anchor_count: int = 0
    unresolved_anchor_count: int = 0
    rationale_summary: list[str] = Field(default_factory=list)
    uncertainty_summary: dict[str, Any] = Field(default_factory=dict)


class TraceSourceDocument(BaseModel):
    document_id: str
    document_ref: str
    source_kind: str
    title: str
    display_name: str
    origin_path: str | None = None
    canonical_path: str | None = None
    media_type: str = "text/plain"
    charset: str = "utf-8"
    text_sha256: str
    byte_length: int = 0
    text_length: int = 0
    imported_at: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class TraceCitationAnchor(BaseModel):
    anchor_id: str
    document_id: str
    source_ref: str
    status: str
    anchor_kind: str
    source_location: str | None = None
    excerpt: str | None = None
    normalized_excerpt: str | None = None
    excerpt_sha256: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    candidate_spans: list[dict[str, Any]] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)


class TraceDocumentIngestRequest(BaseModel):
    project_id: str
    domain: str
    document_ref: str
    description: str = ""
    source_document: TraceSourceDocument | None = None
    document_text: str = ""
    existing_claim_ids: list[str] = Field(default_factory=list)
    claims: list[TraceDocumentClaim]
    relations: list[TraceDocumentRelation] = Field(default_factory=list)


class TraceDocumentIngestBundle(BaseModel):
    project_id: str
    domain: str
    document_ref: str
    description: str = ""
    source_document: TraceSourceDocument
    raw_units: list[TraceRawUnit] = Field(default_factory=list)
    evidence_items: list[TraceEvidenceItem] = Field(default_factory=list)
    claim_candidates: list[dict[str, Any]] = Field(default_factory=list)
    relation_candidates: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_references: list[TraceUnresolvedReference] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    mapping_report: TraceDocumentMappingReport


class TraceDocumentIngestResult(BaseModel):
    bundle: TraceDocumentIngestBundle
