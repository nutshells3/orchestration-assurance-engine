"""Document-ingest adapters that emit source-mapping bundles, not ClaimGraphs."""

from __future__ import annotations

import hashlib
import io
import mimetypes
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    TraceCitationAnchor,
    TraceClaimMapping,
    TraceDocumentIngestBundle,
    TraceDocumentIngestRequest,
    TraceDocumentIngestResult,
    TraceDocumentMappingReport,
    TraceEvidenceItem,
    TraceRawUnit,
    TraceRelationMapping,
    TraceSourceDocument,
    TraceUnresolvedReference,
)

TRACE_ROLE_PREFIX = "tracer_role:"
TRACE_STATUS_PREFIX = "tracer_status:"
TRACE_DEPTH_PREFIX = "tracer_depth:"
TRACE_DOMAIN_PREFIX = "tracer_domain:"
TRACE_ADAPTER_VERSION = "m8-01"

DEPENDENCY_RELATIONS = {
    "derives",
    "assumes",
    "supports",
    "cites",
    "interprets",
    "applies_to",
    "requires",
    "strengthens",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    return text or "claim"


def project_short_id(project_id: str) -> str:
    return slugify(project_id.split(".")[-1])


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def build_default_graph_policy(domain: str) -> dict[str, Any]:
    normalized = str(domain or "general")
    return {
        "default_assumption_carrier": "locale"
        if normalized == "formal_proof"
        else "premise",
        "allow_global_axioms": False,
        "require_backtranslation_review": True,
        "require_dual_formalization_for_core_claims": normalized == "formal_proof",
    }


def role_to_claim_class(role: str) -> str:
    if role in {
        "axiom",
        "premise",
        "hypothesis",
        "hidden_assumption",
        "statute",
        "precedent",
    }:
        return "assumption"
    if role in {"theorem", "conclusion", "holding"}:
        return "core_claim"
    if role == "observation":
        return "metric"
    return "enabling_claim"


def role_to_claim_kind(role: str) -> str:
    if role == "definition":
        return "definition_candidate"
    if role in {"theorem", "lemma", "corollary", "conclusion", "holding"}:
        return "theorem_candidate"
    if role == "observation":
        return "evaluation_criterion"
    return "hypothesis"


def trace_status_to_candidate(status: str) -> str:
    if status == "refuted":
        return "rejected"
    if status in {"challenged", "circular", "unsupported"}:
        return "blocked"
    if status == "supported":
        return "formalizing"
    return "candidate"


def trace_relation_to_canonical(relation: dict[str, Any]) -> dict[str, Any]:
    from_claim_id = str(relation["source_id"])
    to_claim_id = str(relation["target_id"])
    candidate_type = "depends_on"
    relation_type = str(relation.get("relation_type", "derives"))

    if relation_type in DEPENDENCY_RELATIONS:
        from_claim_id = str(relation["target_id"])
        to_claim_id = str(relation["source_id"])
    elif relation_type == "contradicts":
        candidate_type = "conflicts_with"
    elif relation_type == "weakens":
        candidate_type = "blocks"
    elif relation_type == "specializes":
        candidate_type = "specializes"
    elif relation_type == "generalizes":
        candidate_type = "generalizes"

    rationale_parts = [
        f"trace_relation={relation_type}",
        f"strength={relation.get('strength', 'unknown')}",
    ]
    if relation.get("rationale"):
        rationale_parts.append(str(relation["rationale"]))

    relation_id = relation.get("relation_id")
    if not relation_id:
        relation_id = (
            f"rel.{slugify(from_claim_id)}.{candidate_type}.{slugify(to_claim_id)}"
        )

    return {
        "relation_id": relation_id,
        "from_claim_id": from_claim_id,
        "to_claim_id": to_claim_id,
        "relation_type": candidate_type,
        "status": "candidate",
        "required_for_promotion": candidate_type == "depends_on",
        "rationale": " | ".join(rationale_parts),
    }


def _normalize_local_path(path: str | Path) -> tuple[Path, str]:
    resolved = Path(path).expanduser().resolve()
    normalized = resolved.as_posix()
    if normalized.startswith("/") and len(normalized) > 3 and normalized[2] == ":":
        normalized = normalized[1:]
    if normalized[1:3] == ":/":
        normalized = normalized[0].lower() + normalized[1:]
    return resolved, normalized


def _decode_text_bytes(raw_bytes: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252"):
        try:
            return raw_bytes.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace"), "utf-8-replace"


def _extract_pdf_text(raw_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency contract guard
        raise RuntimeError(
            "PDF ingest requires pypdf to be installed in the active engine environment."
        ) from exc

    reader = PdfReader(io.BytesIO(raw_bytes))
    pages = []
    for page in reader.pages:
        pages.append((page.extract_text() or "").strip())
    return "\n\n".join(page for page in pages if page).strip()


def _extract_document_text(
    raw_bytes: bytes,
    *,
    file_name: str,
    media_type: str | None,
) -> tuple[str, str, str]:
    inferred_media_type = media_type or mimetypes.guess_type(file_name)[0] or "text/plain"
    normalized_media_type = inferred_media_type.split(";", 1)[0].strip().lower()
    if normalized_media_type == "application/pdf" or file_name.lower().endswith(".pdf"):
        return _extract_pdf_text(raw_bytes), "binary/pdf", "application/pdf"

    text, encoding = _decode_text_bytes(raw_bytes)
    return text, encoding, inferred_media_type


def _build_local_file_source_document(
    project_id: str,
    *,
    resolved_path: Path,
    normalized_path: str,
    media_type: str,
    encoding: str,
    raw_bytes: bytes,
    text: str,
) -> TraceSourceDocument:
    path_digest = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()[:12]
    document_id = (
        f"document.local.{project_short_id(project_id)}."
        f"{slugify(resolved_path.stem)}.{path_digest}"
    )
    return TraceSourceDocument(
        document_id=document_id,
        document_ref=document_id,
        source_kind="local_file",
        title=resolved_path.stem or resolved_path.name,
        display_name=resolved_path.name,
        origin_path=str(resolved_path),
        canonical_path=normalized_path,
        media_type=media_type,
        charset=encoding,
        text_sha256=text_sha256(text),
        byte_length=len(raw_bytes),
        text_length=len(text),
        imported_at=now_utc_iso(),
        provenance={
            "adapter": "trace_document",
            "adapter_version": TRACE_ADAPTER_VERSION,
            "identity_basis": {
                "kind": "canonical_path",
                "canonical_path": normalized_path,
                "path_digest": path_digest,
            },
        },
    )


def build_uploaded_source_document(
    project_id: str,
    *,
    file_name: str,
    text: str,
    raw_bytes: bytes,
    media_type: str,
    charset: str,
) -> TraceSourceDocument:
    display_name = Path(file_name).name or file_name or "uploaded-document"
    content_digest = text_sha256(text)[:12]
    document_id = (
        f"document.upload.{project_short_id(project_id)}."
        f"{slugify(Path(display_name).stem or display_name)}.{content_digest}"
    )
    return TraceSourceDocument(
        document_id=document_id,
        document_ref=document_id,
        source_kind="uploaded_file",
        title=Path(display_name).stem or display_name,
        display_name=display_name,
        media_type=media_type,
        charset=charset,
        text_sha256=text_sha256(text),
        byte_length=len(raw_bytes),
        text_length=len(text),
        imported_at=now_utc_iso(),
        provenance={
            "adapter": "trace_document",
            "adapter_version": TRACE_ADAPTER_VERSION,
            "identity_basis": {
                "kind": "upload_name_and_content_sha256",
                "file_name": display_name,
                "text_digest": content_digest,
            },
            "upload": {
                "original_file_name": display_name,
                "media_type": media_type,
            },
        },
    )


def load_local_text_document(
    project_id: str,
    path: str | Path,
) -> tuple[TraceSourceDocument, str]:
    resolved, normalized_path = _normalize_local_path(path)
    raw_bytes = resolved.read_bytes()
    text, encoding, media_type = _extract_document_text(
        raw_bytes,
        file_name=resolved.name,
        media_type=mimetypes.guess_type(resolved.name)[0],
    )
    source_document = _build_local_file_source_document(
        project_id,
        resolved_path=resolved,
        normalized_path=normalized_path,
        media_type=media_type,
        encoding=encoding,
        raw_bytes=raw_bytes,
        text=text,
    )
    return source_document, text


def load_uploaded_document(
    project_id: str,
    *,
    file_name: str,
    raw_bytes: bytes,
    media_type: str | None = None,
) -> tuple[TraceSourceDocument, str]:
    text, encoding, resolved_media_type = _extract_document_text(
        raw_bytes,
        file_name=file_name,
        media_type=media_type,
    )
    source_document = build_uploaded_source_document(
        project_id,
        file_name=file_name,
        text=text,
        raw_bytes=raw_bytes,
        media_type=resolved_media_type,
        charset=encoding,
    )
    return source_document, text


def build_inline_source_document(
    project_id: str,
    text: str,
    *,
    label: str = "inline document",
    document_ref: str | None = None,
) -> TraceSourceDocument:
    digest = text_sha256(text)[:12]
    document_id = document_ref or (
        f"document.inline.{project_short_id(project_id)}.{slugify(label)}.{digest}"
    )
    return TraceSourceDocument(
        document_id=document_id,
        document_ref=document_id,
        source_kind="inline_text",
        title=label,
        display_name=label,
        media_type="text/plain",
        charset="utf-8",
        text_sha256=text_sha256(text),
        byte_length=len(text.encode("utf-8")),
        text_length=len(text),
        imported_at=now_utc_iso(),
        provenance={
            "adapter": "trace_document",
            "adapter_version": TRACE_ADAPTER_VERSION,
            "identity_basis": {
                "kind": "inline_text_sha256",
                "digest": digest,
            },
        },
    )


def _line_number_at(text: str, offset: int) -> int:
    bounded = max(0, min(offset, len(text)))
    return text.count("\n", 0, bounded) + 1


def _candidate_span(text: str, start: int, end: int, *, reason: str) -> dict[str, Any]:
    return {
        "span_start": start,
        "span_end": end,
        "line_start": _line_number_at(text, start),
        "line_end": _line_number_at(text, max(start, end - 1)),
        "reason": reason,
    }


def _normalized_text_with_index(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index_map: list[int] = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if chars and not in_space:
                chars.append(" ")
                index_map.append(index)
            in_space = True
            continue
        chars.append(char.lower())
        index_map.append(index)
        in_space = False

    if chars and chars[-1] == " ":
        chars.pop()
        index_map.pop()
    return "".join(chars), index_map


def _find_exact_excerpt_occurrences(text: str, excerpt: str) -> list[tuple[int, int]]:
    if not excerpt:
        return []
    spans: list[tuple[int, int]] = []
    start = text.find(excerpt)
    while start >= 0:
        spans.append((start, start + len(excerpt)))
        start = text.find(excerpt, start + 1)
    return spans


def _find_normalized_excerpt_occurrences(text: str, excerpt: str) -> list[tuple[int, int]]:
    normalized_excerpt = normalize_whitespace(excerpt).lower()
    if not normalized_excerpt:
        return []
    normalized_text, index_map = _normalized_text_with_index(text)
    spans: list[tuple[int, int]] = []
    start = normalized_text.find(normalized_excerpt)
    while start >= 0:
        end = start + len(normalized_excerpt)
        original_start = index_map[start]
        original_end = index_map[end - 1] + 1
        spans.append((original_start, original_end))
        start = normalized_text.find(normalized_excerpt, start + 1)
    return spans


def normalize_citation_anchor(
    *,
    source_document: TraceSourceDocument,
    document_text: str,
    source_location: str | None = None,
    excerpt: str | None = None,
    span_start: int | None = None,
    span_end: int | None = None,
) -> TraceCitationAnchor:
    status = "unresolved"
    anchor_kind = "document_ref"
    normalized_excerpt = normalize_whitespace(excerpt or "") or None
    candidate_spans: list[dict[str, Any]] = []
    resolved_start: int | None = None
    resolved_end: int | None = None
    match_strategy = "document_ref_fallback"
    confidence = 0.25

    if (
        span_start is not None
        and span_end is not None
        and 0 <= int(span_start) < int(span_end) <= len(document_text)
    ):
        resolved_start = int(span_start)
        resolved_end = int(span_end)
        anchor_kind = "char_span"
        status = "resolved"
        match_strategy = "explicit_offsets"
        confidence = 1.0
        if not excerpt:
            excerpt = document_text[resolved_start:resolved_end]
            normalized_excerpt = normalize_whitespace(excerpt) or None
    elif excerpt:
        exact_matches = _find_exact_excerpt_occurrences(document_text, excerpt)
        if len(exact_matches) == 1:
            resolved_start, resolved_end = exact_matches[0]
            anchor_kind = "char_span"
            status = "resolved"
            match_strategy = "exact_excerpt"
            confidence = 0.95
        elif len(exact_matches) > 1:
            anchor_kind = "excerpt_match"
            status = "ambiguous"
            match_strategy = "multiple_exact_excerpt_matches"
            confidence = 0.5
            candidate_spans = [
                _candidate_span(document_text, start, end, reason="exact_excerpt_match")
                for start, end in exact_matches[:5]
            ]
        else:
            normalized_matches = _find_normalized_excerpt_occurrences(document_text, excerpt)
            if len(normalized_matches) == 1:
                resolved_start, resolved_end = normalized_matches[0]
                anchor_kind = "char_span"
                status = "resolved"
                match_strategy = "normalized_excerpt"
                confidence = 0.85
            elif len(normalized_matches) > 1:
                anchor_kind = "excerpt_match"
                status = "ambiguous"
                match_strategy = "multiple_normalized_excerpt_matches"
                confidence = 0.45
                candidate_spans = [
                    _candidate_span(
                        document_text,
                        start,
                        end,
                        reason="normalized_excerpt_match",
                    )
                    for start, end in normalized_matches[:5]
                ]
            elif source_location:
                anchor_kind = "location_hint"
                status = "ambiguous"
                match_strategy = "location_hint_fallback"
                confidence = 0.3
            else:
                anchor_kind = "excerpt_hash"
                status = "unresolved"
                match_strategy = "excerpt_hash_only"
                confidence = 0.15
    elif source_location:
        anchor_kind = "location_hint"
        status = "ambiguous"
        match_strategy = "location_hint_only"
        confidence = 0.25

    if status == "resolved" and resolved_start is not None and resolved_end is not None:
        excerpt = excerpt or document_text[resolved_start:resolved_end]
        normalized_excerpt = normalize_whitespace(excerpt) or None

    excerpt_hash = text_sha256(excerpt) if excerpt else None
    anchor_basis = "|".join(
        [
            source_document.document_id,
            anchor_kind,
            status,
            str(source_location or ""),
            str(resolved_start if resolved_start is not None else ""),
            str(resolved_end if resolved_end is not None else ""),
            str(excerpt_hash or ""),
            ",".join(
                f"{item['span_start']}:{item['span_end']}" for item in candidate_spans
            ),
        ]
    )
    anchor_id = f"anchor.{hashlib.sha256(anchor_basis.encode('utf-8')).hexdigest()[:16]}"

    line_start = None
    line_end = None
    if resolved_start is not None and resolved_end is not None:
        line_start = _line_number_at(document_text, resolved_start)
        line_end = _line_number_at(document_text, max(resolved_start, resolved_end - 1))

    return TraceCitationAnchor(
        anchor_id=anchor_id,
        document_id=source_document.document_id,
        source_ref=source_document.document_ref,
        status=status,
        anchor_kind=anchor_kind,
        source_location=source_location,
        excerpt=excerpt,
        normalized_excerpt=normalized_excerpt,
        excerpt_sha256=excerpt_hash,
        span_start=resolved_start,
        span_end=resolved_end,
        line_start=line_start,
        line_end=line_end,
        candidate_spans=candidate_spans,
        provenance={
            "adapter": "trace_document",
            "adapter_version": TRACE_ADAPTER_VERSION,
            "document_text_sha256": source_document.text_sha256,
        },
        uncertainty={
            "confidence": confidence,
            "match_strategy": match_strategy,
            "candidate_count": len(candidate_spans),
        },
    )


def trace_claim_to_canonical(
    *,
    project_id: str,
    domain: str,
    claim: dict[str, Any],
    canonical_claim_id: str,
    default_source_ref: str,
    source_document: TraceSourceDocument | None = None,
    document_text: str = "",
    citation_anchor: TraceCitationAnchor | None = None,
) -> dict[str, Any]:
    role = str(claim.get("role", "premise"))
    status = str(claim.get("status", "stated"))
    title = str(claim.get("title") or "untitled")
    statement = str(claim.get("statement") or "")
    notes = [str(item) for item in list(claim.get("notes") or [])]

    if citation_anchor is None and source_document is not None:
        citation_anchor = normalize_citation_anchor(
            source_document=source_document,
            document_text=document_text,
            source_location=str(claim.get("source_location") or "") or None,
            excerpt=str(claim.get("source_text") or "") or None,
            span_start=claim.get("span_start"),
            span_end=claim.get("span_end"),
        )

    source_anchor: dict[str, Any]
    if citation_anchor is not None:
        source_anchor = {
            "source_type": "document",
            "source_ref": citation_anchor.source_ref,
        }
        if citation_anchor.excerpt:
            source_anchor["excerpt"] = citation_anchor.excerpt
        if citation_anchor.span_start is not None:
            source_anchor["span_start"] = citation_anchor.span_start
        if citation_anchor.span_end is not None:
            source_anchor["span_end"] = citation_anchor.span_end
    else:
        source_ref = str(
            claim.get("source_location")
            or claim.get("source_ref")
            or default_source_ref
        )
        source_anchor = {
            "source_type": "document",
            "source_ref": source_ref,
            "excerpt": claim.get("source_text"),
        }
        if claim.get("span_start") is not None:
            source_anchor["span_start"] = int(claim["span_start"])
        if claim.get("span_end") is not None:
            source_anchor["span_end"] = int(claim["span_end"])

    claim_class = role_to_claim_class(role)
    formalization_required = str(domain) == "formal_proof"
    downstream_kind = (
        "research_then_dev"
        if formalization_required and claim_class in {"core_claim", "enabling_claim"}
        else "research_only"
    )
    independent_formalizations_required = (
        2 if formalization_required and claim_class == "core_claim" else 0
    )

    return {
        "claim_id": canonical_claim_id,
        "title": title,
        "nl_statement": statement,
        "normalized_statement": statement,
        "intent_gloss": f"Imported from {domain} document analysis.",
        "claim_class": claim_class,
        "claim_kind": role_to_claim_kind(role),
        "status": trace_status_to_candidate(status),
        "formalization_required": formalization_required,
        "downstream_kind": downstream_kind,
        "priority": max(0, 100 - (int(claim.get("depth", 0) or 0) * 10)),
        "tags": [
            f"{TRACE_ROLE_PREFIX}{role}",
            f"{TRACE_STATUS_PREFIX}{status}",
            f"{TRACE_DEPTH_PREFIX}{int(claim.get('depth', 0) or 0)}",
            f"{TRACE_DOMAIN_PREFIX}{domain}",
            "connector:trace_document",
        ],
        "notes": notes,
        "scope": {
            "domain": str(claim.get("scope") or domain),
            "modality": "other",
            "included_conditions": [],
            "excluded_conditions": [],
        },
        "semantics_guard": {
            "must_preserve": [title or statement or canonical_claim_id],
            "allowed_weakenings": [],
            "forbidden_weakenings": [],
            "forbidden_strengthenings": [],
            "backtranslation_required": formalization_required,
            "independent_formalizations_required": independent_formalizations_required,
        },
        "policy": {
            "allowed_assumption_carriers": ["premise", "locale"],
            "global_axiom_allowed": False,
            "sorry_allowed_in_scratch": True,
            "sorry_allowed_in_mainline": False,
        },
        "provenance": {
            "created_by_role": "system",
            "source_anchors": [source_anchor],
            "last_reviewed_by_role": "system",
            "review_notes": notes or None,
        },
        "owner_role": "system",
        "reviewer_roles": ["human_reviewer"],
    }


def _make_claim_id(project_id: str, title: str, existing_ids: set[str]) -> str:
    project_short = project_short_id(project_id)
    stem = f"claim.{project_short}.{slugify(title)}"
    candidate = stem
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{stem}.{suffix}"
        suffix += 1
    existing_ids.add(candidate)
    return candidate


def _raw_unit_from_claim(
    *,
    claim: dict[str, Any],
    source_document: TraceSourceDocument,
    citation_anchor: TraceCitationAnchor,
) -> TraceRawUnit:
    return TraceRawUnit(
        unit_id=f"unit.{slugify(claim.get('id') or claim.get('title') or source_document.document_id)}",
        unit_kind="claim_span",
        source_ref=source_document.document_ref,
        text=str(claim.get("source_text") or claim.get("statement") or ""),
        span_start=citation_anchor.span_start,
        span_end=citation_anchor.span_end,
        citation_anchor=citation_anchor,
        metadata={
            "source_claim_id": str(claim.get("id") or ""),
            "role": str(claim.get("role") or "premise"),
            "status": str(claim.get("status") or "stated"),
            "source_location": str(claim.get("source_location") or "") or None,
        },
    )


def _evidence_item_from_claim(
    *,
    claim: dict[str, Any],
    source_document: TraceSourceDocument,
    citation_anchor: TraceCitationAnchor,
) -> TraceEvidenceItem | None:
    excerpt = str(claim.get("source_text") or "").strip() or citation_anchor.excerpt
    if not excerpt:
        return None
    return TraceEvidenceItem(
        evidence_id=(
            f"evidence.{slugify(claim.get('id') or claim.get('title') or source_document.document_id)}"
        ),
        title=str(claim.get("title") or "document excerpt"),
        summary=f"Extracted from {source_document.display_name}",
        evidence_kind="document_excerpt",
        source_ref=source_document.document_ref,
        excerpt=excerpt,
        span_start=citation_anchor.span_start,
        span_end=citation_anchor.span_end,
        citation_anchor=citation_anchor,
        provenance={
            "adapter": "trace_document",
            "adapter_version": TRACE_ADAPTER_VERSION,
            "source_claim_id": str(claim.get("id") or ""),
            "source_location": str(claim.get("source_location") or "") or None,
        },
        uncertainty={
            "confidence": 0.75,
            "requires_human_admission": True,
            "anchor_status": citation_anchor.status,
        },
    )


def ingest_trace_document(request: TraceDocumentIngestRequest) -> TraceDocumentIngestResult:
    source_document = request.source_document or build_inline_source_document(
        request.project_id,
        request.document_text,
        label=request.document_ref,
        document_ref=request.document_ref,
    )
    existing_ids: set[str] = {str(item) for item in request.existing_claim_ids}
    source_role_counts: Counter[str] = Counter()
    proposed_class_counts: Counter[str] = Counter()
    proposed_kind_counts: Counter[str] = Counter()
    anchor_status_counts: Counter[str] = Counter()
    claim_mappings: list[TraceClaimMapping] = []
    relation_mappings: list[TraceRelationMapping] = []
    unresolved_references: list[TraceUnresolvedReference] = []
    raw_units: list[TraceRawUnit] = []
    evidence_items: list[TraceEvidenceItem] = []
    claim_candidates: list[dict[str, Any]] = []
    relation_candidates: list[dict[str, Any]] = []
    source_to_candidate: dict[str, str] = {}

    for source_claim in request.claims:
        source_payload = source_claim.model_dump(mode="json", exclude_none=True)
        proposed_claim_id = _make_claim_id(
            request.project_id,
            source_claim.title,
            existing_ids,
        )
        source_to_candidate[source_claim.id] = proposed_claim_id
        citation_anchor = normalize_citation_anchor(
            source_document=source_document,
            document_text=request.document_text,
            source_location=str(source_payload.get("source_location") or "") or None,
            excerpt=str(source_payload.get("source_text") or "") or None,
            span_start=source_payload.get("span_start"),
            span_end=source_payload.get("span_end"),
        )
        anchor_status_counts[citation_anchor.status] += 1
        claim_candidate = trace_claim_to_canonical(
            project_id=request.project_id,
            domain=request.domain,
            claim=source_payload,
            canonical_claim_id=proposed_claim_id,
            default_source_ref=source_document.document_ref,
            source_document=source_document,
            document_text=request.document_text,
            citation_anchor=citation_anchor,
        )
        raw_units.append(
            _raw_unit_from_claim(
                claim=source_payload,
                source_document=source_document,
                citation_anchor=citation_anchor,
            )
        )
        evidence_item = _evidence_item_from_claim(
            claim=source_payload,
            source_document=source_document,
            citation_anchor=citation_anchor,
        )
        if evidence_item is not None:
            evidence_items.append(evidence_item)
        claim_candidates.append(claim_candidate)
        source_role_counts[source_claim.role] += 1
        proposed_class_counts[str(claim_candidate["claim_class"])] += 1
        proposed_kind_counts[str(claim_candidate["claim_kind"])] += 1
        claim_mappings.append(
            TraceClaimMapping(
                source_claim_id=source_claim.id,
                proposed_claim_id=proposed_claim_id,
                source_role=source_claim.role,
                proposed_claim_class=str(claim_candidate["claim_class"]),
                proposed_claim_kind=str(claim_candidate["claim_kind"]),
                source_anchor_ref=citation_anchor.anchor_id,
                mapping_rationale=(
                    f"Mapped {source_claim.role} to {claim_candidate['claim_class']} / "
                    f"{claim_candidate['claim_kind']}."
                ),
                citation_anchor=citation_anchor,
                uncertainty={
                    "confidence": 0.75,
                    "requires_admission_review": True,
                },
            )
        )

    for source_relation in request.relations:
        relation_anchor = normalize_citation_anchor(
            source_document=source_document,
            document_text=request.document_text,
        )
        if source_relation.source_id not in source_to_candidate:
            unresolved_references.append(
                TraceUnresolvedReference(
                    reference_id=f"unresolved.{slugify(source_relation.source_id)}",
                    reference_kind="missing_source_claim",
                    source_ref=source_document.document_ref,
                    description=(
                        f"Relation source {source_relation.source_id!r} was not present "
                        "in extracted claim candidates."
                    ),
                    confidence=0.9,
                    suggested_resolution="Re-run extraction or map the relation manually.",
                    citation_anchor=relation_anchor,
                )
            )
            continue
        if source_relation.target_id not in source_to_candidate:
            unresolved_references.append(
                TraceUnresolvedReference(
                    reference_id=f"unresolved.{slugify(source_relation.target_id)}",
                    reference_kind="missing_target_claim",
                    source_ref=source_document.document_ref,
                    description=(
                        f"Relation target {source_relation.target_id!r} was not present "
                        "in extracted claim candidates."
                    ),
                    confidence=0.9,
                    suggested_resolution="Re-run extraction or map the relation manually.",
                    citation_anchor=relation_anchor,
                )
            )
            continue

        mapped_relation = trace_relation_to_canonical(
            {
                **source_relation.model_dump(mode="json", exclude_none=True),
                "source_id": source_to_candidate[source_relation.source_id],
                "target_id": source_to_candidate[source_relation.target_id],
                "relation_id": (
                    f"rel.{project_short_id(request.project_id)}."
                    f"{slugify(source_relation.source_id)}."
                    f"{source_relation.relation_type}."
                    f"{slugify(source_relation.target_id)}"
                ),
            }
        )
        relation_candidates.append(mapped_relation)
        relation_mappings.append(
            TraceRelationMapping(
                source_relation_type=source_relation.relation_type,
                proposed_relation_type=str(mapped_relation["relation_type"]),
                source_claim_id=source_relation.source_id,
                target_claim_id=source_relation.target_id,
                proposed_from_claim_id=str(mapped_relation["from_claim_id"]),
                proposed_to_claim_id=str(mapped_relation["to_claim_id"]),
                mapping_rationale=(
                    f"Mapped {source_relation.relation_type} to "
                    f"{mapped_relation['relation_type']}."
                ),
                uncertainty={
                    "confidence": 0.7,
                    "requires_admission_review": True,
                },
            )
        )

    mapping_report = TraceDocumentMappingReport(
        project_id=request.project_id,
        domain=request.domain,
        document_id=source_document.document_id,
        document_ref=source_document.document_ref,
        imported_claim_count=len(claim_mappings),
        imported_relation_count=len(relation_mappings),
        source_role_counts=dict(source_role_counts),
        proposed_class_counts=dict(proposed_class_counts),
        proposed_kind_counts=dict(proposed_kind_counts),
        anchor_status_counts=dict(anchor_status_counts),
        claim_mappings=claim_mappings,
        relation_mappings=relation_mappings,
        unresolved_reference_count=len(unresolved_references),
        ambiguous_anchor_count=int(anchor_status_counts.get("ambiguous", 0)),
        unresolved_anchor_count=int(anchor_status_counts.get("unresolved", 0)),
        rationale_summary=[
            "Connector emitted claim and relation candidates only.",
            "Canonical ClaimGraph admission remains engine-owned.",
            "Citation anchors are normalized against a stable source-document identity.",
        ],
        uncertainty_summary={
            "requires_human_admission": True,
            "unresolved_reference_count": len(unresolved_references),
            "anchor_status_counts": dict(anchor_status_counts),
        },
    )

    bundle = TraceDocumentIngestBundle(
        project_id=request.project_id,
        domain=request.domain,
        document_ref=source_document.document_ref,
        description=request.description,
        source_document=source_document,
        raw_units=raw_units,
        evidence_items=evidence_items,
        claim_candidates=claim_candidates,
        relation_candidates=relation_candidates,
        unresolved_references=unresolved_references,
        provenance={
            "adapter": "trace_document",
            "adapter_version": TRACE_ADAPTER_VERSION,
            "generated_at": now_utc_iso(),
            "source_document_ref": source_document.document_ref,
            "source_document_id": source_document.document_id,
        },
        uncertainty={
            "requires_human_admission": True,
            "connector_is_not_claim_graph_owner": True,
            "anchor_status_counts": dict(anchor_status_counts),
        },
        mapping_report=mapping_report,
    )
    return TraceDocumentIngestResult(bundle=bundle)


__all__ = [
    "TRACE_ADAPTER_VERSION",
    "build_default_graph_policy",
    "build_inline_source_document",
    "ingest_trace_document",
    "load_local_text_document",
    "normalize_citation_anchor",
    "trace_claim_to_canonical",
    "trace_relation_to_canonical",
]
