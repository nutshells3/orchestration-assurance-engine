"""Compatibility shim over canonical evidence-connector document ingest adapters."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_evidence_connectors_src() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "packages" / "evidence-connectors" / "src"
        if candidate.exists():
            return candidate
    raise RuntimeError("Could not locate packages/evidence-connectors/src from engine.")


EVIDENCE_CONNECTORS_SRC = resolve_evidence_connectors_src()
if str(EVIDENCE_CONNECTORS_SRC) not in sys.path:
    sys.path.insert(0, str(EVIDENCE_CONNECTORS_SRC))

from formal_claim_evidence_connectors import (  # noqa: E402
    TraceCitationAnchor,
    TraceClaimMapping,
    TraceDocumentClaim,
    TraceDocumentIngestBundle,
    TraceDocumentIngestRequest,
    TraceDocumentIngestResult,
    TraceDocumentMappingReport,
    TraceDocumentRelation,
    TraceEvidenceItem,
    TraceEvaluationEvidence,
    TraceRawUnit,
    TraceRelationMapping,
    TraceSourceDocument,
    TraceUnresolvedReference,
    build_inline_source_document,
    build_uploaded_source_document,
    build_default_graph_policy,
    extract_evaluation_evidence,
    ingest_trace_document,
    load_local_text_document,
    load_uploaded_document,
    normalize_citation_anchor,
    trace_claim_to_canonical,
    trace_relation_to_canonical,
)

__all__ = [
    "TraceCitationAnchor",
    "TraceClaimMapping",
    "TraceDocumentClaim",
    "TraceDocumentIngestBundle",
    "TraceDocumentIngestRequest",
    "TraceDocumentIngestResult",
    "TraceDocumentMappingReport",
    "TraceDocumentRelation",
    "TraceEvidenceItem",
    "TraceEvaluationEvidence",
    "TraceRawUnit",
    "TraceRelationMapping",
    "TraceSourceDocument",
    "TraceUnresolvedReference",
    "build_inline_source_document",
    "build_uploaded_source_document",
    "build_default_graph_policy",
    "extract_evaluation_evidence",
    "ingest_trace_document",
    "load_local_text_document",
    "load_uploaded_document",
    "normalize_citation_anchor",
    "trace_claim_to_canonical",
    "trace_relation_to_canonical",
]
