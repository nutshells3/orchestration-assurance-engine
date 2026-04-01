"""Canonical Python contract bindings generated from the JSON Schemas."""

# --- v1 bindings (unchanged) ---
from .assurance_graph import AssuranceGraph
from .assurance_profile import AssuranceProfile, FormalStatus, Gate
from .claim_graph import ClaimGraph, Status as ClaimStatus
from .pipeline_trace import (
    CandidateEntry,
    CandidateLedger,
    PipelineTraceV1,
    TraceResults,
    UnavailableReason as TraceUnavailableReason,
)
from .pipeline_event import (
    EventClass,
    OmissionReason,
    PipelineEventV1,
    UnavailableReason,
    VerifierDelta,
)
from .trace_sidecar_meta import TraceSidecarMeta
from .prefix_slice import (
    OmissionReason as SliceOmissionReason,
    PrefixSliceTextV1,
    PrefixSliceV1,
)
from .prefix_slice_graph import (
    GraphEdge,
    GraphNode,
    PrefixSliceGraphV1,
    StateGraphV1,
)

# --- v2 bindings ---
from .pipeline_trace_v2 import (
    PipelineTraceV2,
    TraceMeta as TraceMetaV2,
    TraceResults as TraceResultsV2,
    ClaimCandidateEntry as ClaimCandidateEntryV2,
    RelationCandidateEntry as RelationCandidateEntryV2,
    CandidateLedger as CandidateLedgerV2,
    ClaimGraph as ClaimGraphV2,
    ClaimNode,
    ClaimEdge,
    PromotionTransition,
    PropagationTrace,
    VectorScores,
    VectorScoreDelta,
    SoundnessScore as SoundnessScoreV2,
    UnavailableReason as TraceUnavailableReasonV2,
)
from .pipeline_event_v2 import (
    EventClass as EventClassV2,
    EventType as EventTypeV2,
    PipelineEventV2,
    VerifierDelta as VerifierDeltaV2,
)
from .trace_sidecar_meta_v2 import TraceSidecarMetaV2
from .prefix_slice_text_v1 import (
    PrefixSliceTextV1 as PrefixSliceTextV1Frozen,
    OmissionReason as SliceOmissionReasonFrozen,
)
from .prefix_slice_graph_v1 import (
    GraphEdge as GraphEdgeV1,
    GraphNode as GraphNodeV1,
    PrefixSliceGraphV1 as PrefixSliceGraphV1Frozen,
    StateGraphV1 as StateGraphV1Frozen,
)

__all__ = [
    # v1
    "AssuranceGraph",
    "AssuranceProfile",
    "CandidateEntry",
    "CandidateLedger",
    "ClaimGraph",
    "ClaimStatus",
    "EventClass",
    "FormalStatus",
    "Gate",
    "GraphEdge",
    "GraphNode",
    "OmissionReason",
    "PipelineTraceV1",
    "PipelineEventV1",
    "PrefixSliceGraphV1",
    "PrefixSliceTextV1",
    "PrefixSliceV1",
    "SliceOmissionReason",
    "StateGraphV1",
    "TraceResults",
    "TraceSidecarMeta",
    "TraceUnavailableReason",
    "UnavailableReason",
    "VerifierDelta",
    # v2
    "CandidateLedgerV2",
    "ClaimEdge",
    "ClaimCandidateEntryV2",
    "ClaimGraphV2",
    "ClaimNode",
    "EventClassV2",
    "EventTypeV2",
    "GraphEdgeV1",
    "GraphNodeV1",
    "PipelineEventV2",
    "PipelineTraceV2",
    "PrefixSliceGraphV1Frozen",
    "PrefixSliceTextV1Frozen",
    "PromotionTransition",
    "PropagationTrace",
    "RelationCandidateEntryV2",
    "SliceOmissionReasonFrozen",
    "SoundnessScoreV2",
    "StateGraphV1Frozen",
    "TraceMetaV2",
    "TraceResultsV2",
    "TraceSidecarMetaV2",
    "TraceUnavailableReasonV2",
    "VectorScoreDelta",
    "VectorScores",
    "VerifierDeltaV2",
]
