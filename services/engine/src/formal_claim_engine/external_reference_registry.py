"""Engine-owned external reference registry read models."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .store import canonical_artifact_id

REGISTRY_SCHEMA_VERSION = "1.0.0"
REFERENCE_STATUS_ORDER = {
    "resolved": 0,
    "ambiguous": 1,
    "unresolved": 2,
    "stale": 3,
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_excerpt(value: str | None) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(str(value).split())
    return collapsed or None


def reference_registry_artifact_id(project_id: str) -> str:
    return f"reference_registry.{canonical_artifact_id(project_id)}"


def reference_registry_signature(payload: dict[str, Any]) -> str:
    normalized = dict(payload)
    normalized.pop("generated_at", None)
    return hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def derive_reference_id(*, source_ref: str, excerpt: str | None, span_start: int | None, span_end: int | None) -> str:
    basis = "|".join(
        [
            source_ref,
            str(span_start) if span_start is not None else "",
            str(span_end) if span_end is not None else "",
            normalize_excerpt(excerpt) or "",
        ]
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return f"ref.{digest}"


def reference_match_keys(anchor: dict[str, Any]) -> list[str]:
    source_ref = str(anchor.get("source_ref") or "")
    keys: list[str] = []
    span_start = anchor.get("span_start")
    span_end = anchor.get("span_end")
    if source_ref and (span_start is not None or span_end is not None):
        keys.append(f"{source_ref}|span|{span_start}|{span_end}")
    normalized_excerpt = normalize_excerpt(
        str(anchor.get("normalized_excerpt") or anchor.get("excerpt") or "") or None
    )
    if source_ref and normalized_excerpt:
        keys.append(f"{source_ref}|excerpt|{normalized_excerpt}")
    source_location = str(anchor.get("source_location") or "") or None
    if source_ref and source_location:
        keys.append(f"{source_ref}|location|{source_location}")
    return keys


class ExternalReferenceDocument(BaseModel):
    document_id: str
    document_ref: str
    source_kind: str
    title: str
    display_name: str
    origin_path: str | None = None
    canonical_path: str | None = None
    text_sha256: str = ""
    current_revision_id: str | None = None


class ExternalReferenceArtifactPreview(BaseModel):
    artifact_kind: str
    artifact_id: str
    title: str
    summary: str
    created_at: str | None = None
    claim_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalReferenceRecord(BaseModel):
    reference_id: str
    status: str
    source_document: ExternalReferenceDocument
    citation_anchor: dict[str, Any] = Field(default_factory=dict)
    citation: dict[str, Any] = Field(default_factory=dict)
    source_mapping_ref: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    uncertainty: dict[str, Any] = Field(default_factory=dict)
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_artifact_kinds: list[str] = Field(default_factory=list)


class ExternalReferenceLink(BaseModel):
    link_id: str
    reference_id: str
    status: str
    subject_kind: str
    subject_id: str
    claim_id: str | None = None
    relation: str
    source_document: ExternalReferenceDocument
    artifact_preview: ExternalReferenceArtifactPreview


class ExternalReferenceRegistry(BaseModel):
    schema_version: str = REGISTRY_SCHEMA_VERSION
    registry_id: str
    project_id: str
    generated_at: str
    reference_count: int
    link_count: int
    source_document_count: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    references: list[ExternalReferenceRecord] = Field(default_factory=list)
    links: list[ExternalReferenceLink] = Field(default_factory=list)


def source_document_summary(
    source_document: dict[str, Any],
    *,
    current_revision_id: str | None,
) -> ExternalReferenceDocument:
    document_id = str(source_document.get("document_id") or source_document.get("document_ref") or "")
    return ExternalReferenceDocument(
        document_id=document_id,
        document_ref=str(source_document.get("document_ref") or document_id),
        source_kind=str(source_document.get("source_kind") or "document"),
        title=str(source_document.get("title") or source_document.get("display_name") or document_id),
        display_name=str(source_document.get("display_name") or source_document.get("title") or document_id),
        origin_path=source_document.get("origin_path"),
        canonical_path=source_document.get("canonical_path"),
        text_sha256=str(source_document.get("text_sha256") or ""),
        current_revision_id=current_revision_id,
    )


def pick_status(*statuses: str) -> str:
    filtered = [status for status in statuses if status]
    if not filtered:
        return "resolved"
    return max(filtered, key=lambda item: REFERENCE_STATUS_ORDER.get(item, 0))


def build_reference_record(
    *,
    reference_id: str,
    status: str,
    source_document: ExternalReferenceDocument,
    citation_anchor: dict[str, Any],
    citation: dict[str, Any],
    source_mapping_ref: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    uncertainty: dict[str, Any] | None = None,
) -> ExternalReferenceRecord:
    return ExternalReferenceRecord(
        reference_id=reference_id,
        status=status,
        source_document=source_document,
        citation_anchor=citation_anchor,
        citation=citation,
        source_mapping_ref=source_mapping_ref,
        provenance=provenance or {},
        uncertainty=uncertainty or {},
    )


def build_link_id(reference_id: str, subject_kind: str, subject_id: str, relation: str) -> str:
    basis = "|".join([reference_id, subject_kind, subject_id, relation])
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]
    return f"link.{digest}"
