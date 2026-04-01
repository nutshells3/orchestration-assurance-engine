"""Canonical engine package for the Formal Claim Workbench."""

from .action_dsl import ActionTemplate, ActionVerb, LegalActionMaskBuilder
from .candidate_registry import CandidateRegistry
from .artifact_provenance import (
    ArtifactCanonicity,
    ArtifactProvenanceRef,
    FallbackGuard,
    ProvenanceRegistry,
)
from .audit_rules import (
    AssuranceComputationInput,
    ContractPack,
    compute_assurance_profile,
    emit_contract_pack,
    project_downstream_policy,
)
from .audit_workflow import AuditWorkflowStage, AuditWorkflowState
from .claim_trace_service import ClaimTraceService
from .claim_structuring_workflow import (
    ClaimStructuringStage,
    ClaimStructuringWorkflowState,
)
from .dual_formalization_workflow import (
    DualFormalizationStage,
    DualFormalizationWorkflowState,
)
from .config import ModelSlot, PipelineConfig, ProofProtocolConfig
from .unified_config import (
    UnifiedConfig,
    load_config as load_unified_config,
    to_pipeline_config,
)
from .engine_api import (
    AuditRunResult,
    ClaimStructuringRunResult,
    EngineProjectHandle,
    EngineProjectSnapshot,
    FormalClaimEngineAPI,
    ProfileRecomputeResult,
    ProjectBundleExport,
    PrefixSliceRunResult,
    TraceExportResult,
)
from .event_normalizer import EventNormalizer, PipelineEventV1, StateHasher
from .event_validation import (
    EventValidationError,
    EventValidator,
    validate_event,
    validate_event_stream,
    validate_event_stream_strict,
    validate_event_strict,
)
from .llm_client import LLMClient
from .model_safe_serializer import ModelSafeSerializer
from .models import AssuranceGraph, AssuranceProfile, ClaimGraph, Gate
from .orchestrator import PipelineOrchestrator, PipelineResult
from .phase_assembler import PhaseAssembler
from .prefix_slice_builder import (
    CanonicalStateSerializer,
    PrefixSliceBuilder,
    extract_gold_action_from_event,
)
from .prefix_slice_graph_builder import PrefixSliceGraphBuilder
from .paired_dataset_extractor import PairedDatasetExtractor
from .safeslice_bridge import SafeSliceBridge
from .propagation_capture import PropagationCapture
from .proof_lineage import ProofAuditProvenance, ProofLineageCollector
from .proof_control import ProofControlPlane
from .proof_protocol import FilesystemProofAdapter, ProofProtocolClient
from .promotion_state_machine import (
    PromotionCheckpointState,
    PromotionStateMachine,
    ReviewActorRole,
)
from .store import ArtifactStore
from .trace_export import (
    SidecarMetaWriter,
    TraceExportBuilder,
    TransitionLogWriter,
)
from .certification_api import (
    CertificationResult,
    CertificationVerdict,
    VerificationResult,
    certified,
    get_config,
    verify_only,
)
from .certification_http import serve as serve_certification_http

__all__ = [
    "PipelineConfig",
    "ModelSlot",
    "ProofProtocolConfig",
    "ClaimGraph",
    "AssuranceGraph",
    "AssuranceProfile",
    "AuditRunResult",
    "ClaimStructuringRunResult",
    "EngineProjectHandle",
    "EngineProjectSnapshot",
    "EventNormalizer",
    "EventValidationError",
    "EventValidator",
    "FormalClaimEngineAPI",
    "Gate",
    "ArtifactStore",
    "AssuranceComputationInput",
    "AuditWorkflowStage",
    "AuditWorkflowState",
    "ContractPack",
    "ClaimTraceService",
    "ClaimStructuringStage",
    "ClaimStructuringWorkflowState",
    "DualFormalizationStage",
    "DualFormalizationWorkflowState",
    "compute_assurance_profile",
    "emit_contract_pack",
    "ModelSafeSerializer",
    "PipelineEventV1",
    "ActionTemplate",
    "ActionVerb",
    "ArtifactCanonicity",
    "ArtifactProvenanceRef",
    "CandidateRegistry",
    "CanonicalStateSerializer",
    "extract_gold_action_from_event",
    "FallbackGuard",
    "LegalActionMaskBuilder",
    "PhaseAssembler",
    "PipelineOrchestrator",
    "PrefixSliceRunResult",
    "PrefixSliceBuilder",
    "PrefixSliceGraphBuilder",
    "PairedDatasetExtractor",
    "PropagationCapture",
    "ProvenanceRegistry",
    "ProofAuditProvenance",
    "ProofLineageCollector",
    "PipelineResult",
    "FilesystemProofAdapter",
    "ProofControlPlane",
    "ProofProtocolClient",
    "PromotionCheckpointState",
    "PromotionStateMachine",
    "ProfileRecomputeResult",
    "ProjectBundleExport",
    "ReviewActorRole",
    "SidecarMetaWriter",
    "StateHasher",
    "TraceExportBuilder",
    "TraceExportResult",
    "TransitionLogWriter",
    "SafeSliceBridge",
    "project_downstream_policy",
    "validate_event",
    "validate_event_stream",
    "validate_event_stream_strict",
    "validate_event_strict",
    "LLMClient",
    "UnifiedConfig",
    "load_unified_config",
    "to_pipeline_config",
    "CertificationResult",
    "CertificationVerdict",
    "VerificationResult",
    "certified",
    "get_config",
    "verify_only",
    "serve_certification_http",
]
