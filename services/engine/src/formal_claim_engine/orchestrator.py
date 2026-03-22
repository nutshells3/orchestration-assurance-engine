"""
Pipeline Orchestrator.

Drives the 3-phase pipeline:
  Phase 1: Claim Structuring  (Planner → ClaimGraphAgent)
  Phase 2: Formalization & Audit  (Formalizer A/B → Verifier → Auditor → PolicyEngine)
  Phase 3: Evidence & Execution  (ResearchAgent / DevAgent → PolicyEngine recompute)

Each phase produces artifacts that are persisted to the ArtifactStore
and validated against JSON Schema before proceeding.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from pathlib import Path

from .audit_rules import (
    AssuranceComputationInput,
    compute_assurance_profile,
    emit_contract_pack,
)
from .config import PipelineConfig, proof_backend_family, proof_system_name
from .llm_client import LLMClient, llm_client
from .models import (
    ClaimGraph, AssuranceGraph, AssuranceProfile,
    Gate, ClaimStatus, FormalStatus,
)
from .claim_structuring_workflow import (
    STRUCTURING_PLANNER_ACTIONS,
    ClaimStructuringStage,
    ClaimStructuringWorkflowState,
)
from .audit_workflow import AuditWorkflowStage, AuditWorkflowState
from .dual_formalization_workflow import (
    DualFormalizationStage,
    DualFormalizationWorkflowState,
    FormalizationAttempt,
    FormalizationAttemptLineage,
    FormalizationAttemptStatus,
    FormalizationDivergence,
    normalize_assumptions,
    output_sha256,
)
from .promotion_state_machine import (
    PromotionCheckpointState,
    PromotionStateMachine,
    ReviewActorRole,
)
from .store import ArtifactStore, canonical_artifact_id, now_utc
from .proof_protocol import (
    ProofProtocolClient,
    build_proof_protocol_client,
)

from .agents.planner import PlannerAgent
from .agents.claim_graph_agent import ClaimGraphAgent
from .agents.formalizer import FormalizerAgent
from .agents.proof_verifier import ProofVerifierAgent
from .agents.auditor import AuditorAgent
from .agents.research_agent import ResearchAgent
from .agents.dev_agent import DevAgent
from .agents.policy_engine import PolicyEngineAgent

log = logging.getLogger(__name__)


def _enumish(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if "." in normalized:
        normalized = normalized.split(".")[-1]
    return normalized


def _slug(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or fallback


def _normalize_claim_class(value: Any, role: str) -> str:
    normalized = _enumish(value)
    role = _enumish(role)
    aliases = {
        "legal_principle": "assumption",
        "principle": "assumption",
        "rule": "assumption",
        "fact": "assumption",
        "holding": "core_claim",
        "conclusion": "core_claim",
        "issue": "research_question",
        "question": "research_question",
    }
    allowed = {
        "core_claim",
        "enabling_claim",
        "assumption",
        "metric",
        "evaluator",
        "policy_variable",
        "implementation_detail",
        "runtime_artifact",
        "research_question",
        "appendix",
    }
    if normalized in allowed:
        return normalized
    if normalized in aliases:
        return aliases[normalized]
    role_map = {
        "holding": "core_claim",
        "conclusion": "core_claim",
        "theorem": "core_claim",
        "statute": "assumption",
        "precedent": "assumption",
        "premise": "assumption",
    }
    return role_map.get(role, "assumption")


def _normalize_claim_kind(value: Any, claim_class: str) -> str:
    normalized = _enumish(value)
    allowed = {
        "theorem_candidate",
        "definition_candidate",
        "hypothesis",
        "invariant",
        "interface_contract",
        "optimization_goal",
        "empirical_generalization",
        "evaluation_criterion",
        "design_principle",
        "safety_property",
        "liveness_property",
        "other",
    }
    if normalized in allowed:
        return normalized
    aliases = {
        "theorem": "theorem_candidate",
        "definition": "definition_candidate",
        "criterion": "evaluation_criterion",
        "safety": "safety_property",
        "liveness": "liveness_property",
    }
    if normalized in aliases:
        return aliases[normalized]
    return {
        "core_claim": "theorem_candidate",
        "assumption": "hypothesis",
        "metric": "evaluation_criterion",
        "research_question": "other",
    }.get(claim_class, "other")


def _normalize_graph_policy_candidate(policy: Any) -> dict[str, Any]:
    candidate = dict(policy or {})
    carrier = _enumish(candidate.get("default_assumption_carrier"))
    if carrier not in {"premise", "locale"}:
        carrier = "premise"
    return {
        "default_assumption_carrier": carrier,
        "allow_global_axioms": bool(candidate.get("allow_global_axioms", False)),
        "require_backtranslation_review": bool(candidate.get("require_backtranslation_review", True)),
        "require_dual_formalization_for_core_claims": bool(
            candidate.get("require_dual_formalization_for_core_claims", True)
        ),
    }


def _normalize_scope_candidate(scope: Any) -> dict[str, Any]:
    candidate = dict(scope or {})
    conditions = candidate.get("conditions") or {}
    included = candidate.get("included_conditions") or candidate.get("included") or conditions.get("included") or []
    excluded = candidate.get("excluded_conditions") or candidate.get("excluded") or conditions.get("excluded") or []
    modality = _enumish(candidate.get("modality") or "other")
    allowed_modalities = {
        "universal",
        "existential",
        "high_probability",
        "average_case",
        "worst_case",
        "interface_contract",
        "empirical",
        "design_intent",
        "optimization",
        "other",
    }
    if modality not in allowed_modalities:
        modality = "other"
    return {
        "domain": str(candidate.get("domain") or "general"),
        "modality": modality,
        "included_conditions": [str(item) for item in list(included or [])],
        "excluded_conditions": [str(item) for item in list(excluded or [])],
    }


def _normalize_source_anchors(
    source_anchors: Any,
    project_id: str,
    title: str,
    raw_claim: dict[str, Any],
) -> list[dict[str, Any]]:
    anchors = []
    for item in list(source_anchors or []):
        if isinstance(item, dict):
            source_type = _enumish(item.get("source_type")) or "document"
            if source_type not in {
                "user_message",
                "planner_note",
                "research_note",
                "dev_note",
                "document",
                "issue",
                "meeting_note",
                "external_source",
                "other",
            }:
                source_type = "document"
            anchors.append(
                {
                    "source_type": source_type,
                    "source_ref": str(item.get("source_ref") or f"project:{project_id}"),
                    "excerpt": str(
                        item.get("excerpt")
                        or item.get("text")
                        or item.get("statement")
                        or ""
                    ),
                    "span_start": item.get("span_start"),
                    "span_end": item.get("span_end"),
                }
            )
        else:
            anchors.append(
                {
                    "source_type": "document",
                    "source_ref": f"project:{project_id}",
                    "excerpt": str(item),
                }
            )
    if not anchors:
        excerpt = str(
            raw_claim.get("excerpt")
            or raw_claim.get("source_text")
            or raw_claim.get("nl_statement")
            or raw_claim.get("statement")
            or raw_claim.get("text")
            or title
        )
        anchors.append(
            {
                "source_type": _enumish(raw_claim.get("source_type")) or "document",
                "source_ref": str(
                    raw_claim.get("source_ref")
                    or raw_claim.get("source_location")
                    or f"project:{project_id}"
                ),
                "excerpt": excerpt,
                "span_start": raw_claim.get("span_start"),
                "span_end": raw_claim.get("span_end"),
            }
        )
    return anchors


def _normalize_claim_policy_candidate(policy: Any) -> dict[str, Any]:
    candidate = dict(policy or {})
    carriers = []
    for item in list(candidate.get("allowed_assumption_carriers") or []):
        normalized = _enumish(item)
        if normalized in {"premise", "locale", "reviewed_global_axiom"}:
            carriers.append(normalized)
    if not carriers:
        carriers = ["premise"]
    return {
        "allowed_assumption_carriers": carriers,
        "global_axiom_allowed": bool(candidate.get("global_axiom_allowed", False)),
        "sorry_allowed_in_scratch": bool(candidate.get("sorry_allowed_in_scratch", True)),
        "sorry_allowed_in_mainline": bool(candidate.get("sorry_allowed_in_mainline", False)),
    }


def _normalize_claim_status(value: Any) -> str:
    normalized = _enumish(value) or "candidate"
    allowed = {
        "proposed",
        "candidate",
        "queued_for_formalization",
        "formalizing",
        "blocked",
        "research_only",
        "dev_only",
        "certified",
        "rejected",
        "superseded",
        "archived",
    }
    aliases = {
        "draft": "candidate",
        "pending": "proposed",
        "active": "candidate",
        "approved": "certified",
    }
    if normalized in allowed:
        return normalized
    return aliases.get(normalized, "candidate")


def _normalize_downstream_kind(value: Any) -> str:
    normalized = _enumish(value) or "research_only"
    allowed = {
        "research_only",
        "dev_only",
        "research_then_dev",
        "dev_then_research",
        "no_downstream",
    }
    aliases = {
        "research": "research_only",
        "dev": "dev_only",
        "none": "no_downstream",
    }
    if normalized in allowed:
        return normalized
    return aliases.get(normalized, "research_only")


def _normalize_role(value: Any, default: str = "system") -> str:
    normalized = _enumish(value) or default
    allowed = {
        "user",
        "planner",
        "claim_graph_agent",
        "formalizer",
        "research",
        "dev",
        "policy_engine",
        "human_reviewer",
        "system",
    }
    return normalized if normalized in allowed else default


def _candidate_objects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        items = value.values()
    else:
        items = value or []
    return [dict(item) for item in items if isinstance(item, dict)]


def _normalize_claim_candidate(raw_claim: dict[str, Any], project_id: str, index: int) -> dict[str, Any]:
    title = str(raw_claim.get("title") or f"Claim {index}")
    role = _enumish(raw_claim.get("role") or "premise")
    claim_class = _normalize_claim_class(raw_claim.get("claim_class"), role)
    nl_statement = str(
        raw_claim.get("nl_statement")
        or raw_claim.get("statement")
        or raw_claim.get("text")
        or raw_claim.get("normalized_statement")
        or title
    )
    claim_id = str(raw_claim.get("claim_id") or raw_claim.get("id") or f"claim.{_slug(project_id.split('.')[-1], 'project')}.{_slug(title, str(index))}")
    normalized_statement = str(raw_claim.get("normalized_statement") or raw_claim.get("statement") or nl_statement)
    raw_priority = raw_claim.get("priority")
    if isinstance(raw_priority, str):
        priority_map = {"high": 90, "medium": 60, "low": 30}
        priority = priority_map.get(raw_priority.strip().lower(), 50)
    else:
        try:
            priority = int(raw_priority or 50)
        except Exception:
            priority = 50
    provenance = dict(raw_claim.get("provenance") or {})
    semantics_guard = dict(raw_claim.get("semantics_guard") or {})
    return {
        "claim_id": claim_id,
        "title": title,
        "nl_statement": nl_statement,
        "normalized_statement": normalized_statement,
        "intent_gloss": str(raw_claim.get("intent_gloss") or normalized_statement),
        "claim_class": claim_class,
        "claim_kind": _normalize_claim_kind(raw_claim.get("claim_kind"), claim_class),
        "status": _normalize_claim_status(raw_claim.get("status")),
        "formalization_required": bool(raw_claim.get("formalization_required", True)),
        "downstream_kind": _normalize_downstream_kind(raw_claim.get("downstream_kind")),
        "priority": priority,
        "tags": [str(item) for item in list(raw_claim.get("tags") or [])],
        "notes": [str(item) for item in list(raw_claim.get("notes") or [])],
        "scope": _normalize_scope_candidate(raw_claim.get("scope")),
        "semantics_guard": {
            "must_preserve": [title],
            "allowed_weakenings": [str(item) for item in list(semantics_guard.get("allowed_weakenings") or [])],
            "forbidden_weakenings": [str(item) for item in list(semantics_guard.get("forbidden_weakenings") or [])],
            "forbidden_strengthenings": [str(item) for item in list(semantics_guard.get("forbidden_strengthenings") or [])],
            "backtranslation_required": bool(semantics_guard.get("backtranslation_required", True)),
            "independent_formalizations_required": int(semantics_guard.get("independent_formalizations_required", 2)),
        },
        "policy": _normalize_claim_policy_candidate(raw_claim.get("policy")),
        "provenance": {
            "created_by_role": _normalize_role(provenance.get("created_by_role")),
            "source_anchors": _normalize_source_anchors(
                provenance.get("source_anchors"),
                project_id,
                title,
                raw_claim,
            ),
            "last_reviewed_by_role": _normalize_role(provenance.get("last_reviewed_by_role")),
            "review_notes": [str(item) for item in list((provenance.get("review_notes") or []))],
        },
        "owner_role": _normalize_role(raw_claim.get("owner_role")),
        "reviewer_roles": [
            _normalize_role(item, "human_reviewer")
            for item in list(raw_claim.get("reviewer_roles") or ["human_reviewer", "policy_engine"])
        ],
        "supersedes": [str(item) for item in list(raw_claim.get("supersedes") or [])],
    }


def _normalize_relation_candidate(raw_relation: dict[str, Any], index: int) -> dict[str, Any]:
    relation_type = _enumish(raw_relation.get("relation_type") or "depends_on")
    relation_aliases = {
        "supports": "depends_on",
        "derives": "depends_on",
        "cites": "motivates",
        "interprets": "refines",
        "applies_to": "specializes",
        "requires": "blocks",
        "weakens": "alternative_to",
        "strengthens": "motivates",
    }
    allowed = {
        "depends_on","refines","specializes","generalizes","conflicts_with",
        "supersedes","decomposes_into","blocks","motivates","alternative_to",
    }
    relation_type = relation_type if relation_type in allowed else relation_aliases.get(relation_type, "depends_on")
    status = _enumish(raw_relation.get("status") or "provisional")
    if status not in {"active", "provisional", "rejected", "superseded", "archived"}:
        status = "provisional"
    source_id = str(raw_relation.get("from_claim_id") or raw_relation.get("source_id") or "")
    target_id = str(raw_relation.get("to_claim_id") or raw_relation.get("target_id") or "")
    return {
        "relation_id": str(raw_relation.get("relation_id") or f"rel.{_slug(source_id, 'source')}.{relation_type}.{_slug(target_id, 'target')}.{index}"),
        "from_claim_id": source_id,
        "to_claim_id": target_id,
        "relation_type": relation_type,
        "status": status,
        "required_for_promotion": bool(raw_relation.get("required_for_promotion", False)),
        "rationale": str(raw_relation.get("rationale") or ""),
    }


class PipelineOrchestrator:
    """
    End-to-end pipeline controller.

    Usage:
        config = PipelineConfig(project_id="project.my_thing")
        orch = PipelineOrchestrator(config)
        result = await orch.run("Prove that the dispatch algorithm converges.")
    """

    def __init__(
        self,
        config: PipelineConfig,
        llm: LLMClient | None = None,
        store: ArtifactStore | None = None,
    ):
        self.config = config
        self.llm = llm or llm_client
        self.store = store or ArtifactStore(config.data_dir)
        self._proof_client = build_proof_protocol_client(config)

        # agents
        self.planner = PlannerAgent(config, self.llm)
        self.claim_graph_agent = ClaimGraphAgent(config, self.llm)
        self.formalizer_a = FormalizerAgent(config, self.llm, label="A")
        self.formalizer_b = FormalizerAgent(config, self.llm, label="B")
        self.verifier = ProofVerifierAgent(config, self.llm)
        self.auditor = AuditorAgent(config, self.llm)
        self.research = ResearchAgent(config, self.llm)
        self.dev = DevAgent(config, self.llm)
        self.policy = PolicyEngineAgent(config, self.llm)
        self.promotion_state_machine = PromotionStateMachine(self.store)
        self.last_claim_structuring_workflow: ClaimStructuringWorkflowState | None = None
        self.last_dual_formalization_workflow: DualFormalizationWorkflowState | None = None
        self.last_audit_workflow: AuditWorkflowState | None = None

    @property
    def proof_client(self) -> ProofProtocolClient:
        return self._proof_client

    @proof_client.setter
    def proof_client(self, value: ProofProtocolClient) -> None:
        self._proof_client = value

    # ===================================================================
    # Top-level entry point
    # ===================================================================

    async def run(self, user_input: str) -> PipelineResult:
        """Full pipeline execution for a user request."""
        log.info(f"=== Pipeline start: {user_input[:80]}... ===")
        result = PipelineResult(project_id=self.config.project_id)

        # Phase 1: Claim structuring
        claim_graph, workflow = await self.run_claim_structuring_workflow(user_input)
        self.last_claim_structuring_workflow = workflow
        result.claim_structuring_workflow = workflow.model_dump(
            mode="json",
            exclude_none=True,
        )
        result.claim_graph = claim_graph
        self.store.save_claim_graph(claim_graph)
        log.info(f"Phase 1 complete: {len(claim_graph.claims)} claims")

        # Phase 2: Formalization & audit (per claim)
        for claim in claim_graph.claims:
            if not claim.formalization_required:
                log.info(f"Skipping {claim.claim_id}: formalization not required")
                continue

            phase2 = await self.phase2_formalize_and_audit(claim_graph, claim)
            result.phase2_results[claim.claim_id] = phase2

        # Phase 3: Evidence collection & profile recompute
        for claim_id, p2 in result.phase2_results.items():
            if p2.get("profile") and p2["profile"].get("gate") in (
                "research_only", "dev_guarded", "certified"
            ):
                phase3 = await self.phase3_evidence_and_execution(
                    claim_graph, claim_id, p2
                )
                result.phase3_results[claim_id] = phase3

        log.info("=== Pipeline complete ===")
        return result

    # ===================================================================
    # Phase 1: Claim Structuring
    # ===================================================================

    async def phase1_claim_structuring(self, user_input: str) -> ClaimGraph:
        """Return the admitted ClaimGraph from the explicit structuring workflow."""
        claim_graph, workflow = await self.run_claim_structuring_workflow(user_input)
        self.last_claim_structuring_workflow = workflow
        return claim_graph

    async def run_claim_structuring_workflow(
        self,
        user_input: str,
    ) -> tuple[ClaimGraph, ClaimStructuringWorkflowState]:
        """Execute planner admission, structuring, validation, and retry checkpoints."""
        workflow = ClaimStructuringWorkflowState(
            project_id=self.config.project_id,
            user_input=user_input,
            max_attempts=max(1, self.config.max_retries_per_phase),
        )
        self.last_claim_structuring_workflow = workflow
        workflow.transition(ClaimStructuringStage.pending, note="phase1 workflow initialized")

        planner_ctx = {
            "user_input": user_input,
            "claim_graph": None,
            "assurance_profiles": None,
        }
        planner_out = await self.planner.run(planner_ctx)
        planner_payload = planner_out["output"]
        workflow.planner_action = str(planner_payload.get("action") or "").strip()
        workflow.planner_rationale = str(planner_payload.get("rationale") or "")
        workflow.planner_warnings = [
            str(item) for item in (planner_payload.get("warnings") or [])
        ]
        workflow.planner_prompt_lineage = planner_out.get("lineage")
        log.info(f"Planner action: {workflow.planner_action}")

        if workflow.planner_action not in STRUCTURING_PLANNER_ACTIONS:
            reason = (
                f"Planner action {workflow.planner_action!r} does not admit "
                "claim-structuring progression."
            )
            workflow.mark_failed(reason)
            self._persist_claim_structuring_workflow(workflow)
            raise RuntimeError(reason)

        planner_claim_graph = planner_payload.get("claim_graph_update")
        retry_guidance = workflow.planner_rationale

        for attempt_number in range(1, workflow.max_attempts + 1):
            workflow.transition(
                ClaimStructuringStage.structuring,
                note=f"structuring attempt {attempt_number}",
            )

            source = "claim_graph_agent"
            if attempt_number == 1 and isinstance(planner_claim_graph, dict):
                source = "planner"
                cg_data = planner_claim_graph
                prompt_lineage = workflow.planner_prompt_lineage
            else:
                cga_ctx = {
                    "project_id": self.config.project_id,
                    "user_input": user_input,
                    "planner_guidance": retry_guidance,
                    "existing_claims": None,
                }
                cga_out = await self.claim_graph_agent.run(cga_ctx)
                cg_data = cga_out["output"]
                prompt_lineage = cga_out.get("lineage")

            workflow.transition(
                ClaimStructuringStage.validating,
                note=f"schema checkpoint for {source} attempt {attempt_number}",
            )
            try:
                claim_graph = self._validate_claim_graph_candidate(cg_data)
            except Exception as exc:
                errors = self._claim_graph_validation_errors(exc)
                workflow.last_validation_errors = errors
                workflow.record_attempt(
                    attempt_number=attempt_number,
                    source=source,
                    note="schema checkpoint failed",
                    validation_errors=errors,
                    prompt_lineage=prompt_lineage,
                )
                log.error("ClaimGraph validation failed: %s", errors[0])
                log.debug(
                    "Raw data: %s",
                    json.dumps(cg_data, indent=2, default=str)[:2000],
                )
                if attempt_number >= workflow.max_attempts:
                    workflow.mark_failed(
                        "Claim-structuring retries exhausted.",
                        validation_errors=errors,
                    )
                    self._persist_claim_structuring_workflow(workflow)
                    raise RuntimeError(errors[0]) from exc
                retry_guidance = self._build_structuring_retry_guidance(
                    workflow.planner_rationale,
                    errors,
                )
                continue

            workflow.record_attempt(
                attempt_number=attempt_number,
                source=source,
                note="schema checkpoint passed",
                prompt_lineage=prompt_lineage,
            )
            workflow.mark_admitted(
                self._scalar_text(claim_graph.graph_id),
                note="claim graph admitted after validation checkpoint",
            )
            self._persist_claim_structuring_workflow(workflow)
            return claim_graph, workflow

        raise RuntimeError("Claim-structuring workflow exhausted without admission.")

    def _validate_claim_graph_candidate(self, cg_data: Any) -> ClaimGraph:
        if not isinstance(cg_data, dict):
            raise TypeError("ClaimGraph candidate must be a JSON object.")
        candidate = {
            key: value
            for key, value in dict(cg_data).items()
            if key in {
                "schema_version",
                "graph_id",
                "project_id",
                "created_at",
                "updated_at",
                "description",
                "root_claim_ids",
                "claims",
                "relations",
                "graph_policy",
            }
        }
        candidate.setdefault("schema_version", "1.0.0")
        if not candidate.get("project_id"):
            candidate["project_id"] = self.config.project_id
        if not candidate.get("created_at"):
            candidate["created_at"] = now_utc().isoformat()
        if not candidate.get("updated_at"):
            candidate["updated_at"] = now_utc().isoformat()
        if not candidate.get("graph_id"):
            candidate["graph_id"] = f"cg.{_slug(self.config.project_id.split('.')[-1], 'project')}"
        candidate["claims"] = [
            _normalize_claim_candidate(raw_claim, self.config.project_id, index)
            for index, raw_claim in enumerate(_candidate_objects(candidate.get("claims")), start=1)
        ]
        candidate["relations"] = [
            _normalize_relation_candidate(raw_relation, index)
            for index, raw_relation in enumerate(_candidate_objects(candidate.get("relations")), start=1)
        ]
        root_claim_ids = [str(item) for item in list(candidate.get("root_claim_ids") or []) if str(item)]
        if not root_claim_ids:
            incoming = {str(item.get("to_claim_id") or "") for item in candidate["relations"]}
            root_claim_ids = [
                str(item.get("claim_id") or "")
                for item in candidate["claims"]
                if str(item.get("claim_id") or "") and str(item.get("claim_id") or "") not in incoming
            ]
            if not root_claim_ids and candidate["claims"]:
                root_claim_ids = [str(candidate["claims"][0].get("claim_id") or "")]
        candidate["root_claim_ids"] = root_claim_ids
        candidate["graph_policy"] = _normalize_graph_policy_candidate(candidate.get("graph_policy"))
        return ClaimGraph.model_validate(candidate)

    def _claim_graph_validation_errors(self, exc: Exception) -> list[str]:
        error_items = getattr(exc, "errors", None)
        if callable(error_items):
            messages = []
            for item in error_items():
                loc = ".".join(str(part) for part in item.get("loc", []))
                messages.append(f"{loc}: {item.get('msg')}")
            if messages:
                return messages
        return [str(exc)]

    def _build_structuring_retry_guidance(
        self,
        planner_rationale: str,
        errors: list[str],
    ) -> str:
        error_block = "\n".join(f"- {error}" for error in errors)
        parts = []
        if planner_rationale:
            parts.append(planner_rationale)
        parts.append("The previous attempt failed schema validation.")
        parts.append(error_block)
        parts.append("Return a corrected ClaimGraph that satisfies the canonical schema.")
        return "\n".join(parts)

    # ===================================================================
    # Phase 2: Formalization & Audit
    # ===================================================================

    def _scalar_text(self, value: Any) -> str:
        scalar = getattr(value, "value", value)
        return canonical_artifact_id(scalar)

    def _dual_formalization_required(self, claim_graph: ClaimGraph, claim: Any) -> bool:
        claim_class = self._scalar_text(getattr(claim, "claim_class", ""))
        graph_policy = getattr(claim_graph, "graph_policy", None)
        graph_requires_dual = bool(
            getattr(graph_policy, "require_dual_formalization_for_core_claims", False)
        )
        semantics_guard = getattr(claim, "semantics_guard", None)
        independent_required = int(
            getattr(semantics_guard, "independent_formalizations_required", 1) or 1
        )
        return bool(
            self.config.require_dual_formalization
            or independent_required >= 2
            or (graph_requires_dual and claim_class == "core_claim")
        )

    def _normalize_formalizer_output(
        self,
        claim_id: str,
        label: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(output)
        target_backend = str(
            normalized.get("target_backend")
            or self.config.proof_protocol.target_backend_id
        )
        proof_language = proof_backend_family(target_backend)
        normalized.setdefault("claim_id", claim_id)
        normalized.setdefault("formalizer", label)
        normalized.setdefault("target_backend", target_backend)
        normalized.setdefault("proof_language", proof_language)
        normalized.setdefault(
            "theorem_statement",
            str(
                normalized.get("back_translation")
                or normalized.get("primary_target")
                or "True"
            ),
        )
        normalized.setdefault("assumptions_used", [])
        normalized.setdefault("definition_names", [])
        normalized.setdefault("open_obligation_locations", [])
        normalized.setdefault("confidence", 0.0)
        return normalized

    def _build_formalization_attempt(
        self,
        workflow: DualFormalizationWorkflowState,
        label: str,
        result: dict[str, Any] | BaseException,
    ) -> FormalizationAttempt:
        lineage = FormalizationAttemptLineage(
            project_id=workflow.project_id,
            claim_graph_id=workflow.claim_graph_id,
            claim_id=workflow.claim_id,
            workflow_id=workflow.workflow_id,
            source_role=f"formalizer_{label.lower()}",
        )
        if isinstance(result, BaseException):
            return FormalizationAttempt(
                formalizer_label=label,
                status=FormalizationAttemptStatus.failed,
                lineage=lineage,
                error=str(result),
                warnings=["Formalizer raised before producing a canonical output."],
            )

        output = self._normalize_formalizer_output(
            workflow.claim_id,
            label,
            dict(result.get("output") or {}),
        )
        return FormalizationAttempt(
            formalizer_label=label,
            status=FormalizationAttemptStatus.succeeded,
            lineage=lineage,
            output=output,
            output_sha256=output_sha256(output),
            session_name=str(output.get("session_name") or ""),
            module_name=str(output.get("module_name") or ""),
            primary_target=str(output.get("primary_target") or ""),
            back_translation=str(output.get("back_translation") or ""),
            divergence_notes=str(output.get("divergence_notes") or ""),
            warnings=[str(item) for item in list(output.get("warnings") or [])],
            prompt_lineage=result.get("lineage"),
        )

    def _compute_formalization_divergence(
        self,
        attempts: list[FormalizationAttempt],
    ) -> FormalizationDivergence:
        successful = [
            attempt for attempt in attempts
            if attempt.status == FormalizationAttemptStatus.succeeded and attempt.output
        ]
        failed = [
            attempt.formalizer_label for attempt in attempts
            if attempt.status == FormalizationAttemptStatus.failed
        ]
        if not successful:
            return FormalizationDivergence(
                classification="all_failed",
                summary="No formalizer produced a usable theory candidate.",
                successful_formalizers=[],
                failed_formalizers=failed,
                notes=["Dual formalization workflow terminated without a buildable candidate."],
            )

        if len(successful) == 1:
            label = successful[0].formalizer_label
            notes = []
            if failed:
                notes.append(
                    f"Only formalizer {label} produced a candidate; "
                    f"{', '.join(sorted(failed))} failed."
                )
            return FormalizationDivergence(
                classification="single_success",
                summary=f"Single surviving formalization from {label}.",
                successful_formalizers=[label],
                failed_formalizers=failed,
                notes=notes,
            )

        by_label = {attempt.formalizer_label: attempt for attempt in successful}
        attempt_a = by_label.get("A") or successful[0]
        attempt_b = by_label.get("B") or successful[1]
        theorem_a = str(attempt_a.primary_target or "")
        theorem_b = str(attempt_b.primary_target or "")
        backtranslation_a = str(attempt_a.back_translation or "").strip()
        backtranslation_b = str(attempt_b.back_translation or "").strip()
        assumptions_a = normalize_assumptions(attempt_a.output)
        assumptions_b = normalize_assumptions(attempt_b.output)
        theorem_match = bool(theorem_a and theorem_a == theorem_b)
        backtranslation_match = bool(
            backtranslation_a and backtranslation_a == backtranslation_b
        )
        code_sha_match = bool(
            attempt_a.output_sha256
            and attempt_a.output_sha256 == attempt_b.output_sha256
        )
        only_in_a = sorted(set(assumptions_a).difference(assumptions_b))
        only_in_b = sorted(set(assumptions_b).difference(assumptions_a))
        divergent = not (
            theorem_match
            and backtranslation_match
            and not only_in_a
            and not only_in_b
        )
        notes = []
        if theorem_a or theorem_b:
            notes.append(f"Primary target A={theorem_a or 'n/a'} B={theorem_b or 'n/a'}.")
        if only_in_a or only_in_b:
            notes.append("Formalizers used different assumption carriers/statements.")
        if failed:
            notes.append(f"Additional failed formalizers: {', '.join(sorted(failed))}.")
        classification = "diverged" if divergent else "aligned"
        summary = (
            "Independent formalizers diverged on theorem shape or assumptions."
            if divergent
            else "Independent formalizers converged on the same theorem intent."
        )
        return FormalizationDivergence(
            classification=classification,
            summary=summary,
            successful_formalizers=sorted(by_label),
            failed_formalizers=failed,
            primary_target_match=theorem_match,
            back_translation_match=backtranslation_match,
            code_sha_match=code_sha_match,
            assumptions_only_in_a=only_in_a,
            assumptions_only_in_b=only_in_b,
            notes=notes,
        )

    def _persist_formalization_workflow(
        self,
        workflow: DualFormalizationWorkflowState,
    ) -> None:
        for attempt in workflow.attempts:
            metadata = {
                "workflow_id": workflow.workflow_id,
                "project_id": workflow.project_id,
                "claim_graph_id": workflow.claim_graph_id,
                "status": attempt.status.value,
                "formalizer_label": attempt.formalizer_label,
                "output_sha256": attempt.output_sha256,
                "session_name": attempt.session_name,
                "module_name": attempt.module_name,
                "primary_target": attempt.primary_target,
                "back_translation": attempt.back_translation,
                "divergence_notes": attempt.divergence_notes,
                "warnings": attempt.warnings,
                "error": attempt.error,
                "prompt_lineage": attempt.prompt_lineage,
            }
            self.store.append_review_event(
                target_claim_id=workflow.claim_id,
                artifact_kind="formalization_attempt",
                artifact_id=attempt.attempt_id,
                event_type="formalization_attempt",
                actor=attempt.lineage.source_role,
                actor_role="author",
                notes=attempt.error or attempt.divergence_notes or "",
                metadata=metadata,
            )
        self.store.append_review_event(
            target_claim_id=workflow.claim_id,
            artifact_kind="formalization_workflow",
            artifact_id=workflow.workflow_id,
            event_type="dual_formalization_workflow",
            actor="orchestrator",
            actor_role="system",
            notes=(workflow.divergence.summary if workflow.divergence else ""),
            metadata={
                "project_id": workflow.project_id,
                "claim_graph_id": workflow.claim_graph_id,
                "selected_formalizers": workflow.selected_formalizers,
                "successful_formalizers": workflow.successful_formalizers,
                "failed_formalizers": workflow.failed_formalizers,
                "state": workflow.state.value,
                "divergence": (
                    workflow.divergence.model_dump(mode="json", exclude_none=True)
                    if workflow.divergence
                    else None
                ),
            },
        )

    def _persist_claim_structuring_workflow(
        self,
        workflow: ClaimStructuringWorkflowState,
    ) -> None:
        if workflow.planner_action:
            self.store.append_review_event(
                target_claim_id=workflow.project_id,
                artifact_kind="claim_structuring_planner",
                artifact_id=f"{workflow.workflow_id}.planner",
                event_type="claim_structuring_planner",
                actor="planner",
                actor_role="author",
                notes=workflow.planner_rationale,
                metadata={
                    "workflow_id": workflow.workflow_id,
                    "project_id": workflow.project_id,
                    "planner_action": workflow.planner_action,
                    "planner_warnings": workflow.planner_warnings,
                    "prompt_lineage": workflow.planner_prompt_lineage,
                },
            )
        for attempt in workflow.attempts:
            self.store.append_review_event(
                target_claim_id=workflow.project_id,
                artifact_kind="claim_structuring_attempt",
                artifact_id=f"{workflow.workflow_id}.attempt.{attempt.attempt_number}",
                event_type="claim_structuring_attempt",
                actor=attempt.source,
                actor_role="author",
                notes=attempt.note or "",
                metadata={
                    "workflow_id": workflow.workflow_id,
                    "project_id": workflow.project_id,
                    "attempt_number": attempt.attempt_number,
                    "source": attempt.source,
                    "validation_errors": attempt.validation_errors,
                    "prompt_lineage": attempt.prompt_lineage,
                },
            )
        self.store.append_review_event(
            target_claim_id=workflow.project_id,
            artifact_kind="claim_structuring_workflow",
            artifact_id=workflow.workflow_id,
            event_type="claim_structuring_workflow",
            actor="orchestrator",
            actor_role="system",
            notes=workflow.failure_reason or "",
            metadata={
                "project_id": workflow.project_id,
                "state": workflow.state.value,
                "planner_action": workflow.planner_action,
                "planner_warnings": workflow.planner_warnings,
                "planner_prompt_lineage": workflow.planner_prompt_lineage,
                "admitted_graph_id": workflow.admitted_graph_id,
                "failure_reason": workflow.failure_reason,
                "last_validation_errors": workflow.last_validation_errors,
            },
        )

    async def run_dual_formalization_workflow(
        self,
        claim_graph: ClaimGraph,
        claim: Any,
    ) -> DualFormalizationWorkflowState:
        claim_id = self._scalar_text(claim.claim_id)
        workflow = DualFormalizationWorkflowState(
            project_id=self.config.project_id,
            claim_graph_id=self._scalar_text(claim_graph.graph_id),
            claim_id=claim_id,
            dual_required=self._dual_formalization_required(claim_graph, claim),
        )
        workflow.transition(
            DualFormalizationStage.pending,
            note="phase2 dual-formalization workflow initialized",
        )
        formal_ctx = {
            "claim": claim.model_dump(mode="json"),
            "claim_policy": claim.policy.model_dump(mode="json"),
            "graph_policy": (
                claim_graph.graph_policy.model_dump(mode="json")
                if claim_graph.graph_policy
                else None
            ),
            "existing_theories": [],
            "target_backend": self.config.proof_protocol.target_backend_id,
        }
        workflow.selected_formalizers = ["A", "B"] if workflow.dual_required else ["A"]
        workflow.transition(
            DualFormalizationStage.formalizing,
            note="dispatching independent formalizer attempts",
        )

        tasks = {
            "A": self.formalizer_a.run(formal_ctx),
        }
        if workflow.dual_required:
            tasks["B"] = self.formalizer_b.run(formal_ctx)
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for label, outcome in zip(tasks, gathered, strict=True):
            workflow.record_attempt(
                self._build_formalization_attempt(workflow, label, outcome)
            )

        divergence = self._compute_formalization_divergence(workflow.attempts)
        if not workflow.successful_formalizers:
            workflow.mark_failed(divergence.summary, divergence=divergence)
            self.last_dual_formalization_workflow = workflow
            self._persist_formalization_workflow(workflow)
            return workflow
        workflow.mark_completed(divergence)
        self.last_dual_formalization_workflow = workflow
        self._persist_formalization_workflow(workflow)
        return workflow

    def _fallback_audit_from_formalization_workflow(
        self,
        workflow: DualFormalizationWorkflowState,
        claim: Any,
    ) -> dict[str, Any]:
        notes = list((workflow.divergence.notes if workflow.divergence else []))
        notes.append("Audit fell back to workflow-level diagnostics because no formalization survived.")
        return {
            "claim_id": self._scalar_text(claim.claim_id),
            "audit_kind": "comparison",
            "trust_frontier": {
                "global_axiom_dependency_count": 0,
                "locale_assumption_count": 0,
                "premise_assumption_count": 0,
                "oracle_dependency_count": 0,
                "unreviewed_import_count": 0,
                "transitive_dependency_count": 0,
                "reviewed_global_axiom_ids": [],
                "oracle_ids": [],
                "hotspot_artifact_ids": [],
                "notes": notes,
            },
            "conservativity": {
                "definitional_only": False,
                "reviewed_global_axioms_required": False,
                "compile_away_known": False,
                "nondefinitional_hotspots": [],
                "trusted_mechanisms": [],
                "flagged_mechanisms": [],
            },
            "model_health": {
                "locale_satisfiability": "untested",
                "countermodel_probe": "untested",
                "vacuity_check": "inconclusive",
                "premise_sensitivity": "untested",
                "conclusion_perturbation": "untested",
                "notes": notes,
            },
            "intent_alignment": {
                "independent_formalization_count": len(workflow.successful_formalizers),
                "agreement_score": 0.0,
                "backtranslation_review": "needs_revision",
                "paraphrase_robustness_score": 0.0,
                "semantics_guard_violations": [
                    "No successful formalization attempt is available for audit."
                ],
                "reviewer_notes": notes,
            },
            "blocking_issues": ["No successful formalization attempt survived phase2."],
            "warnings": notes,
            "recommendation": "block",
        }

    def _phase2_session_dir(self, claim_id: str, label: str) -> Path:
        return Path(self.config.data_dir) / "theories" / claim_id / label

    def _select_audit_candidate(
        self,
        formalizer_outputs: dict[str, dict[str, Any]],
        verifier_results: dict[str, dict[str, Any]],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        if not formalizer_outputs:
            raise RuntimeError("Cannot select an audit target without formalizer outputs.")

        def rank(label: str) -> tuple[int, int, int]:
            verifier = verifier_results.get(label, {})
            proof_status = verifier.get("proof_status")
            if proof_status == "proof_complete":
                status_rank = 3
            elif proof_status == "built":
                status_rank = 2
            elif verifier.get("build_success"):
                status_rank = 1
            else:
                status_rank = 0
            theorem_rank = len(verifier.get("theorems_found", []))
            obligation_rank = -(
                int(verifier.get("sorry_count", 0)) + int(verifier.get("oops_count", 0))
            )
            return status_rank, theorem_rank, obligation_rank

        label = max(formalizer_outputs, key=rank)
        return label, formalizer_outputs[label], verifier_results.get(label, {})

    def _derive_probe_statement(self, formalizer_output: dict[str, Any]) -> str:
        statement = str(formalizer_output.get("probe_statement") or "").strip()
        if statement:
            return statement
        return "True"

    def _derive_robustness_request(
        self,
        *,
        claim_id: str,
        label: str,
        formalizer_output: dict[str, Any],
    ) -> dict[str, Any] | None:
        proof_language = str(
            formalizer_output.get("proof_language")
            or proof_backend_family(
                str(
                    formalizer_output.get("target_backend")
                    or self.config.proof_protocol.target_backend_id
                )
            )
        )
        source = str(formalizer_output.get("proof_source") or "").strip()
        if not source or proof_language != "isabelle":
            return None

        premise_variants = []
        premise_match = re.search(r"(?m)^(\s*assumes\b.*)$", source)
        if premise_match:
            premise_variants.append(
                {
                    "label": "drop first assumption",
                    "marker": premise_match.group(1) + "\n",
                }
            )

        conclusion_variants = []
        conclusion_match = re.search(r'(?m)^\s*shows\s+("[^"]+")', source)
        if conclusion_match:
            old_text = conclusion_match.group(1)
            conclusion_variants.append(
                {
                    "label": "strengthen goal",
                    "old_text": old_text,
                    "new_text": '"False"' if old_text != '"False"' else '"True"',
                }
            )

        if not premise_variants and not conclusion_variants:
            return None

        return {
            "session_name": str(formalizer_output.get("session_name") or f"Session_{label}"),
            "target_theory": str(formalizer_output.get("module_name") or f"Module_{label}"),
            "target_theorem": str(
                formalizer_output.get("primary_target") or f"target_{label.lower()}"
            ),
            "base_theory_body": source,
            "workspace_root": str(
                Path(self.config.data_dir) / "proof_audit_workspaces" / claim_id / label
            ),
            "premise_variants": premise_variants,
            "conclusion_variants": conclusion_variants,
        }

    def _build_proof_audit_request(
        self,
        *,
        claim_id: str,
        label: str,
        formalizer_output: dict[str, Any],
    ) -> dict[str, Any]:
        session_name = str(formalizer_output.get("session_name") or f"Session_{label}")
        theory_name = str(formalizer_output.get("module_name") or f"Module_{label}")
        target_theorem = str(
            formalizer_output.get("primary_target") or f"target_{label.lower()}"
        )
        session_dir = str(self._phase2_session_dir(claim_id, label))
        request = {
            "claim_id": claim_id,
            "session_name": session_name,
            "session_dir": session_dir,
            "target_theory": theory_name,
            "target_theorem": target_theorem,
            "proof_source": str(formalizer_output.get("proof_source") or ""),
            "theorem_statement": str(
                formalizer_output.get("theorem_statement")
                or formalizer_output.get("back_translation")
                or target_theorem
            ),
            "target_backend": str(
                formalizer_output.get("target_backend")
                or self.config.proof_protocol.target_backend_id
            ),
            "resource_policy": {
                "wall_ms": max(
                    1,
                    int(self.config.proof_protocol.budget.wall_timeout_seconds * 1000),
                ),
                "idle_ms": max(
                    1,
                    int(self.config.proof_protocol.budget.idle_timeout_seconds * 1000),
                ),
                "cancel_grace_ms": max(
                    1,
                    int(self.config.proof_protocol.budget.cancel_grace_seconds * 1000),
                ),
                "max_rss_mb": max(1, int(self.config.proof_protocol.budget.max_rss_mb)),
                "max_output_bytes": max(
                    1,
                    int(self.config.proof_protocol.budget.max_output_bytes),
                ),
                "max_diag_count": max(
                    1,
                    int(self.config.proof_protocol.budget.max_diag_count),
                ),
                "max_children": max(
                    0,
                    int(self.config.proof_protocol.budget.max_children),
                ),
                "max_restarts": max(
                    0,
                    int(self.config.proof_protocol.budget.max_restarts),
                ),
            },
            "export_requirements": ["contractPack"],
            "trust_frontier_requirements": ["trustFrontier"],
            "probe_requirements": ["dependencySlice", "counterexample", "proofSearch"],
        }
        robustness = self._derive_robustness_request(
            claim_id=claim_id,
            label=label,
            formalizer_output=formalizer_output,
        )
        if robustness:
            request["robustness_harness_requirements"] = [
                "premiseDeletion",
                "conclusionPerturbation",
            ]
            request["backend_extension_selection"] = {"robustness_request": robustness}
        return request

    def _build_deterministic_audit_output(
        self,
        *,
        claim: Any,
        formalization_workflow: DualFormalizationWorkflowState,
        proof_audit: dict[str, Any],
    ) -> dict[str, Any]:
        divergence = formalization_workflow.divergence
        trust = dict(proof_audit.get("trust") or {})
        surface = dict(trust.get("surface") or {})
        probe_results = [
            dict(item)
            for item in list(proof_audit.get("probe_results") or [])
            if isinstance(item, dict)
        ]
        robustness_harness = dict(proof_audit.get("robustness_harness") or {})

        def probe_result(*kinds: str) -> dict[str, Any]:
            for item in probe_results:
                if str(item.get("kind") or "") in kinds:
                    return item
            return {}

        counterexample_probe = probe_result("counterexample", "nitpick")

        global_axiom_ids = list(surface.get("global_axiom_ids") or [])
        reviewed_global_axiom_ids = list(surface.get("reviewed_global_axiom_ids") or [])
        oracle_ids = list(surface.get("oracle_ids") or [])
        hotspot_artifact_ids = list(surface.get("imported_theory_hotspots") or [])
        required_independent = int(
            getattr(claim.semantics_guard, "independent_formalizations_required", 1) or 1
        )
        successful_count = len(formalization_workflow.successful_formalizers)
        classification = divergence.classification if divergence else "single_success"

        if classification == "aligned" and successful_count >= required_independent:
            agreement_score = 0.95
            backtranslation_review = "pass"
        elif classification == "diverged":
            agreement_score = 0.55
            backtranslation_review = "needs_revision"
        elif classification == "single_success":
            agreement_score = 0.4
            backtranslation_review = (
                "needs_revision" if successful_count < required_independent else "unreviewed"
            )
        else:
            agreement_score = 0.0
            backtranslation_review = "fail"

        semantics_guard_violations = []
        if successful_count < required_independent:
            semantics_guard_violations.append(
                "Independent formalization requirement was not satisfied."
            )
        if classification == "diverged":
            semantics_guard_violations.append(
                "Independent formalizations diverged on theorem shape or assumptions."
            )

        trust_notes = list(surface.get("notes") or [])
        model_notes = [
            str(probe.get("summary") or "")
            for probe in probe_results
            if str(probe.get("summary") or "")
        ]
        reviewer_notes = list((divergence.notes if divergence else []))
        if divergence:
            reviewer_notes.append(divergence.summary)

        warnings = []
        if classification == "diverged":
            warnings.append("Independent formalizations diverged; review intent alignment.")
        if oracle_ids:
            warnings.append("Review or eliminate theorem-local oracle dependencies.")
        if len(global_axiom_ids) > len(reviewed_global_axiom_ids):
            warnings.append(
                "Review or justify theorem-local global axiom dependencies before promotion."
            )
        if robustness_harness.get("premise_sensitivity") == "fragile":
            warnings.append("Review fragile premise dependencies before promotion.")

        blocking_issues = []
        if counterexample_probe.get("outcome") == "countermodel_found":
            blocking_issues.append(
                "Resolve the countermodel reported by the counterexample probe."
            )
        if classification == "all_failed":
            blocking_issues.append("No successful formalization survived proof-backed audit.")

        recommendation = (
            "block"
            if blocking_issues
            else "needs_revision"
            if warnings or semantics_guard_violations
            else "proceed"
        )
        return {
            "claim_id": self._scalar_text(claim.claim_id),
            "audit_kind": "theorem_local",
            "trust_frontier": {
                "global_axiom_dependency_count": len(global_axiom_ids),
                "locale_assumption_count": len(surface.get("locale_assumptions") or []),
                "premise_assumption_count": len(surface.get("premise_assumptions") or []),
                "oracle_dependency_count": len(oracle_ids),
                "unreviewed_import_count": len(hotspot_artifact_ids),
                "transitive_dependency_count": len(
                    surface.get("transitive_theorem_dependencies") or []
                ),
                "reviewed_global_axiom_ids": reviewed_global_axiom_ids,
                "oracle_ids": oracle_ids,
                "hotspot_artifact_ids": hotspot_artifact_ids,
                "notes": trust_notes,
            },
            "conservativity": {
                "definitional_only": not global_axiom_ids and not oracle_ids,
                "reviewed_global_axioms_required": len(global_axiom_ids)
                > len(reviewed_global_axiom_ids),
                "compile_away_known": False,
                "nondefinitional_hotspots": list(dict.fromkeys(oracle_ids + hotspot_artifact_ids)),
                "trusted_mechanisms": ["definition", "locale", "theorem"],
                "flagged_mechanisms": list(
                    dict.fromkeys(
                        (["oracle"] if oracle_ids else [])
                        + (
                            ["global_axiom"]
                            if len(global_axiom_ids) > len(reviewed_global_axiom_ids)
                            else []
                        )
                    )
                ),
            },
            "model_health": {
                "locale_satisfiability": "untested",
                "countermodel_probe": str(
                    counterexample_probe.get("outcome") or "untested"
                ),
                "vacuity_check": (
                    "fail"
                    if robustness_harness.get("premise_sensitivity") == "fragile"
                    else "pass"
                    if robustness_harness.get("premise_sensitivity") == "stable"
                    else "untested"
                ),
                "premise_sensitivity": str(
                    robustness_harness.get("premise_sensitivity") or "untested"
                ),
                "conclusion_perturbation": str(
                    robustness_harness.get("conclusion_perturbation") or "untested"
                ),
                "notes": model_notes,
            },
            "intent_alignment": {
                "independent_formalization_count": successful_count,
                "agreement_score": agreement_score,
                "backtranslation_review": backtranslation_review,
                "paraphrase_robustness_score": agreement_score,
                "semantics_guard_violations": semantics_guard_violations,
                "reviewer_notes": reviewer_notes,
            },
            "blocking_issues": blocking_issues,
            "warnings": list(dict.fromkeys(warnings)),
            "recommendation": recommendation,
        }

    def _persist_audit_workflow(
        self,
        workflow: AuditWorkflowState,
        *,
        audit_output: dict[str, Any] | None,
        proof_audit: dict[str, Any] | None,
    ) -> None:
        self.store.append_review_event(
            target_claim_id=workflow.claim_id,
            artifact_kind="audit_workflow",
            artifact_id=workflow.workflow_id,
            event_type="audit_workflow",
            actor="orchestrator",
            actor_role="system",
            notes=workflow.failure_reason or "",
            metadata={
                "project_id": workflow.project_id,
                "claim_graph_id": workflow.claim_graph_id,
                "selected_formalizer": workflow.selected_formalizer,
                "session_name": workflow.session_name,
                "session_dir": workflow.session_dir,
                "target_theory": workflow.target_theory,
                "target_theorem": workflow.target_theorem,
                "proof_request_path": workflow.proof_request_path,
                "proof_audit_success": workflow.proof_audit_success,
                "profile_id": workflow.profile_id,
                "blocking_issues": workflow.blocking_issues,
                "warnings": workflow.warnings,
                "audit_output": audit_output,
                "proof_audit": proof_audit,
            },
        )

    def load_promotion_state(self, claim_id: str) -> PromotionCheckpointState:
        profile = self.store.load_assurance_profile_for_claim(claim_id)
        return self.promotion_state_machine.load_state(profile)

    def advance_promotion_state(
        self,
        claim_id: str,
        *,
        target_gate: Gate | str,
        actor: str,
        actor_role: ReviewActorRole | str,
        override: bool = False,
        rationale: str = "",
        notes: str = "",
    ) -> PromotionCheckpointState:
        profile = self.store.load_assurance_profile_for_claim(claim_id)
        return self.promotion_state_machine.transition(
            profile,
            target_gate=target_gate,
            actor=actor,
            actor_role=actor_role,
            override=override,
            rationale=rationale,
            notes=notes,
        )

    async def run_audit_workflow(
        self,
        claim_graph: ClaimGraph,
        claim: Any,
        formalization_workflow: DualFormalizationWorkflowState,
        formalizer_outputs: dict[str, dict[str, Any]],
        verifier_results: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], AuditWorkflowState]:
        workflow = AuditWorkflowState(
            project_id=self.config.project_id,
            claim_graph_id=self._scalar_text(claim_graph.graph_id),
            claim_id=self._scalar_text(claim.claim_id),
        )
        self.last_audit_workflow = workflow
        workflow.transition(AuditWorkflowStage.pending, note="phase2 audit workflow initialized")

        try:
            workflow.transition(
                AuditWorkflowStage.selecting_artifact,
                note="selecting canonical formal artifact for proof audit",
            )
            label, formalizer_output, _ = self._select_audit_candidate(
                formalizer_outputs,
                verifier_results,
            )
            workflow.selected_formalizer = label
            workflow.session_name = str(formalizer_output.get("session_name") or "")
            workflow.session_dir = str(self._phase2_session_dir(workflow.claim_id, label))
            workflow.target_theory = str(formalizer_output.get("module_name") or "")
            workflow.target_theorem = str(formalizer_output.get("primary_target") or "")

            request_dir = (
                Path(self.config.data_dir) / "proof_audit_requests" / workflow.claim_id / label
            )
            request_dir.mkdir(parents=True, exist_ok=True)
            request_path = request_dir / "audit-request.json"
            request_payload = self._build_proof_audit_request(
                claim_id=workflow.claim_id,
                label=label,
                formalizer_output=formalizer_output,
            )
            request_path.write_text(json.dumps(request_payload, indent=2), encoding="utf-8")
            workflow.proof_request_path = str(request_path)

            workflow.transition(
                AuditWorkflowStage.proof_audit,
                note=f"invoking FWP proof audit for formalizer {label}",
            )
            proof_audit = self.proof_client.run_audit(request_path)
            response_path = request_dir / "audit-response.json"
            response_path.write_text(json.dumps(proof_audit, indent=2), encoding="utf-8")
            workflow.proof_audit_success = bool(proof_audit.get("success"))

            workflow.transition(
                AuditWorkflowStage.profiling,
                note="computing deterministic assurance profile from proof outputs",
            )
            audit_output = self._build_deterministic_audit_output(
                claim=claim,
                formalization_workflow=formalization_workflow,
                proof_audit=proof_audit,
            )
            profile = compute_assurance_profile(
                AssuranceComputationInput(
                    project_id=self.config.project_id,
                    claim=claim.model_dump(mode="json"),
                    verifier_output=verifier_results,
                    audit_output=audit_output,
                    research_output=None,
                    coverage_data=None,
                    claim_graph=claim_graph,
                    runner_trust=proof_audit.get("trust"),
                    probe_results=proof_audit.get("probe_results"),
                    robustness_harness=proof_audit.get("robustness_harness"),
                    claim_graph_ref=self._scalar_text(claim_graph.graph_id),
                )
            )
            self.store.save_assurance_profile(profile)
            profile_data = profile.model_dump(mode="json", exclude_none=True)
            workflow.mark_completed(
                profile_id=str(profile_data.get("profile_id") or ""),
                blocking_issues=list(audit_output.get("blocking_issues") or []),
                warnings=list(audit_output.get("warnings") or []),
            )
            self.last_audit_workflow = workflow
            self._persist_audit_workflow(
                workflow,
                audit_output=audit_output,
                proof_audit=proof_audit,
            )
            return audit_output, proof_audit, profile_data, workflow
        except Exception as exc:
            workflow.mark_failed(str(exc))
            self.last_audit_workflow = workflow
            self._persist_audit_workflow(
                workflow,
                audit_output=None,
                proof_audit=None,
            )
            raise

    async def phase2_formalize_and_audit(
        self, claim_graph: ClaimGraph, claim
    ) -> dict[str, Any]:
        """
        For one claim:
          1. Dual formalization (A + B in parallel)
          2. Proof backend build for each formal artifact
          3. Verifier interprets build results
          4. Auditor analyzes trust frontier + intent alignment
          5. Policy engine produces assurance profile
        """
        claim_data = claim.model_dump(mode="json")
        policy_data = claim.policy.model_dump(mode="json")
        graph_policy = None
        if claim_graph.graph_policy:
            graph_policy = claim_graph.graph_policy.model_dump(mode="json")

        result: dict[str, Any] = {"claim_id": claim.claim_id}

        # --- Step 1: Dual formalization ---
        workflow = await self.run_dual_formalization_workflow(claim_graph, claim)
        self.last_dual_formalization_workflow = workflow
        result["dual_formalization_workflow"] = workflow.model_dump(
            mode="json",
            exclude_none=True,
        )

        formalizer_outputs = {
            attempt.formalizer_label: dict(attempt.output or {})
            for attempt in workflow.attempts
            if attempt.status == FormalizationAttemptStatus.succeeded and attempt.output
        }
        result["formalizer_a"] = formalizer_outputs.get("A")
        result["formalizer_b"] = formalizer_outputs.get("B")

        if not formalizer_outputs:
            result["build_results"] = {}
            result["verifier_results"] = {}
            audit_output = self._fallback_audit_from_formalization_workflow(
                workflow,
                claim,
            )
            result["audit"] = audit_output
            profile = compute_assurance_profile(
                AssuranceComputationInput(
                    project_id=self.config.project_id,
                    claim=claim_data,
                    verifier_output={},
                    audit_output=audit_output,
                    research_output=None,
                    coverage_data=None,
                    claim_graph=claim_graph,
                    claim_graph_ref=self._scalar_text(claim_graph.graph_id),
                )
            )
            profile_data = profile.model_dump(mode="json", exclude_none=True)
            try:
                self.store.save_assurance_profile(profile)
                result["profile"] = profile_data
            except Exception as e:
                log.error(f"AssuranceProfile validation failed: {e}")
                result["profile"] = profile_data
                result["profile_validation_error"] = str(e)
            return result

        # --- Step 2: Proof backend build ---
        build_results = {}
        for label, output in formalizer_outputs.items():
            if output is None:
                continue
            code = output.get("proof_source", "")
            session = output.get("session_name", "Default")
            theory = output.get("module_name", f"Module_{label}")

            session_dir = f"{self.config.data_dir}/theories/{claim.claim_id}/{label}"
            self.proof_client.prepare_theory_session(
                session_dir=session_dir,
                session_name=session,
                theory_name=theory,
                theory_body=code,
                theorem_statement=str(
                    output.get("theorem_statement")
                    or output.get("back_translation")
                    or "True"
                ),
                subject_id=canonical_artifact_id(claim.claim_id),
            )
            br = self.proof_client.build_session(
                session_name=session,
                session_dir=session_dir,
                target_theory=theory,
                target_theorem=str(
                    output.get("primary_target", f"target_{label.lower()}")
                ),
                subject_id=canonical_artifact_id(claim.claim_id),
            )
            build_results[label] = {
                "success": br.success,
                "stdout": br.stdout[:3000],
                "stderr": br.stderr[:3000],
                "sorry_count": br.sorry_count,
                "oops_count": br.oops_count,
                "sorry_locations": br.sorry_locations,
                "theorems": br.theorems,
                "definitions": br.definitions,
                "locales": br.locales,
                "session_fingerprint": br.session_fingerprint,
            }

        result["build_results"] = build_results

        # --- Step 3: Verifier interprets results ---
        verifier_results = {}
        for label, output in formalizer_outputs.items():
            if output is None:
                continue
            br = build_results.get(label, {})
            v_ctx = {
                "claim_id": claim.claim_id,
                "formalizer_label": label,
                "proof_language": output.get("proof_language", proof_backend_family(self.config.proof_protocol.target_backend_id)),
                "proof_source": output.get("proof_source", ""),
                "build_output": f"STDOUT:\n{br.get('stdout','')}\nSTDERR:\n{br.get('stderr','')}",
                "dependency_data": None,
            }
            v_out = await self.verifier.run(v_ctx)
            verifier_results[label] = v_out["output"]

        result["verifier_results"] = verifier_results

        # --- Step 4: FWP-backed deterministic audit workflow ---
        (
            audit_output,
            proof_audit,
            profile_data,
            audit_workflow,
        ) = await self.run_audit_workflow(
            claim_graph,
            claim,
            workflow,
            formalizer_outputs,
            verifier_results,
        )
        self.last_audit_workflow = audit_workflow
        result["audit_workflow"] = audit_workflow.model_dump(
            mode="json",
            exclude_none=True,
        )
        result["proof_audit"] = proof_audit
        result["audit"] = audit_output

        # --- Step 5: Policy engine → Assurance Profile ---
        result["profile"] = profile_data

        return result

    # ===================================================================
    # Phase 3: Evidence Collection & Execution
    # ===================================================================

    async def phase3_evidence_and_execution(
        self,
        claim_graph: ClaimGraph,
        claim_id: str,
        phase2_result: dict,
    ) -> dict[str, Any]:
        """
        If the gate allows, run research and/or dev work,
        then recompute the assurance profile.
        """
        result: dict[str, Any] = {"claim_id": claim_id}
        profile = phase2_result.get("profile", {})
        gate = profile.get("gate", "draft")

        # Find the claim object
        claim_data = None
        for c in claim_graph.claims:
            if c.claim_id == claim_id:
                claim_data = c.model_dump(mode="json")
                break

        if not claim_data:
            result["error"] = f"Claim {claim_id} not found in graph"
            return result

        # --- Research (if gate allows) ---
        if gate in ("research_only", "dev_guarded", "certified"):
            research_ctx = {
                "claim": claim_data,
                "research_task": (
                    f"Find supporting and challenging evidence for this claim. "
                    f"Current support_status: {profile.get('support_status', 'none')}"
                ),
                "existing_evidence": None,
            }
            research_out = await self.research.run(research_ctx)
            result["research"] = research_out["output"]

        # --- Dev work (if gate allows) ---
        if gate in ("dev_guarded", "certified"):
            contract_pack = emit_contract_pack(profile)
            result["contract_pack"] = contract_pack.to_dict()
            # Build a minimal contract context
            contract_ctx = {
                "claim": claim_data,
                "assurance_profile": profile,
                "contract": contract_pack.to_dict(),
                "dev_task": "Implement based on the current Contract Pack scope.",
            }
            dev_out = await self.dev.run(contract_ctx)
            result["dev"] = dev_out["output"]

        # --- Recompute assurance profile ---
        updated_profile_model = compute_assurance_profile(
            AssuranceComputationInput(
                project_id=self.config.project_id,
                claim=claim_data,
                verifier_output=phase2_result.get("verifier_results"),
                audit_output=phase2_result.get("audit"),
                research_output=result.get("research"),
                coverage_data=None,
                claim_graph=claim_graph,
                claim_graph_ref=self._scalar_text(claim_graph.graph_id),
                existing_profile=profile,
            )
        )
        updated_profile = updated_profile_model.model_dump(mode="json", exclude_none=True)
        result["updated_profile"] = updated_profile

        try:
            self.store.save_assurance_profile(updated_profile_model)
        except Exception as e:
            log.error(f"Updated profile validation failed: {e}")
            result["profile_validation_error"] = str(e)

        return result


# ===================================================================
# Result container
# ===================================================================

class PipelineResult:
    """Collects all outputs from a pipeline run."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.claim_structuring_workflow: dict[str, Any] | None = None
        self.claim_graph: ClaimGraph | None = None
        self.phase2_results: dict[str, dict] = {}
        self.phase3_results: dict[str, dict] = {}

    def summary(self) -> dict:
        claims = []
        if self.claim_graph:
            for c in self.claim_graph.claims:
                p2 = self.phase2_results.get(c.claim_id, {})
                p3 = self.phase3_results.get(c.claim_id, {})
                profile = p3.get("updated_profile") or p2.get("profile") or {}
                claims.append({
                    "claim_id": c.claim_id,
                    "title": c.title,
                    "status": c.status.value,
                    "gate": profile.get("gate", "n/a"),
                    "formal_status": profile.get("formal_status", "n/a"),
                    "violations": [
                        a for a in profile.get("required_actions", [])
                        if a.startswith("[VIOLATION]")
                    ],
                })
        return {"project_id": self.project_id, "claims": claims}
