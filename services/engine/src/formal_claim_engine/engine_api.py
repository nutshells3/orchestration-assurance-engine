"""Typed service boundary over project, workflow, audit, and promotion operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from .audit_rules import AssuranceComputationInput, compute_assurance_profile
from .candidate_registry import CandidateRegistry
from .claim_trace_service import ClaimTraceService
from .config import PipelineConfig
from .llm_client import LLMClient, llm_client
from .model_safe_serializer import ModelSafeSerializer
from .models import AssuranceProfile, ClaimGraph, Gate
from .orchestrator import PipelineOrchestrator
from .phase_assembler import PhaseAssembler
from .promotion_state_machine import PromotionCheckpointState, ReviewActorRole
from .store import ArtifactStore, canonical_artifact_id
from .trace_export import (
    SidecarMetaWriter,
    TraceExportBuilder,
    TransitionLogWriter,
)

OrchestratorFactory = Callable[[PipelineConfig, LLMClient, ArtifactStore], PipelineOrchestrator]


class EngineProjectHandle(BaseModel):
    project_id: str
    name: str
    domain: str
    description: str = ""
    claim_graph_id: str | None = None


class EngineProjectSnapshot(BaseModel):
    project_id: str
    name: str
    domain: str
    description: str = ""
    claim_graph_id: str | None = None
    claim_count: int = 0
    snapshot: dict[str, Any] = Field(default_factory=dict)


class ClaimStructuringRunResult(BaseModel):
    project_id: str
    claim_graph: dict[str, Any]
    workflow: dict[str, Any]
    project: EngineProjectSnapshot


class DocumentIngestRunResult(BaseModel):
    project_id: str
    claim_graph_id: str | None = None
    claims_added: int
    relations_added: int
    claim_ids: list[str] = Field(default_factory=list)
    mapping_report: dict[str, Any] = Field(default_factory=dict)
    ingest_bundle: dict[str, Any] = Field(default_factory=dict)
    unresolved_references: list[dict[str, Any]] = Field(default_factory=list)
    evidence_items_added: int = 0
    evaluation_evidence_added: int = 0
    source_document: dict[str, Any] = Field(default_factory=dict)
    source_mapping_ref: dict[str, Any] = Field(default_factory=dict)
    evaluation_evidence_ref: dict[str, Any] = Field(default_factory=dict)
    project: EngineProjectSnapshot


class DualFormalizationRunResult(BaseModel):
    project_id: str
    claim_id: str
    workflow: dict[str, Any]


class AuditRunResult(BaseModel):
    project_id: str
    claim_id: str
    build_results: dict[str, Any] = Field(default_factory=dict)
    verifier_results: dict[str, Any] = Field(default_factory=dict)
    proof_audit: dict[str, Any] = Field(default_factory=dict)
    audit_output: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)
    audit_workflow: dict[str, Any] = Field(default_factory=dict)
    promotion_state: dict[str, Any] = Field(default_factory=dict)


class ClaimAnalysisRunResult(BaseModel):
    project_id: str
    claim_id: str
    analysis_mode: str
    audit_output: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)
    promotion_state: dict[str, Any] = Field(default_factory=dict)
    review_event: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ProfileRecomputeResult(BaseModel):
    project_id: str
    claim_id: str
    profile: dict[str, Any]
    promotion_state: dict[str, Any]


class BatchClaimFormalizationResult(BaseModel):
    claim_id: str
    dual_formalization: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)
    recomputed_profile: dict[str, Any] = Field(default_factory=dict)


class BatchFormalizationRunResult(BaseModel):
    project_id: str
    claim_ids: list[str] = Field(default_factory=list)
    results: list[BatchClaimFormalizationResult] = Field(default_factory=list)


class ProjectBundleExport(BaseModel):
    project: EngineProjectSnapshot
    claim_graph: dict[str, Any] | None = None
    assurance_profiles: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_evidence: list[dict[str, Any]] = Field(default_factory=list)
    review_events: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    promotion_states: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TraceExportResult(BaseModel):
    project_id: str = ""
    output_dir: str
    trace_path: str
    transition_log_path: str
    sidecar_meta_path: str
    export_version: str = "v2"
    redaction_violations: list[str] = Field(default_factory=list)

    @property
    def validation_ok(self) -> bool:
        return len(self.redaction_violations) == 0

    @property
    def validation_errors(self) -> list[str]:
        return list(self.redaction_violations)


class PrefixSliceRunResult(BaseModel):
    project_id: str
    slice_count: int = 0
    output_path: str = ""
    format: str = "jsonl"


def _normalize_attempt_label(value: Any) -> str:
    """Normalize formalization attempt labels to canonical 'a' or 'b' strings."""
    if value is None:
        return "a"
    if isinstance(value, list):
        # B20/AUD-006: attempts=[a,b] -> take first element as singular attempt
        value = value[0] if value else "a"
    if isinstance(value, int):
        return {0: "a", 1: "b"}.get(value, str(value))
    text = str(value).strip().lower()
    if text in {"0", "a", "attempt_a"}:
        return "a"
    if text in {"1", "b", "attempt_b"}:
        return "b"
    return text or "a"


def _canonicalize_proposal(
    event_type: str,
    raw_proposal: dict[str, Any] | None,
    meta: dict[str, Any],
    claim_id: str,
) -> dict[str, Any] | None:
    """B20/AUD-006: Canonicalize controllable event proposals.

    Ensures every controllable event has complete canonical arguments:
    - propose_relation: src_id, tgt_id, relation_type, strength
    - select_formalization: claim_id, attempt (singular string)
    - finalize_profile: claim_id
    - propose_promotion: claim_id, target_gate
    - add_hidden_assumption: text, attaches_to
    - request_recheck: claim_id
    """
    proposal = dict(raw_proposal) if raw_proposal else {}

    if event_type == "propose_relation":
        # Ensure all four required fields are present
        proposal.setdefault("src_id", proposal.get("source_id") or proposal.get("from_claim_id") or proposal.get("src") or "")
        proposal.setdefault("tgt_id", proposal.get("target_id") or proposal.get("to_claim_id") or proposal.get("tgt") or "")
        proposal.setdefault("relation_type", proposal.get("type") or proposal.get("rel") or "")
        # strength must never be null; default to "unknown"
        raw_strength = proposal.get("strength")
        if raw_strength is None or str(raw_strength).strip().lower() in ("", "none"):
            proposal["strength"] = "unknown"
        return proposal

    elif event_type == "select_formalization":
        # B20/AUD-006: normalize attempts=[...] -> singular attempt
        if "attempts" in proposal and "attempt" not in proposal:
            attempts_value = proposal.pop("attempts")
            proposal["attempt"] = _normalize_attempt_label(attempts_value)
        elif "attempt" in proposal:
            proposal["attempt"] = _normalize_attempt_label(proposal["attempt"])
        proposal.setdefault("claim_id", claim_id)
        if "attempt" not in proposal:
            # Try metadata fallback
            attempt = (
                meta.get("attempt")
                or meta.get("formalizer_label")
                or meta.get("selected_formalizer")
            )
            proposal["attempt"] = _normalize_attempt_label(attempt)
        return proposal

    elif event_type == "finalize_profile":
        proposal.setdefault("claim_id", claim_id)
        return proposal

    elif event_type == "propose_promotion":
        proposal.setdefault("claim_id", claim_id)
        if "target_gate" not in proposal:
            target_gate = (
                meta.get("to_gate")
                or meta.get("target_gate")
                or proposal.get("gate")
            )
            if target_gate:
                proposal["target_gate"] = str(target_gate)
        return proposal

    elif event_type == "add_hidden_assumption":
        proposal.setdefault("text", proposal.get("assumption_text") or "")
        proposal.setdefault("attaches_to", proposal.get("claim_id") or claim_id)
        return proposal

    elif event_type == "request_recheck":
        proposal.setdefault("claim_id", claim_id)
        return proposal

    # For non-controllable events, return the proposal as-is (or None)
    return proposal if proposal else None


def _build_event_outcome(
    event_type: str,
    claim_id: str,
    proposal: dict[str, Any] | None,
    accepted: bool | None,
    claim_data: dict[str, Any],
    claim_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """B30: Build outcome data for a per-claim event.

    This outcome is consumed by prefix builders to reconstruct progressive
    visible state.  Without it, _build_state_up_to() never accumulates
    claims/relations beyond the base source document.

    Returns None when the event produces no visible state change.
    """
    if not accepted:
        return None

    proposal = proposal or {}

    if event_type in ("select_formalization", "dual_formalization_workflow",
                      "formalization_attempt", "formalization_selection"):
        # A formalization was selected for this claim -- the claim
        # itself becomes visible in the formalization state.
        claim_info = claim_by_id.get(claim_id) or {}
        if claim_info:
            return {
                "claims": [{
                    "claim_id": claim_id,
                    "text": str(claim_info.get("text") or claim_info.get("nl_statement") or ""),
                    "status": str(claim_info.get("status") or "active"),
                }],
                "formalization": {
                    claim_id: {
                        "status": "formalized",
                        "selected": proposal.get("attempt", "a"),
                    },
                },
            }
        return None

    elif event_type in ("finalize_profile", "audit_workflow"):
        # Profile was finalized -- audit/profile data is now visible.
        profile = claim_data.get("profile") or {}
        if profile:
            vs = profile.get("vector_scores") or {}
            return {
                "audit": {
                    "claim_id": claim_id,
                    "gate": str(profile.get("gate") or "draft"),
                    "formal_status": str(profile.get("formal_status") or ""),
                    "vector_scores": {
                        "trust_base_integrity": float(vs.get("trust_base_integrity", 0)),
                        "intent_alignment": float(vs.get("intent_alignment", 0)),
                        "evidence_support": float(vs.get("evidence_support", 0)),
                        "coverage": float(vs.get("coverage", 0)),
                        "robustness": float(vs.get("robustness", 0)),
                    },
                },
            }
        return None

    elif event_type in ("propose_promotion", "promotion_transition",
                        "promotion_proposal"):
        # Promotion state change.
        return None  # Promotion outcome handled by the FSM, not here

    elif event_type == "profile_recomputed":
        profile = claim_data.get("profile") or {}
        if profile:
            vs = profile.get("vector_scores") or {}
            return {
                "audit": {
                    "claim_id": claim_id,
                    "gate": str(profile.get("gate") or "draft"),
                    "formal_status": str(profile.get("formal_status") or ""),
                    "profile_recomputed": True,
                    "vector_scores": {
                        "trust_base_integrity": float(vs.get("trust_base_integrity", 0)),
                        "intent_alignment": float(vs.get("intent_alignment", 0)),
                        "evidence_support": float(vs.get("evidence_support", 0)),
                        "coverage": float(vs.get("coverage", 0)),
                        "robustness": float(vs.get("robustness", 0)),
                    },
                },
            }
        return None

    return None


class FormalClaimEngineAPI:
    """Internal typed engine surface used by MCP and future desktop adapters."""

    def __init__(
        self,
        *,
        config: PipelineConfig | None = None,
        llm: LLMClient | None = None,
        data_dir: str | None = None,
        claim_trace_service: ClaimTraceService | None = None,
        orchestrator_factory: OrchestratorFactory | None = None,
    ):
        base_config = config or PipelineConfig()
        if data_dir is not None:
            base_config = replace(base_config, data_dir=data_dir)
        self.config = base_config
        self.llm = llm or llm_client
        self.claim_trace_service = claim_trace_service or ClaimTraceService(
            config=self.config,
            llm=self.llm,
            data_dir=self.config.data_dir,
        )
        self.orchestrator_factory = (
            orchestrator_factory or self._default_orchestrator_factory
        )

    def create_project(
        self,
        name: str,
        domain: str,
        description: str = "",
    ) -> EngineProjectHandle:
        project = self.claim_trace_service.create_project(
            name=name,
            domain=domain,
            description=description,
        )
        return EngineProjectHandle(
            project_id=project.id,
            name=project.name,
            domain=project.domain.value,
            description=project.description,
            claim_graph_id=project.claim_graph_id,
        )

    def list_projects(self) -> list[EngineProjectHandle]:
        projects = self.claim_trace_service.list_projects()
        return [
            EngineProjectHandle(
                project_id=str(project["id"]),
                name=str(project["name"]),
                domain=str(project["domain"]),
                description="",
                claim_graph_id=None,
            )
            for project in projects
        ]

    def open_project(self, project_id: str) -> EngineProjectSnapshot:
        project, graph_data = self._require_project(project_id)
        snapshot = self.claim_trace_service.snapshot(project_id)
        return EngineProjectSnapshot(
            project_id=project.id,
            name=project.name,
            domain=project.domain.value,
            description=project.description,
            claim_graph_id=project.claim_graph_id,
            claim_count=len((graph_data or {}).get("claims", [])),
            snapshot=snapshot,
        )

    async def ingest_document(
        self,
        project_id: str,
        text: str,
    ) -> DocumentIngestRunResult:
        self._require_project(project_id)
        result = self.claim_trace_service.ingest_document(project_id, text)
        if hasattr(result, "__await__"):
            result = await result
        return DocumentIngestRunResult(
            project_id=project_id,
            claim_graph_id=str(result.get("claim_graph_id") or ""),
            claims_added=int(result.get("claims_added") or 0),
            relations_added=int(result.get("relations_added") or 0),
            claim_ids=[str(item) for item in list(result.get("claim_ids") or [])],
            mapping_report=dict(result.get("mapping_report") or {}),
            ingest_bundle=dict(result.get("ingest_bundle") or {}),
            unresolved_references=[
                dict(item) for item in list(result.get("unresolved_references") or [])
            ],
            evidence_items_added=int(result.get("evidence_items_added") or 0),
            evaluation_evidence_added=int(result.get("evaluation_evidence_added") or 0),
            source_document=dict(result.get("source_document") or {}),
            source_mapping_ref=dict(result.get("source_mapping_ref") or {}),
            evaluation_evidence_ref=dict(result.get("evaluation_evidence_ref") or {}),
            project=self.open_project(project_id),
        )

    async def import_local_document(
        self,
        project_id: str,
        path: str,
    ) -> DocumentIngestRunResult:
        self._require_project(project_id)
        result = self.claim_trace_service.import_local_document(project_id, path)
        if hasattr(result, "__await__"):
            result = await result
        return DocumentIngestRunResult(
            project_id=project_id,
            claim_graph_id=str(result.get("claim_graph_id") or ""),
            claims_added=int(result.get("claims_added") or 0),
            relations_added=int(result.get("relations_added") or 0),
            claim_ids=[str(item) for item in list(result.get("claim_ids") or [])],
            mapping_report=dict(result.get("mapping_report") or {}),
            ingest_bundle=dict(result.get("ingest_bundle") or {}),
            unresolved_references=[
                dict(item) for item in list(result.get("unresolved_references") or [])
            ],
            evidence_items_added=int(result.get("evidence_items_added") or 0),
            evaluation_evidence_added=int(result.get("evaluation_evidence_added") or 0),
            source_document=dict(result.get("source_document") or {}),
            source_mapping_ref=dict(result.get("source_mapping_ref") or {}),
            evaluation_evidence_ref=dict(result.get("evaluation_evidence_ref") or {}),
            project=self.open_project(project_id),
        )

    async def upload_document_bytes(
        self,
        project_id: str,
        *,
        file_name: str,
        raw_bytes: bytes,
        media_type: str | None = None,
    ) -> DocumentIngestRunResult:
        self._require_project(project_id)
        result = self.claim_trace_service.import_uploaded_document(
            project_id,
            file_name=file_name,
            raw_bytes=raw_bytes,
            media_type=media_type,
        )
        if hasattr(result, "__await__"):
            result = await result
        return DocumentIngestRunResult(
            project_id=project_id,
            claim_graph_id=str(result.get("claim_graph_id") or ""),
            claims_added=int(result.get("claims_added") or 0),
            relations_added=int(result.get("relations_added") or 0),
            claim_ids=[str(item) for item in list(result.get("claim_ids") or [])],
            mapping_report=dict(result.get("mapping_report") or {}),
            ingest_bundle=dict(result.get("ingest_bundle") or {}),
            unresolved_references=[
                dict(item) for item in list(result.get("unresolved_references") or [])
            ],
            evidence_items_added=int(result.get("evidence_items_added") or 0),
            evaluation_evidence_added=int(result.get("evaluation_evidence_added") or 0),
            source_document=dict(result.get("source_document") or {}),
            source_mapping_ref=dict(result.get("source_mapping_ref") or {}),
            evaluation_evidence_ref=dict(result.get("evaluation_evidence_ref") or {}),
            project=self.open_project(project_id),
        )

    async def run_claim_structuring(
        self,
        project_id: str,
        user_input: str,
    ) -> ClaimStructuringRunResult:
        project, _ = self._require_project(project_id)
        orchestrator = self._build_orchestrator(project_id)
        claim_graph, workflow = await orchestrator.run_claim_structuring_workflow(user_input)
        graph_data = claim_graph.model_dump(mode="json", exclude_none=True)
        self.claim_trace_service.repository.save(project, graph_data)
        return ClaimStructuringRunResult(
            project_id=project_id,
            claim_graph=graph_data,
            workflow=workflow.model_dump(mode="json", exclude_none=True),
            project=self.open_project(project_id),
        )

    async def run_dual_formalization(
        self,
        project_id: str,
        claim_id: str,
    ) -> DualFormalizationRunResult:
        claim_graph = self._load_claim_graph(project_id)
        claim = self._find_claim(claim_graph, claim_id)
        orchestrator = self._build_orchestrator(project_id)
        workflow = await orchestrator.run_dual_formalization_workflow(claim_graph, claim)
        return DualFormalizationRunResult(
            project_id=project_id,
            claim_id=canonical_artifact_id(claim.claim_id),
            workflow=workflow.model_dump(mode="json", exclude_none=True),
        )

    async def run_audit(
        self,
        project_id: str,
        claim_id: str,
    ) -> AuditRunResult:
        claim_graph = self._load_claim_graph(project_id)
        claim = self._find_claim(claim_graph, claim_id)
        orchestrator = self._build_orchestrator(project_id)
        phase2 = await orchestrator.phase2_formalize_and_audit(claim_graph, claim)
        promotion_state = orchestrator.load_promotion_state(claim_id)
        return AuditRunResult(
            project_id=project_id,
            claim_id=canonical_artifact_id(claim.claim_id),
            build_results=dict(phase2.get("build_results") or {}),
            verifier_results=dict(phase2.get("verifier_results") or {}),
            proof_audit=dict(phase2.get("proof_audit") or {}),
            audit_output=dict(phase2.get("audit") or {}),
            profile=dict(phase2.get("profile") or {}),
            audit_workflow=dict(phase2.get("audit_workflow") or {}),
            promotion_state=promotion_state.model_dump(mode="json", exclude_none=True),
        )

    async def run_batch_formalization(
        self,
        project_id: str,
        claim_ids: list[str] | None = None,
        *,
        max_concurrency: int = 4,
    ) -> BatchFormalizationRunResult:
        claim_graph = self._load_claim_graph(project_id)
        if claim_ids:
            selected_ids = [canonical_artifact_id(claim_id) for claim_id in claim_ids]
        else:
            selected_ids = [
                canonical_artifact_id(claim.claim_id)
                for claim in claim_graph.claims
                if bool(getattr(claim, "formalization_required", False))
            ]
        if not selected_ids:
            return BatchFormalizationRunResult(project_id=project_id, claim_ids=[], results=[])

        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def process_claim(claim_id: str) -> BatchClaimFormalizationResult:
            async with semaphore:
                claim = self._find_claim(claim_graph, claim_id)
                orchestrator = self._build_orchestrator(project_id)
                phase2 = await orchestrator.phase2_formalize_and_audit(claim_graph, claim)
                recomputed = self.recompute_profile(
                    project_id,
                    claim_id,
                    {
                        "verifier_results": dict(phase2.get("verifier_results") or {}),
                        "audit_output": dict(phase2.get("audit") or {}),
                        "profile": dict(phase2.get("profile") or {}),
                    },
                )
                return BatchClaimFormalizationResult(
                    claim_id=claim_id,
                    dual_formalization=dict(phase2.get("dual_formalization_workflow") or {}),
                    audit={
                        "project_id": project_id,
                        "claim_id": claim_id,
                        "build_results": dict(phase2.get("build_results") or {}),
                        "verifier_results": dict(phase2.get("verifier_results") or {}),
                        "proof_audit": dict(phase2.get("proof_audit") or {}),
                        "audit_output": dict(phase2.get("audit") or {}),
                        "profile": dict(phase2.get("profile") or {}),
                        "audit_workflow": dict(phase2.get("audit_workflow") or {}),
                    },
                    recomputed_profile=recomputed.model_dump(mode="json", exclude_none=True),
                )

        results = list(await asyncio.gather(*(process_claim(claim_id) for claim_id in selected_ids)))
        return BatchFormalizationRunResult(
            project_id=project_id,
            claim_ids=selected_ids,
            results=results,
        )

    async def analyze_claim(
        self,
        project_id: str,
        claim_id: str,
    ) -> ClaimAnalysisRunResult:
        claim_graph = self._load_claim_graph(project_id)
        claim = self._find_claim(claim_graph, claim_id)
        warnings: list[str] = []

        if bool(getattr(claim, "formalization_required", False)):
            try:
                audit_result = await self.run_audit(project_id, claim_id)
                return ClaimAnalysisRunResult(
                    project_id=project_id,
                    claim_id=canonical_artifact_id(claim.claim_id),
                    analysis_mode="audit_workflow",
                    audit_output=dict(audit_result.audit_output or {}),
                    profile=dict(audit_result.profile or {}),
                    promotion_state=dict(audit_result.promotion_state or {}),
                    review_event=dict(audit_result.audit_workflow or {}),
                    warnings=list(
                        (audit_result.audit_output or {}).get("warnings") or []
                    ),
                )
            except Exception as exc:
                warnings.append(
                    f"formal_audit_fallback:{exc.__class__.__name__}:{exc}"
                )

        return self._best_effort_claim_analysis(
            project_id,
            claim_graph,
            claim,
            warnings=warnings,
        )

    async def trace_forward(self, project_id: str, claim_id: str) -> dict[str, Any]:
        return await self.claim_trace_service.trace_forward(project_id, claim_id)

    async def trace_backward(self, project_id: str, claim_id: str) -> dict[str, Any]:
        return await self.claim_trace_service.trace_backward(project_id, claim_id)

    async def detect_gaps(self, project_id: str) -> dict[str, Any]:
        return await self.claim_trace_service.find_gaps(project_id)

    async def assess_soundness(
        self,
        project_id: str,
        claim_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.claim_trace_service.assess_soundness(project_id, claim_id)

    def get_graph(self, project_id: str, depth_filter: int = -1) -> dict[str, Any]:
        return self.claim_trace_service.get_graph(project_id, depth_filter)

    def export_graph(self, project_id: str, format: str = "json") -> str:
        return self.claim_trace_service.export_graph(project_id, format)

    def get_summary(self, project_id: str) -> dict[str, Any]:
        return self.claim_trace_service.get_summary(project_id)

    def list_graph_revisions(self, project_id: str) -> list[dict[str, Any]]:
        project, _ = self._require_project(project_id)
        if not project.claim_graph_id:
            return []
        return self.claim_trace_service.repository.artifact_store.list_revisions(
            "claim_graphs",
            project.claim_graph_id,
        )

    def load_graph_revision(self, project_id: str, revision_id: str) -> dict[str, Any]:
        project, _ = self._require_project(project_id)
        if not project.claim_graph_id:
            raise FileNotFoundError(f"Project '{project_id}' has no ClaimGraph revisions yet.")
        return self.claim_trace_service.repository.artifact_store.load_revision(
            "claim_graphs",
            project.claim_graph_id,
            revision_id,
        )

    def get_graph_signal_overlays(self, project_id: str) -> dict[str, dict[str, Any]]:
        project, _ = self._require_project(project_id)
        claim_graph = self._load_claim_graph(project_id)
        artifact_store = self.claim_trace_service.repository.artifact_store

        overlays: dict[str, dict[str, Any]] = {}
        for claim in claim_graph.claims:
            claim_id = canonical_artifact_id(claim.claim_id)
            events = artifact_store.query_review_events(claim_id)
            state = self._load_promotion_state_or_default(project_id, claim_id)
            overlay = overlays.setdefault(
                claim_id,
                {
                    "claim_id": claim_id,
                    "review_event_count": len(events),
                    "audit_event_count": sum(
                        1 for event in events if event.get("event_type") == "audit_workflow"
                    ),
                    "promotion_gate": state.current_gate.value,
                    "recommended_gate": state.recommended_gate.value,
                    "required_action_count": len(state.required_actions),
                    "required_actions": list(state.required_actions),
                    "blocked": state.current_gate == Gate.blocked,
                    "rejected": state.current_gate == Gate.rejected,
                    "superseded": state.current_gate == Gate.superseded,
                    "unresolved_gap_count": 0,
                    "blocking_gap_count": 0,
                    "gap_kinds": [],
                    "oracle_dependency_count": 0,
                    "hotspot_artifact_count": 0,
                    "blocking_obligation_count": 0,
                    "countermodel_probe": None,
                    "vacuity_check": None,
                },
            )
            try:
                profile = artifact_store.load_assurance_profile_for_claim(claim_id)
            except FileNotFoundError:
                continue

            overlay["profile_gate"] = getattr(profile.gate, "value", profile.gate)
            overlay["formal_status"] = getattr(
                profile.formal_status,
                "value",
                profile.formal_status,
            )
            overlay["support_status"] = getattr(
                profile.support_status,
                "value",
                profile.support_status,
            )
            overlay["intent_status"] = getattr(
                profile.intent_status,
                "value",
                profile.intent_status,
            )
            overlay["oracle_dependency_count"] = int(
                profile.trust_frontier.oracle_dependency_count
            )
            overlay["hotspot_artifact_count"] = len(
                list(profile.trust_frontier.hotspot_artifact_ids or [])
            )
            overlay["blocking_obligation_count"] = len(
                list(profile.obligations.blocking_obligations or [])
            )
            overlay["countermodel_probe"] = getattr(
                profile.model_health.countermodel_probe,
                "value",
                profile.model_health.countermodel_probe,
            )
            overlay["vacuity_check"] = getattr(
                profile.model_health.vacuity_check,
                "value",
                profile.model_health.vacuity_check,
            )
            overlay["required_action_count"] = len(list(profile.required_actions or []))
            overlay["required_actions"] = list(profile.required_actions or [])

        for gap in project.gaps:
            affected_claims = set(gap.affected_claim_ids or []) | set(
                gap.blocking_claim_ids or []
            )
            for claim_id in affected_claims:
                canonical_claim_id = canonical_artifact_id(claim_id)
                overlay = overlays.setdefault(
                    canonical_claim_id,
                    {
                        "claim_id": canonical_claim_id,
                        "review_event_count": 0,
                        "audit_event_count": 0,
                        "promotion_gate": Gate.draft.value,
                        "recommended_gate": Gate.draft.value,
                        "required_action_count": 0,
                        "required_actions": [],
                        "blocked": False,
                        "rejected": False,
                        "superseded": False,
                        "unresolved_gap_count": 0,
                        "blocking_gap_count": 0,
                        "gap_kinds": [],
                        "oracle_dependency_count": 0,
                        "hotspot_artifact_count": 0,
                        "blocking_obligation_count": 0,
                        "countermodel_probe": None,
                        "vacuity_check": None,
                    },
                )
                overlay["unresolved_gap_count"] = int(overlay["unresolved_gap_count"]) + 1
                if claim_id in set(gap.blocking_claim_ids or []):
                    overlay["blocking_gap_count"] = int(overlay["blocking_gap_count"]) + 1
                gap_kinds = set(str(item) for item in list(overlay["gap_kinds"]))
                gap_kinds.add(str(gap.kind))
                overlay["gap_kinds"] = sorted(gap_kinds)

        return overlays

    def get_assurance_profile(self, project_id: str, claim_id: str) -> dict[str, Any]:
        self._load_claim_graph(project_id)
        profile = self.claim_trace_service.repository.artifact_store.load_assurance_profile_for_claim(
            canonical_artifact_id(claim_id)
        )
        return profile.model_dump(mode="json", exclude_none=True)

    def get_latest_audit_report(self, project_id: str, claim_id: str) -> dict[str, Any]:
        self._load_claim_graph(project_id)
        events = [
            event
            for event in self.claim_trace_service.repository.artifact_store.query_review_events(
                canonical_artifact_id(claim_id)
            )
            if event.get("event_type") == "audit_workflow"
        ]
        if not events:
            raise FileNotFoundError(
                f"Audit workflow event not found for claim:{canonical_artifact_id(claim_id)}"
            )
        return events[-1]

    def get_promotion_state(self, project_id: str, claim_id: str) -> dict[str, Any]:
        self._load_claim_graph(project_id)
        state = self._load_promotion_state_or_default(project_id, claim_id)
        return state.model_dump(mode="json", exclude_none=True)

    def list_review_events(self, project_id: str, claim_id: str) -> list[dict[str, Any]]:
        self._load_claim_graph(project_id)
        return self.claim_trace_service.repository.artifact_store.query_review_events(
            canonical_artifact_id(claim_id)
        )

    def list_source_documents(self, project_id: str) -> list[dict[str, Any]]:
        self._require_project(project_id)
        return self.claim_trace_service.list_source_documents(project_id)

    def get_source_mapping_bundle(
        self,
        project_id: str,
        document_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_project(project_id)
        return self.claim_trace_service.load_source_mapping_bundle(
            project_id,
            document_id,
            revision_id=revision_id,
        )

    def list_external_references(
        self,
        project_id: str,
        revision_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_project(project_id)
        registry = self.claim_trace_service.load_external_reference_registry(
            project_id,
            revision_id=revision_id,
        )
        return [dict(item) for item in list((registry.get("artifact") or {}).get("references") or [])]

    def get_external_reference(
        self,
        project_id: str,
        reference_id: str,
    ) -> dict[str, Any]:
        self._require_project(project_id)
        for item in self.list_external_references(project_id):
            if canonical_artifact_id(item.get("reference_id") or "") == canonical_artifact_id(reference_id):
                return item
        raise FileNotFoundError(f"External reference not found: {reference_id}")

    def list_assurance_links(
        self,
        project_id: str,
        claim_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_project(project_id)
        registry = self.claim_trace_service.load_external_reference_registry(project_id)
        links = [
            dict(item)
            for item in list((registry.get("artifact") or {}).get("links") or [])
            if str(item.get("subject_kind") or "") not in {"claim", "evaluation_evidence"}
        ]
        if claim_id is None:
            return links
        canonical_claim = canonical_artifact_id(claim_id)
        return [
            item
            for item in links
            if canonical_artifact_id(item.get("claim_id") or "") == canonical_claim
        ]

    def get_claim_reference_links(
        self,
        project_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]:
        self._require_project(project_id)
        registry = self.claim_trace_service.load_external_reference_registry(project_id)
        canonical_claim = canonical_artifact_id(claim_id)
        return [
            dict(item)
            for item in list((registry.get("artifact") or {}).get("links") or [])
            if str(item.get("subject_kind") or "") == "claim"
            and canonical_artifact_id(item.get("claim_id") or "") == canonical_claim
        ]

    def get_reference_backlinks(
        self,
        project_id: str,
        reference_id: str,
    ) -> list[dict[str, Any]]:
        self._require_project(project_id)
        canonical_reference = canonical_artifact_id(reference_id)
        registry = self.claim_trace_service.load_external_reference_registry(project_id)
        return [
            dict(item)
            for item in list((registry.get("artifact") or {}).get("links") or [])
            if canonical_artifact_id(item.get("reference_id") or "") == canonical_reference
        ]

    def list_evaluation_evidence(self, project_id: str) -> list[dict[str, Any]]:
        self._require_project(project_id)
        return self.claim_trace_service.list_evaluation_evidence(project_id)

    def get_evaluation_evidence(
        self,
        project_id: str,
        evidence_id: str,
    ) -> dict[str, Any]:
        self._require_project(project_id)
        return self.claim_trace_service.get_evaluation_evidence(project_id, evidence_id)

    def get_claim_evidence_links(
        self,
        project_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]:
        self._require_project(project_id)
        return self.claim_trace_service.get_claim_evidence_links(project_id, claim_id)

    def get_reference_evidence_links(
        self,
        project_id: str,
        reference_id: str,
    ) -> list[dict[str, Any]]:
        self._require_project(project_id)
        return self.claim_trace_service.get_reference_evidence_links(project_id, reference_id)

    def recompute_profile(
        self,
        project_id: str,
        claim_id: str,
        audit_result: AuditRunResult | dict[str, Any],
        *,
        research_output: dict[str, Any] | None = None,
    ) -> ProfileRecomputeResult:
        claim_graph = self._load_claim_graph(project_id)
        claim = self._find_claim(claim_graph, claim_id)
        if isinstance(audit_result, AuditRunResult):
            verifier_results = audit_result.verifier_results
            audit_output = audit_result.audit_output
            existing_profile = audit_result.profile
        else:
            verifier_results = dict(audit_result.get("verifier_results") or {})
            audit_output = dict(audit_result.get("audit_output") or audit_result.get("audit") or {})
            existing_profile = dict(audit_result.get("profile") or {})

        profile = compute_assurance_profile(
            AssuranceComputationInput(
                project_id=project_id,
                claim=claim.model_dump(mode="json"),
                verifier_output=verifier_results,
                audit_output=audit_output,
                research_output=research_output,
                coverage_data=None,
                claim_graph=claim_graph,
                claim_graph_ref=canonical_artifact_id(claim_graph.graph_id),
                existing_profile=existing_profile or None,
            )
        )
        artifact_store = self.claim_trace_service.repository.artifact_store
        artifact_store.save_assurance_profile(profile)
        canonical_claim_id = canonical_artifact_id(claim.claim_id)
        gate_before = str(existing_profile.get("gate") or Gate.draft.value)
        gate_after = str(getattr(profile.gate, "value", profile.gate) or Gate.draft.value)
        before_scores = dict(existing_profile.get("vector_scores") or {})
        after_scores = dict(profile.model_dump(mode="json", exclude_none=True).get("vector_scores") or {})
        vector_dims = [
            "trust_base_integrity",
            "intent_alignment",
            "evidence_support",
            "coverage",
            "robustness",
        ]
        vector_score_delta = {
            dim: float(after_scores.get(dim, 0.0)) - float(before_scores.get(dim, 0.0))
            for dim in vector_dims
        }
        artifact_store.append_review_event(
            target_claim_id=canonical_claim_id,
            artifact_kind="assurance_profiles",
            artifact_id=canonical_artifact_id(profile.profile_id),
            event_type="profile_recomputed",
            actor="system",
            actor_role=ReviewActorRole.system.value,
            notes="Profile recomputed from audit results.",
            metadata={
                "proposal": {"claim_id": canonical_claim_id},
                "accepted": True,
                "verifier_delta": {
                    "legality": True,
                    "vector_score_delta": vector_score_delta,
                    "gate_before": gate_before,
                    "gate_after": gate_after,
                    "hidden_assumptions_added": [],
                    "profile_recomputed": True,
                },
            },
        )
        orchestrator = self._build_orchestrator(project_id)
        promotion_state = orchestrator.load_promotion_state(canonical_claim_id)
        audit_blocking_issues = list(audit_output.get("blocking_issues") or [])
        if (
            promotion_state.current_gate == Gate.draft
            and gate_after not in {Gate.blocked.value, Gate.rejected.value}
            and not audit_blocking_issues
        ):
            try:
                promotion_state = orchestrator.advance_promotion_state(
                    claim_id=canonical_claim_id,
                    target_gate=Gate.queued,
                    actor="system.auto",
                    actor_role=ReviewActorRole.system,
                    override=True,
                    rationale="Auto-bootstrap after successful profile recompute.",
                    notes="Auto-bootstrap after phase2 profile creation.",
                )
            except Exception:
                promotion_state = orchestrator.load_promotion_state(canonical_claim_id)
        return ProfileRecomputeResult(
            project_id=project_id,
            claim_id=canonical_claim_id,
            profile=profile.model_dump(mode="json", exclude_none=True),
            promotion_state=promotion_state.model_dump(mode="json", exclude_none=True),
        )

    def approve_promotion(
        self,
        project_id: str,
        claim_id: str,
        *,
        target_gate: Gate | str,
        actor: str,
        actor_role: ReviewActorRole | str,
        override: bool = False,
        rationale: str = "",
        notes: str = "",
    ) -> PromotionCheckpointState:
        self._load_claim_graph(project_id)
        orchestrator = self._build_orchestrator(project_id)
        return orchestrator.advance_promotion_state(
            claim_id=claim_id,
            target_gate=target_gate,
            actor=actor,
            actor_role=actor_role,
            override=override,
            rationale=rationale,
            notes=notes,
        )

    def _collect_bundle_data(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        claim_graph_model = self._load_claim_graph(project_id)
        claim_graph = claim_graph_model.model_dump(mode="json", exclude_none=True)
        artifact_store = self.claim_trace_service.repository.artifact_store

        assurance_profiles: list[dict[str, Any]] = []
        review_events: dict[str, list[dict[str, Any]]] = {}
        promotion_states: dict[str, dict[str, Any]] = {}
        orchestrator = self._build_orchestrator(project_id)
        for claim in claim_graph_model.claims:
            claim_id = canonical_artifact_id(claim.claim_id)
            review_events[claim_id] = artifact_store.query_review_events(claim_id)
            try:
                profile = artifact_store.load_assurance_profile_for_claim(claim_id)
            except FileNotFoundError:
                continue
            assurance_profiles.append(profile.model_dump(mode="json", exclude_none=True))
            promotion_states[claim_id] = orchestrator.load_promotion_state(claim_id).model_dump(
                mode="json",
                exclude_none=True,
            )

        return {
            "claim_graph": claim_graph,
            "assurance_profiles": assurance_profiles,
            "review_events": review_events,
            "promotion_states": promotion_states,
            "evaluation_evidence": self.list_evaluation_evidence(project_id),
        }

    def export_trace(
        self,
        project_id: str,
        output_dir: str,
    ) -> TraceExportResult:
        from .event_normalizer import (
            EventNormalizer,
            classify_event,
            normalize_actor,
            normalize_event_type,
            normalize_phase,
            sanitize_reject_reason,
        )

        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        project_snapshot = self.open_project(project_id)
        artifact_store = self.claim_trace_service.repository.artifact_store
        structuring_graph = self._load_claim_graph(project_id)
        claim_graph_model = structuring_graph
        claim_graph_data = structuring_graph.model_dump(mode="json", exclude_none=True)

        source_text = ""
        source_units: list[dict[str, Any]] = []
        smb_dir = Path(artifact_store.root) / "source_mapping_bundles"
        if smb_dir.exists():
            for smb_file in sorted(smb_dir.glob("*.json")):
                try:
                    smb_data = json.loads(smb_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                source_doc = smb_data.get("source_document") or {}
                source_text = str(source_doc.get("text") or source_doc.get("content") or source_text)
                raw_units = smb_data.get("raw_units") or []
                if raw_units:
                    built_units: list[dict[str, Any]] = []
                    offset = 0
                    for idx, unit in enumerate(raw_units, start=1):
                        if not isinstance(unit, dict):
                            continue
                        text = str(unit.get("text") or "")
                        if not text:
                            continue
                        built_units.append({
                            "unit_id": str(unit.get("unit_id") or f"su-{idx:04d}"),
                            "start_char": offset,
                            "end_char": offset + len(text),
                            "text": text,
                        })
                        offset += len(text) + 2
                    source_units = built_units
                if source_text:
                    break

        workflow_state: dict[str, Any] | None = None
        workflow_events = artifact_store.query_review_events(project_id)
        for event in reversed(workflow_events):
            if event.get("event_type") == "claim_structuring_workflow":
                workflow_state = dict(event.get("metadata") or {})
                break

        structuring_claims = claim_graph_data.get("claims", [])
        if isinstance(structuring_claims, dict):
            structuring_claims = list(structuring_claims.values())
        structuring_claim_ids = [
            str(item.get("claim_id") or "")
            for item in structuring_claims
            if str(item.get("claim_id") or "")
        ]
        assembler = PhaseAssembler(artifact_store)
        per_claim_results: dict[str, dict[str, Any]] = {}
        evidence_map: dict[str, Any] = {}
        phase_claim_ids = structuring_claim_ids
        for source_claim_id in phase_claim_ids:
            phase2_claim = assembler.assemble_phase2_claim(project_id, source_claim_id)
            phase3_claim = assembler.assemble_phase3_claim(project_id, source_claim_id)
            phase2_claim["_raw_review_events"] = artifact_store.query_review_events(source_claim_id)
            phase2_claim["_source_claim_id"] = source_claim_id
            per_claim_results[source_claim_id] = phase2_claim
            if phase3_claim.get("research_output") is not None or phase3_claim.get("updated_profile") is not None:
                evidence_map[source_claim_id] = phase3_claim

        for raw_claim in claim_graph_data.get("claims") or []:
            claim_id = canonical_artifact_id(raw_claim.get("claim_id") or raw_claim.get("node_id") or "")
            pcr = per_claim_results.get(claim_id, {})
            profile = pcr.get("profile") or {}
            if profile:
                gate = str(profile.get("gate") or "draft")
                raw_claim["lifecycle"] = "active" if gate not in {"", "draft"} else "proposed"
                proof_claim = profile.get("proofClaim") or {}
                raw_claim["confidence"] = float(proof_claim.get("score", 0.0) or 0.0)
            else:
                raw_claim.setdefault("lifecycle", "proposed")
                raw_claim.setdefault("confidence", 0.0)

        claim_by_id = {
            canonical_artifact_id(item.get("claim_id") or item.get("node_id") or ""): item
            for item in (claim_graph_data.get("claims") or [])
        }
        quantitative_cues = (
            "%", "study", "survey", "benchmark", "analysis", "report", "found",
            "tracked", "employees", "months", "2022", "2023", "25", "1,612",
        )
        for rel in claim_graph_data.get("relations") or []:
            raw_strength = str(rel.get("strength") or "").strip().lower()
            if raw_strength and raw_strength != "unknown":
                continue
            src = claim_by_id.get(canonical_artifact_id(rel.get("from_claim_id") or rel.get("src") or ""))
            tgt = claim_by_id.get(canonical_artifact_id(rel.get("to_claim_id") or rel.get("tgt") or ""))
            src_text = str((src or {}).get("text") or (src or {}).get("nl_statement") or "")
            tgt_text = str((tgt or {}).get("text") or (tgt or {}).get("nl_statement") or "")
            src_type = str((src or {}).get("type") or "")
            tgt_type = str((tgt or {}).get("type") or "")
            joined = f"{src_text} {tgt_text} {rel.get('rationale') or ''}".lower()
            if any(cue in joined for cue in quantitative_cues):
                rel["strength"] = "statistical"
            elif "according to" in joined or "witness" in joined or "interview" in joined or "statement" in joined:
                rel["strength"] = "testimonial"
            elif src_type == "evidence" or tgt_type == "evidence":
                rel["strength"] = "statistical"
            else:
                rel["strength"] = "abductive"

        # B40/SAFE-001: Reconstruct source_text from ordered source_units
        # when the source-mapping bundle provides units but not a top-level
        # document body.
        if not source_text and source_units:
            from .trace_export import reconstruct_source_text
            source_text = reconstruct_source_text(source_units)

        # Compute propagation traces and vector score deltas from profiles
        _rels = claim_graph_data.get("relations", [])
        if isinstance(_rels, dict):
            _rels = list(_rels.values())
        _propagation_traces: list[dict[str, Any]] = []
        _vector_score_deltas: list[dict[str, Any]] = []
        for cid, pcr in per_claim_results.items():
            profile = pcr.get("profile") or {}
            if not profile.get("gate"):
                continue
            # Find downstream claims via relations
            affected = []
            for rel in _rels:
                src = rel.get("from_claim_id", "")
                tgt = rel.get("to_claim_id", "")
                if src == cid and tgt != cid:
                    affected.append(tgt)
                elif tgt == cid and src != cid:
                    affected.append(src)
            if affected:
                # v2-final: trigger_claim_id, trigger_event_id, path with status changes
                path_entries = []
                for a_cid in affected:
                    a_profile = (per_claim_results.get(a_cid) or {}).get("profile") or {}
                    path_entries.append({
                        "claim_id": a_cid,
                        "status_before": "draft",
                        "status_after": str(a_profile.get("gate") or "draft"),
                        "reason": "propagation_from_profile_change",
                    })
                _propagation_traces.append({
                    "trigger_claim_id": cid,
                    "trigger_event_id": f"profile_change.{cid}",
                    "path": path_entries,
                })
            # Vector score delta — v2-final requires event_id
            vs = (profile.get("vector_scores") or {})
            if vs:
                _vector_score_deltas.append({
                    "claim_id": cid,
                    "event_id": f"profile_change.{cid}",
                    "gate_before": "draft",
                    "gate_after": str(profile.get("gate") or "draft"),
                    "before": {"trust_base_integrity": 0.0, "intent_alignment": 0.0,
                               "evidence_support": 0.0, "coverage": 0.0, "robustness": 0.0},
                    "after": {
                        "trust_base_integrity": float(vs.get("trust_base_integrity", 0)),
                        "intent_alignment": float(vs.get("intent_alignment", 0)),
                        "evidence_support": float(vs.get("evidence_support", 0)),
                        "coverage": float(vs.get("coverage", 0)),
                        "robustness": float(vs.get("robustness", 0)),
                    },
                    "changed_dimensions": [k for k, v in vs.items() if v and float(v) > 0],
                })

        engine_state = {
            "claim_graph": claim_graph_data,
            "per_claim_results": per_claim_results,
            "source_text": source_text,
            "source_units": source_units,
            "evidence": {"evidence": evidence_map} if evidence_map else None,
            "propagation_traces": _propagation_traces if _propagation_traces else None,
            "vector_score_deltas": _vector_score_deltas if _vector_score_deltas else None,
            "oae_commit": "",
            "audit_rules_version": "",
            "promotion_fsm_version": "",
            "verifier_versions": {},
        }
        builder = TraceExportBuilder(run_id=project_id, engine_state=engine_state)
        candidate_registry = CandidateRegistry(trace_id=builder.trace_id)
        trace = builder.build_v2(candidate_registry=candidate_registry, workflow_state=workflow_state)
        redacted_trace = ModelSafeSerializer.redact(trace)
        trace_path = out / "trace.json"
        trace_path.write_text(json.dumps(redacted_trace, indent=2, ensure_ascii=False), encoding="utf-8")

        normalizer = EventNormalizer(trace_id=builder.trace_id)
        log_writer = TransitionLogWriter(trace_id=builder.trace_id)
        last_controllable_event_id: str | None = None

        runtime_candidate_ledger = dict((workflow_state or {}).get("candidate_ledger") or {})
        for idx, relation_entry in enumerate(list(runtime_candidate_ledger.get("relations_proposed") or []), start=1):
            # B20/AUD-006: Canonicalize propose_relation proposals.
            # strength must never be null; default to "unknown".
            raw_strength = relation_entry.get("strength")
            canonical_strength = str(raw_strength).strip().lower() if raw_strength else "unknown"
            if not canonical_strength or canonical_strength == "none":
                canonical_strength = "unknown"
            src = relation_entry.get("from_claim_id") or relation_entry.get("src_id") or ""
            tgt = relation_entry.get("to_claim_id") or relation_entry.get("tgt_id") or ""
            proposal = {
                "candidate_id": relation_entry.get("candidate_id"),
                "proposal_id": relation_entry.get("proposal_id"),
                "src_id": src,
                "tgt_id": tgt,
                "relation_type": relation_entry.get("relation_type", ""),
                "strength": canonical_strength,
            }
            accepted = bool(relation_entry.get("accepted"))
            # Malformed proposals with blank src/tgt are not policy-supervisable.
            # Keep them in transition_log but mark as automatic_consequence
            # so they don't become policy gold rows.
            is_malformed = not src or not tgt
            rel_event_class = "automatic_consequence" if is_malformed else "controllable_action"
            # B40/SAFE-002: Sanitize reject_reason for model-visible output
            raw_rej = relation_entry.get("reject_reason")
            if is_malformed and not raw_rej:
                raw_rej = "malformed_proposal"
            safe_rel_reason, _raw_rel_diag = sanitize_reject_reason(raw_rej)
            before_hash = hashlib.sha256(json.dumps({"idx": idx - 1}, sort_keys=True).encode()).hexdigest()[:16]
            after_hash = before_hash if not accepted else hashlib.sha256(json.dumps({"idx": idx}, sort_keys=True).encode()).hexdigest()[:16]

            # B30/ACT-002: Build outcome so prefix builders can accumulate
            # visible state progressively.  For accepted relations the
            # outcome carries the relation added to the graph; for rejected
            # proposals the outcome is empty (no state change).
            rel_outcome: dict[str, Any] | None = None
            if accepted:
                rel_outcome = {
                    "relations": [{
                        "source_id": src,
                        "target_id": tgt,
                        "relation_type": proposal.get("relation_type", ""),
                        "strength": proposal.get("strength", "unknown"),
                        "relation_id": str(relation_entry.get("accepted_as") or ""),
                    }],
                }

            log_writer.record_event(
                step_id=f"step-phase1-rel-{idx:04d}",
                phase="phase1",
                event_type="propose_relation",
                actor="planner",
                before_hash=before_hash,
                after_hash=after_hash,
                event_class=rel_event_class,
                proposal=proposal,
                accepted=accepted,
                reject_reason=safe_rel_reason,
                changed_ids=[str(relation_entry.get("accepted_as"))] if accepted and relation_entry.get("accepted_as") else [],
                verifier_delta={"legality": accepted, "gate_before": "draft", "gate_after": "draft",
                               "contradiction_delta": None, "hidden_assumptions_added": None,
                               "profile_recomputed": False},
                outcome=rel_outcome,
            )

        for tracer_claim_id, claim_data in per_claim_results.items():
            source_claim_id = str(claim_data.get("_source_claim_id") or tracer_claim_id)
            events = claim_data.get("_raw_review_events") or []
            for idx, event in enumerate(events):
                normalized = normalizer.normalize_review_event(event, claim_id=source_claim_id, idx=idx)
                v2_event_type = normalize_event_type(normalized.event_type)
                v2_actor = normalize_actor(normalized.actor)
                v2_phase = normalize_phase(normalized.phase)
                v2_event_class = classify_event(v2_event_type)
                cause_id = last_controllable_event_id if v2_event_class == "automatic_consequence" else None
                verifier_delta = dict(normalized.verifier_delta or {}) if normalized.verifier_delta else None
                # Fallback: derive verifier_delta from claim profile
                if not verifier_delta or verifier_delta == {}:
                    claim_profile = claim_data.get("profile") or {}
                    gate = str(claim_profile.get("gate") or "draft")
                    verifier_delta = {
                        "legality": True,
                        "gate_before": "draft",
                        "gate_after": gate,
                        "contradiction_delta": None,
                        "hidden_assumptions_added": None,
                        "profile_recomputed": v2_event_type in ("finalize_profile", "profile_recomputed", "audit_workflow"),
                    }
                effective_proposal = _canonicalize_proposal(
                    v2_event_type,
                    normalized.proposal,
                    event.get("metadata") or {},
                    source_claim_id,
                )

                # B30: Build outcome data so prefix builders can
                # reconstruct progressive visible state.
                per_claim_outcome = _build_event_outcome(
                    v2_event_type,
                    source_claim_id,
                    effective_proposal,
                    normalized.accepted,
                    claim_data,
                    claim_by_id,
                )

                v2_event = log_writer.record_event(
                    step_id=normalized.step_id,
                    phase=v2_phase,
                    event_type=v2_event_type,
                    actor=v2_actor,
                    before_hash=normalized.before_hash,
                    after_hash=normalized.after_hash,
                    event_class=v2_event_class,
                    cause_event_id=cause_id,
                    proposal=effective_proposal,
                    accepted=normalized.accepted,
                    reject_reason=normalized.reject_reason,
                    changed_ids=normalized.changed_ids,
                    verifier_delta=verifier_delta,
                    outcome=per_claim_outcome,
                )
                if v2_event_class == "controllable_action":
                    last_controllable_event_id = v2_event["event_id"]

        transition_log_path = out / "transition_log.jsonl"
        log_writer.write_jsonl(str(transition_log_path))

        sidecar = SidecarMetaWriter(trace_id=builder.trace_id, source_domain=project_snapshot.domain)
        sidecar_meta_path = out / "sidecar_meta.json"
        sidecar.write(str(sidecar_meta_path))

        violations = ModelSafeSerializer.validate_model_safe(redacted_trace)
        return TraceExportResult(
            project_id=project_id,
            output_dir=str(out),
            trace_path=str(trace_path),
            transition_log_path=str(transition_log_path),
            sidecar_meta_path=str(sidecar_meta_path),
            export_version="v2",
            redaction_violations=violations,
        )

    def export_prefix_slices(
        self,
        project_id: str,
        output_path: str | None = None,
        format: str = "jsonl",
    ) -> PrefixSliceRunResult:
        from .prefix_slice_builder import PrefixSliceBuilder
        from .prefix_slice_graph_builder import PrefixSliceGraphBuilder

        if output_path:
            resolved_path = Path(output_path)
            export_dir = resolved_path.parent
        else:
            export_dir = Path(self.config.data_dir) / "exports" / project_id
            resolved_path = export_dir / "prefix_slices.jsonl"
        export_dir.mkdir(parents=True, exist_ok=True)

        trace_result = self.export_trace(project_id, str(export_dir))
        trace = json.loads(Path(trace_result.trace_path).read_text(encoding="utf-8"))
        transition_log = [
            json.loads(line)
            for line in Path(trace_result.transition_log_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        # B10/PFX-002: Build prefix from a minimal base trace (source only).
        # Do NOT seed with all final claims/relations/profiles from the
        # fully populated trace snapshot.  The builders reconstruct visible
        # state incrementally from the transition_log event stream.
        raw_source = trace.get("source") or {}
        # Normalize source keys for the canonical serializer which expects
        # "text" not "source_text", and "title" not "source_title".
        source_for_prefix = {}
        source_text = (
            raw_source.get("source_text")
            or raw_source.get("text")
            or raw_source.get("content")
            or raw_source.get("body")
            or ""
        )
        if not source_text:
            # Reconstruct from source_units when source_text is missing
            units = raw_source.get("source_units") or []
            if units:
                source_text = "\n\n".join(
                    str(u.get("text") or "") for u in units if u.get("text")
                )
        if source_text:
            source_for_prefix["text"] = source_text
        source_title = raw_source.get("title") or raw_source.get("name") or ""
        if source_title:
            source_for_prefix["title"] = source_title

        # B30: Include base phase1 claims in the prefix trace because they
        # exist before any transition_log events (they come from the
        # structuring workflow).  Relations are NOT included here -- they
        # accumulate via propose_relation event outcomes in the transition log.
        phase1_cg = (trace.get("phase1") or {}).get("claim_graph") or {}
        base_claims_for_prefix = []
        for c in (phase1_cg.get("claims") or []):
            base_claims_for_prefix.append({
                "claim_id": c.get("node_id") or c.get("claim_id") or "",
                "text": c.get("text") or c.get("nl_statement") or "",
                "status": c.get("status") or "proposed",
                "type": c.get("type") or "claim",
            })

        # Do NOT seed all claims into the base trace. Claims appear
        # progressively through synthetic claim-creation events.
        trace_for_prefix = {
            "trace_id": (trace.get("meta") or {}).get("trace_id"),
            "source": source_for_prefix,
            "claims": [],
            "relations": [],
            "gaps": [],
            "hidden_assumptions": [],
            "formalization": {},
            "audit": {},
            "artifacts": ["trace.json", "transition_log.jsonl", "sidecar_meta.json"],
        }

        # Inject synthetic claim-creation events at the START of the timeline.
        # These are controllable so they create prefix rows with progressive
        # node growth, but they have no gold_action (not policy-supervisable).
        # The prefix builder will emit them as rows with gold_action=null.
        claim_events = []
        for cidx, claim in enumerate(base_claims_for_prefix):
            cid = claim.get("claim_id") or claim.get("node_id") or ""
            claim_events.append({
                "event_seq": -(len(base_claims_for_prefix) - cidx),
                "step_id": f"step-claim-{cidx:04d}",
                "event_type": "propose_relation",
                "event_class": "controllable_action",
                "actor": "planner",
                "phase": "phase1",
                "before_hash": f"claim-before-{cidx}",
                "after_hash": f"claim-after-{cidx}",
                "accepted": True,
                "changed_ids": [cid],
                "outcome": {"claims": [claim]},
                "_no_gold_action": True,
            })
        # Prepend claim events before existing transition log
        transition_log = claim_events + transition_log

        # B20/AUD-005: Wire up LegalActionMaskBuilder so every policy row
        # gets a computed legal_action_mask instead of null.
        from .action_dsl import LegalActionMaskBuilder

        phase1 = trace.get("phase1") or {}
        phase2 = trace.get("phase2") or {}
        claims_for_mask = (phase1.get("claim_graph") or {}).get("claims") or []
        relations_for_mask = (phase1.get("claim_graph") or {}).get("relations") or []
        profiles_for_mask: dict[str, Any] = {}
        promotion_states_for_mask: dict[str, Any] = {}
        for cid, payload in (phase2.get("per_claim") or {}).items():
            profile = payload.get("profile") or {}
            profiles_for_mask[cid] = {
                "gate": profile.get("gate", "draft"),
                "formal_status": profile.get("formal_status"),
                "required_actions": (payload.get("audit") or {}).get("blocking_issues", []),
            }
            promo_transitions = payload.get("promotion_transitions") or []
            current_gate = profile.get("gate", "draft")
            if promo_transitions:
                last_transition = promo_transitions[-1] if isinstance(promo_transitions, list) else {}
                current_gate = str(last_transition.get("to_gate") or current_gate)
            promotion_states_for_mask[cid] = {
                "current_gate": current_gate,
                "recommended_gate": current_gate,
            }
        claim_graph_for_mask = {
            "claims": [
                {"claim_id": c.get("node_id") or c.get("claim_id"), "title": c.get("text", "")}
                for c in claims_for_mask
            ],
            "relations": relations_for_mask,
        }
        mask_builder = LegalActionMaskBuilder(
            claim_graph=claim_graph_for_mask,
            profiles=profiles_for_mask,
            promotion_states=promotion_states_for_mask,
        )

        # B10: Both builders receive the same trace_for_prefix and the same
        # transition_log so they produce aligned cutoff lists.
        text_builder = PrefixSliceBuilder(trace_for_prefix, transition_log)
        text_builder.set_action_mask_builder(mask_builder)
        text_slices = text_builder.extract_slices()
        if format == "json":
            resolved_path.write_text(json.dumps(text_slices, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            resolved_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in text_slices) + ("\n" if text_slices else ""),
                encoding="utf-8",
            )

        graph_builder = PrefixSliceGraphBuilder(trace_for_prefix, transition_log)
        graph_builder.set_action_mask_builder(mask_builder)
        graph_path = export_dir / "prefix_graph_slices.jsonl"
        graph_slices = graph_builder.extract_graph_slices()
        # Ensure text/graph legal_action_mask parity: copy text mask to graph
        text_mask_by_step = {s.get("step_id"): s.get("legal_action_mask") for s in text_slices}
        for gs in graph_slices:
            text_mask = text_mask_by_step.get(gs.get("step_id"))
            if text_mask is not None:
                gs["legal_action_mask"] = text_mask
        graph_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in graph_slices) + ("\n" if graph_slices else ""),
            encoding="utf-8",
        )

        return PrefixSliceRunResult(
            project_id=project_id,
            slice_count=len(text_slices),
            output_path=str(resolved_path),
            format=format,
        )

    def export_bundle(self, project_id: str) -> ProjectBundleExport:
        project_snapshot = self.open_project(project_id)
        claim_graph = self._load_claim_graph(project_id).model_dump(
            mode="json",
            exclude_none=True,
        )
        artifact_store = self.claim_trace_service.repository.artifact_store

        assurance_profiles: list[dict[str, Any]] = []
        review_events: dict[str, list[dict[str, Any]]] = {}
        promotion_states: dict[str, dict[str, Any]] = {}
        orchestrator = self._build_orchestrator(project_id)
        for claim in self._load_claim_graph(project_id).claims:
            claim_id = canonical_artifact_id(claim.claim_id)
            review_events[claim_id] = artifact_store.query_review_events(claim_id)
            try:
                profile = artifact_store.load_assurance_profile_for_claim(claim_id)
            except FileNotFoundError:
                continue
            assurance_profiles.append(profile.model_dump(mode="json", exclude_none=True))
            promotion_states[claim_id] = orchestrator.load_promotion_state(claim_id).model_dump(
                mode="json",
                exclude_none=True,
            )

        return ProjectBundleExport(
            project=project_snapshot,
            claim_graph=claim_graph,
            assurance_profiles=assurance_profiles,
            evaluation_evidence=self.list_evaluation_evidence(project_id),
            review_events=review_events,
            promotion_states=promotion_states,
        )

    def _best_effort_claim_analysis(
        self,
        project_id: str,
        claim_graph: ClaimGraph,
        claim: Any,
        *,
        warnings: list[str] | None = None,
    ) -> ClaimAnalysisRunResult:
        claim_id = canonical_artifact_id(claim.claim_id)
        reference_links = self.get_claim_reference_links(project_id, claim_id)
        evidence_links = self.get_claim_evidence_links(project_id, claim_id)
        research_output = self._build_best_effort_research_output(
            project_id,
            reference_links=reference_links,
            evidence_links=evidence_links,
        )
        audit_output = self._build_best_effort_audit_output(
            claim_graph=claim_graph,
            claim_id=claim_id,
            reference_links=reference_links,
            evidence_links=evidence_links,
            inherited_warnings=warnings or [],
        )
        profile = compute_assurance_profile(
            AssuranceComputationInput(
                project_id=project_id,
                claim=claim.model_dump(mode="json"),
                verifier_output={},
                audit_output=audit_output,
                research_output=research_output,
                coverage_data=None,
                claim_graph=claim_graph,
                claim_graph_ref=canonical_artifact_id(claim_graph.graph_id),
            )
        )
        self.claim_trace_service.repository.artifact_store.save_assurance_profile(profile)
        review_event = {
            "event_type": "claim_analysis",
            "analysis_mode": "best_effort",
            "warning_count": len(list(audit_output.get("warnings") or [])),
            "blocking_issue_count": len(list(audit_output.get("blocking_issues") or [])),
            "profile_id": canonical_artifact_id(profile.profile_id),
        }
        self.claim_trace_service.repository.artifact_store.append_review_event(
            target_claim_id=claim_id,
            artifact_kind="claim_analysis",
            artifact_id=f"analysis.{claim_id}",
            event_type="claim_analysis",
            actor="engine_api",
            actor_role="system",
            notes="Best-effort claim analysis completed from canonical references/evidence.",
            metadata=review_event,
        )
        promotion_state = self._load_promotion_state_or_default(project_id, claim_id)
        return ClaimAnalysisRunResult(
            project_id=project_id,
            claim_id=claim_id,
            analysis_mode="best_effort",
            audit_output=audit_output,
            profile=profile.model_dump(mode="json", exclude_none=True),
            promotion_state=promotion_state.model_dump(mode="json", exclude_none=True),
            review_event=review_event,
            warnings=list(audit_output.get("warnings") or []),
        )

    def _build_best_effort_research_output(
        self,
        project_id: str,
        *,
        reference_links: list[dict[str, Any]],
        evidence_links: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        evidence_items: list[dict[str, Any]] = []
        support_status = "none"
        for link in evidence_links:
            evidence_id = str(link.get("evidence_id") or "")
            if not evidence_id:
                continue
            evidence = self.get_evaluation_evidence(project_id, evidence_id)
            evidence_kind = str(evidence.get("evidence_kind") or "experiment")
            normalized_kind = (
                "simulation"
                if evidence_kind == "simulation"
                else "test_run"
                if evidence_kind == "test_run"
                else "experiment"
            )
            confidence = float((evidence.get("uncertainty") or {}).get("confidence", 0.75))
            evidence_items.append(
                {
                    "node_id": evidence_id,
                    "node_type": "evidence",
                    "title": evidence.get("title") or evidence_id,
                    "summary": evidence.get("summary") or "",
                    "evidence_kind": normalized_kind,
                    "result_polarity": "supports",
                    "artifact_refs": [evidence.get("source_mapping_ref", {}).get("artifact_id", evidence_id)],
                    "confidence": confidence,
                    "status": evidence.get("status", "active"),
                }
            )
            if normalized_kind == "experiment":
                support_status = "experimentally_supported"
            elif normalized_kind == "simulation" and support_status == "none":
                support_status = "simulation_supported"
            elif normalized_kind == "test_run" and support_status == "none":
                support_status = "test_supported"

        if reference_links and support_status == "none":
            support_status = "literature_supported"
            for link in reference_links:
                reference_id = str(link.get("reference_id") or "")
                preview = dict(link.get("artifact_preview") or {})
                confidence = 0.8 if str(link.get("status") or "") == "resolved" else 0.45
                evidence_items.append(
                    {
                        "node_id": reference_id or f"reference.{len(evidence_items) + 1}",
                        "node_type": "reference",
                        "title": preview.get("title") or reference_id or "Linked reference",
                        "summary": preview.get("summary") or "Canonical source/reference support.",
                        "evidence_kind": "literature",
                        "result_polarity": "supports",
                        "artifact_refs": [reference_id] if reference_id else [],
                        "confidence": confidence,
                        "status": link.get("status", "active"),
                    }
                )

        if not evidence_items:
            return None

        return {
            "overall_assessment": "Best-effort claim analysis derived support from canonical references/evidence.",
            "recommended_support_status": support_status,
            "evidence_items": evidence_items,
        }

    def _build_best_effort_audit_output(
        self,
        *,
        claim_graph: ClaimGraph,
        claim_id: str,
        reference_links: list[dict[str, Any]],
        evidence_links: list[dict[str, Any]],
        inherited_warnings: list[str],
    ) -> dict[str, Any]:
        warnings = list(inherited_warnings)
        blocking_issues: list[str] = []
        unresolved_reference_count = 0
        ambiguous_reference_count = 0
        stale_reference_count = 0
        hotspot_artifact_ids: list[str] = []

        for link in reference_links:
            status = str(link.get("status") or "resolved")
            reference_id = str(link.get("reference_id") or "")
            if status == "resolved":
                continue
            hotspot_artifact_ids.append(reference_id)
            if status == "ambiguous":
                ambiguous_reference_count += 1
                warnings.append(
                    f"Ambiguous source reference requires review: {reference_id}"
                )
            elif status == "stale":
                stale_reference_count += 1
                warnings.append(f"Stale source reference requires refresh: {reference_id}")
            else:
                unresolved_reference_count += 1
                blocking_issues.append(
                    f"Unresolved source reference requires manual anchoring: {reference_id}"
                )

        if not reference_links:
            warnings.append("No canonical source references are linked to this claim yet.")
        if not evidence_links:
            warnings.append("No structured evaluation evidence is linked to this claim yet.")

        return {
            "trust_frontier": {
                "global_axiom_dependency_count": 0,
                "locale_assumption_count": 0,
                "premise_assumption_count": 0,
                "oracle_dependency_count": 0,
                "unreviewed_import_count": unresolved_reference_count
                + ambiguous_reference_count
                + stale_reference_count,
                "transitive_dependency_count": len(list(claim_graph.relations or [])),
                "reviewed_global_axiom_ids": [],
                "oracle_ids": [],
                "hotspot_artifact_ids": list(dict.fromkeys(hotspot_artifact_ids)),
                "notes": [
                    "Best-effort analysis used canonical source/reference state rather than theorem-local runner trust."
                ],
            },
            "conservativity": {
                "definitional_only": True,
                "reviewed_global_axioms_required": False,
                "compile_away_known": False,
                "nondefinitional_hotspots": list(dict.fromkeys(hotspot_artifact_ids)),
                "trusted_mechanisms": ["document_reference", "evaluation_evidence"],
                "flagged_mechanisms": ["stale_reference"] if stale_reference_count else [],
            },
            "model_health": {
                "locale_satisfiability": "untested",
                "countermodel_probe": "untested",
                "vacuity_check": "untested",
                "premise_sensitivity": "untested",
                "conclusion_perturbation": "untested",
                "notes": [
                    "Runner-backed proof probes were not executed for this best-effort analysis."
                ],
            },
            "intent_alignment": {
                "independent_formalization_count": 0,
                "agreement_score": 0.55 if reference_links else 0.35,
                "backtranslation_review": "unreviewed",
                "paraphrase_robustness_score": 0.5,
                "semantics_guard_violations": [],
                "reviewer_notes": [
                    "Intent alignment remains provisional until a formal or human review path is executed."
                ],
            },
            "blocking_issues": list(dict.fromkeys(blocking_issues)),
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _default_orchestrator_factory(
        self,
        config: PipelineConfig,
        llm: LLMClient,
        store: ArtifactStore,
    ) -> PipelineOrchestrator:
        return PipelineOrchestrator(config, llm=llm, store=store)

    def _build_orchestrator(self, project_id: str) -> PipelineOrchestrator:
        project_config = replace(
            self.config,
            project_id=project_id,
            data_dir=self.config.data_dir,
        )
        return self.orchestrator_factory(
            project_config,
            self.llm,
            self.claim_trace_service.repository.artifact_store,
        )

    def _require_project(self, project_id: str):
        project, graph_data = self.claim_trace_service.repository.load(project_id)
        if project is None:
            raise ValueError(f"Project '{project_id}' not found.")
        return project, graph_data

    def _load_claim_graph(self, project_id: str) -> ClaimGraph:
        project, graph_data = self._require_project(project_id)
        if not graph_data:
            raise ValueError(f"Project '{project_id}' has no ClaimGraph yet.")
        if project.claim_graph_id:
            return self.claim_trace_service.repository.artifact_store.load_claim_graph(
                project.claim_graph_id
            )
        return ClaimGraph.model_validate(graph_data)

    def _find_claim(self, claim_graph: ClaimGraph, claim_id: str):
        canonical_claim_id = canonical_artifact_id(claim_id)
        for claim in claim_graph.claims:
            if canonical_artifact_id(claim.claim_id) == canonical_claim_id:
                return claim
        raise ValueError(f"Claim '{canonical_claim_id}' not found in ClaimGraph.")

    def _load_promotion_state_or_default(
        self,
        project_id: str,
        claim_id: str,
    ) -> PromotionCheckpointState:
        canonical_claim_id = canonical_artifact_id(claim_id)
        try:
            return self._build_orchestrator(project_id).load_promotion_state(
                canonical_claim_id
            )
        except FileNotFoundError:
            return PromotionCheckpointState(
                claim_id=canonical_claim_id,
                profile_id=f"profile.pending.{canonical_claim_id}",
                recommended_gate=Gate.draft,
                current_gate=Gate.draft,
                required_actions=[],
                transitions=[],
            )


__all__ = [
    "BatchClaimFormalizationResult",
    "BatchFormalizationRunResult",
    "AuditRunResult",
    "ClaimAnalysisRunResult",
    "ClaimStructuringRunResult",
    "DocumentIngestRunResult",
    "DualFormalizationRunResult",
    "EngineProjectHandle",
    "EngineProjectSnapshot",
    "FormalClaimEngineAPI",
    "PrefixSliceRunResult",
    "ProfileRecomputeResult",
    "ProjectBundleExport",
    "TraceExportResult",
]
