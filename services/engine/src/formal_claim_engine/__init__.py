"""Canonical engine package for the Formal Claim Workbench."""

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
)
from .llm_client import LLMClient
from .models import AssuranceGraph, AssuranceProfile, ClaimGraph, Gate
from .orchestrator import PipelineOrchestrator, PipelineResult
from .proof_control import ProofControlPlane
from .proof_protocol import FilesystemProofAdapter, ProofProtocolClient
from .promotion_state_machine import (
    PromotionCheckpointState,
    PromotionStateMachine,
    ReviewActorRole,
)
from .store import ArtifactStore
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
    "PipelineOrchestrator",
    "PipelineResult",
    "FilesystemProofAdapter",
    "ProofControlPlane",
    "ProofProtocolClient",
    "PromotionCheckpointState",
    "PromotionStateMachine",
    "ProfileRecomputeResult",
    "ProjectBundleExport",
    "ReviewActorRole",
    "project_downstream_policy",
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
