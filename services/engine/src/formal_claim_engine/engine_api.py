"""Typed service boundary over project, workflow, audit, and promotion operations."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Callable

from pydantic import BaseModel, Field

from .audit_rules import AssuranceComputationInput, compute_assurance_profile
from .claim_trace_service import ClaimTraceService
from .config import PipelineConfig
from .llm_client import LLMClient, llm_client
from .unified_config import load_config as _load_unified_config, to_pipeline_config
from .models import AssuranceProfile, ClaimGraph, Gate
from .orchestrator import PipelineOrchestrator
from .promotion_state_machine import PromotionCheckpointState, ReviewActorRole
from .safe_slice_optional import build_safeslice_task_payload
from .store import ArtifactStore, canonical_artifact_id

OrchestratorFactory = Callable[[PipelineConfig, LLMClient, ArtifactStore], PipelineOrchestrator]


def _try_unified_config() -> PipelineConfig:
    """Return a PipelineConfig from verification.toml if found, else defaults."""
    try:
        return to_pipeline_config(_load_unified_config())
    except FileNotFoundError:
        return PipelineConfig()


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


class SafeSliceTaskExportResult(BaseModel):
    project_id: str
    target_claim_ids: list[str] = Field(default_factory=list)
    task: dict[str, Any] = Field(default_factory=dict)
    availability: dict[str, Any] = Field(default_factory=dict)
    review_events: list[dict[str, Any]] = Field(default_factory=list)


class ProjectBundleExport(BaseModel):
    project: EngineProjectSnapshot
    claim_graph: dict[str, Any] | None = None
    assurance_profiles: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_evidence: list[dict[str, Any]] = Field(default_factory=list)
    review_events: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    promotion_states: dict[str, dict[str, Any]] = Field(default_factory=dict)


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
        base_config = config or _try_unified_config()
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
        self.claim_trace_service.repository.artifact_store.save_assurance_profile(profile)
        promotion_state = self._build_orchestrator(project_id).load_promotion_state(
            canonical_artifact_id(claim.claim_id)
        )
        return ProfileRecomputeResult(
            project_id=project_id,
            claim_id=canonical_artifact_id(claim.claim_id),
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

    def export_safe_slice_task(
        self,
        project_id: str,
        *,
        claim_ids: list[str] | None = None,
        thresholds: dict[str, Any] | None = None,
        ambiguity: dict[str, Any] | None = None,
    ) -> SafeSliceTaskExportResult:
        try:
            unified = _load_unified_config()
            safe_slice_cfg = unified.safe_slice
        except FileNotFoundError:
            safe_slice_cfg = None

        if safe_slice_cfg is None or not safe_slice_cfg.enabled:
            raise ValueError(
                "SafeSlice integration is disabled. "
                "Enable [integration.safeslice] in settings/verification.toml."
            )

        claim_graph = self._load_claim_graph(project_id).model_dump(
            mode="json",
            exclude_none=True,
        )
        payload = build_safeslice_task_payload(
            claim_graph,
            target_claim_ids=claim_ids,
            thresholds=thresholds,
            ambiguity=ambiguity,
            adapter_config={
                "relation_types": list(safe_slice_cfg.relation_types),
                "include_baseline_slice": safe_slice_cfg.include_baseline_slice,
                "include_scope_conditions_in_context": safe_slice_cfg.include_scope_conditions_in_context,
                "include_semantics_guard_in_context": safe_slice_cfg.include_semantics_guard_in_context,
            },
            src_path_override=safe_slice_cfg.src_path or None,
        )
        task = dict(payload.get("task") or {})
        target_claim_ids = [
            str(item)
            for item in list(claim_ids or [])
            if str(item)
        ]
        if not target_claim_ids:
            target_claim_ids = [
                canonical_artifact_id(chain.get("metadata", {}).get("target_claim_id") or "")
                for chain in list(task.get("chains") or [])
                if chain.get("metadata", {}).get("target_claim_id")
            ]

        review_events: list[dict[str, Any]] = []
        for target_claim_id in target_claim_ids:
            review_events.append(
                self.claim_trace_service.repository.artifact_store.append_review_event(
                    target_claim_id=target_claim_id,
                    artifact_kind="safe_slice_task",
                    artifact_id=f"safeslice.task.{canonical_artifact_id(target_claim_id)}",
                    event_type="safe_slice_task_export",
                    actor="engine_api",
                    actor_role="system",
                    notes="Exported optional safeslice task from the current ClaimGraph.",
                    metadata={
                        "analysis_mode": "safe_slice_task_export",
                        "task_id": task.get("task_id"),
                        "availability": dict(payload.get("availability") or {}),
                    },
                )
            )

        return SafeSliceTaskExportResult(
            project_id=project_id,
            target_claim_ids=target_claim_ids,
            task=task,
            availability=dict(payload.get("availability") or {}),
            review_events=review_events,
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
    "ProfileRecomputeResult",
    "ProjectBundleExport",
    "SafeSliceTaskExportResult",
]
