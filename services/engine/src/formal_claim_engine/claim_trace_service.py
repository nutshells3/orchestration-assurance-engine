"""Engine-backed claim tracing workflows used by the canonical MCP server."""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from typing import Any

from .agents.provider_adapters import prepare_completion_request
from .claim_trace_repository import ClaimTraceRepository
from .claim_trace_types import (
    Claim,
    ClaimRole,
    ClaimStatus,
    DEPENDENCY_TYPES,
    Domain,
    Gap,
    Relation,
    RelationType,
    SoundnessScore,
    Strength,
    TRACE_DEPTH_PREFIX,
    TRACE_DOMAIN_PREFIX,
    TRACE_ROLE_PREFIX,
    TRACE_STATUS_PREFIX,
    TraceProjectRecord,
    new_id,
    now,
)
from .config import PipelineConfig
from .document_ingest import (
    TraceDocumentClaim,
    TraceDocumentIngestBundle,
    TraceDocumentIngestRequest,
    TraceDocumentRelation,
    TraceSourceDocument,
    build_inline_source_document,
    extract_evaluation_evidence,
    ingest_trace_document,
    load_local_text_document,
    load_uploaded_document,
    trace_claim_to_canonical,
    trace_relation_to_canonical,
)
from .evaluation_evidence import (
    EvaluationEvidenceBundle,
    EvaluationEvidenceLink,
    EvaluationEvidenceRecord,
    build_evidence_link_id,
    evaluation_evidence_artifact_id,
    evaluation_evidence_signature,
)
from .external_reference_registry import (
    ExternalReferenceArtifactPreview,
    ExternalReferenceRegistry,
    ExternalReferenceLink,
    build_link_id,
    build_reference_record,
    derive_reference_id,
    now_utc_iso,
    pick_status,
    reference_match_keys,
    reference_registry_artifact_id,
    reference_registry_signature,
    source_document_summary,
)
from .llm_client import LLMClient, llm_client
from .store import canonical_artifact_id


DOMAIN_CONTEXT = {
    Domain.academic: "Analyze academic arguments for hidden assumptions, citation gaps, and scope creep.",
    Domain.legal: "Analyze legal arguments for authority gaps, jurisdiction mismatches, and unsupported interpretive leaps.",
    Domain.formal_proof: "Analyze formal proofs for unstated assumptions, vacuity, and proof shortcuts.",
    Domain.general: "Analyze general arguments for unsupported claims, circularity, and hidden assumptions.",
}

INGEST_SYSTEM = """You decompose documents into structured argument graphs.

{domain_context}

Return strict JSON with:
- `claims`: each claim has `id`, `title`, `statement`, `role`, `source_location`, `source_text`, `scope`, and `depth`
- `relations`: each relation has `source_id`, `target_id`, `relation_type`, `strength`, and `rationale`
"""

FORWARD_SYSTEM = """You trace an argument forward from a claim toward conclusions.

{domain_context}

Return strict JSON with:
- `trace`
- `new_hidden_assumptions`
- `gaps`
- `summary`
"""

BACKWARD_SYSTEM = """You trace an argument backward from a conclusion toward foundations.

{domain_context}

Return strict JSON with:
- `trace`
- `new_hidden_assumptions`
- `gaps`
- `foundation_completeness`
- `summary`
"""

GAPS_SYSTEM = """You analyze an argument graph for structural weaknesses.

{domain_context}

Return strict JSON with:
- `gaps`
- `structural_issues`
- `summary`
"""

ASSESS_SYSTEM = """You assess the soundness of an argument.

{domain_context}

Return strict JSON with:
- `completeness`
- `logical_validity`
- `evidential_strength`
- `transparency`
- `overall`
- `rationale`
- `strongest_points`
- `weakest_points`
"""


def parse_json(text: str) -> dict | list:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    if start >= 0:
        depth = 0
        end = start
        for index, char in enumerate(text[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        return json.loads(text[start:end])

    raise ValueError(f"Could not parse JSON from model output:\n{text[:500]}")


def _split_document_units(text: str) -> list[tuple[str, int]]:
    units: list[tuple[str, int]] = []
    running_offset = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        line_start = running_offset
        running_offset += len(raw_line) + 1
        if not line:
            continue
        for chunk in re.split(r"(?<=[.!?])\s+", line):
            sentence = chunk.strip()
            if not sentence:
                continue
            position = raw_line.find(sentence)
            start_offset = line_start + (position if position >= 0 else 0)
            units.append((sentence, start_offset))
    if units:
        return units
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return []
    return [(collapsed, 0)]


def _heuristic_role_for_sentence(
    *,
    domain: Domain,
    sentence: str,
    index: int,
    total: int,
) -> str:
    lowered = sentence.lower()
    contains_metric = bool(re.search(r"\b\d+(?:\.\d+)?\b|%|table|figure|median|mean", lowered))
    if domain == Domain.formal_proof:
        return "theorem" if index == total - 1 else "premise"
    if domain == Domain.legal:
        if re.search(r"\b(article|section|statute|clause|holding|court)\b", lowered):
            return "statute" if index == 0 else "holding"
        return "holding" if index == total - 1 else "premise"
    if domain == Domain.academic:
        if contains_metric:
            return "observation"
        return "conclusion" if index == total - 1 and total > 1 else "premise"
    if contains_metric:
        return "observation"
    return "conclusion" if index == total - 1 and total > 1 else "premise"


def _heuristic_document_extraction(
    *,
    project: TraceProjectRecord,
    text: str,
) -> dict[str, Any]:
    units = _split_document_units(text)
    if not units:
        units = [("Imported document contained no extractable text.", 0)]
    claims: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    max_units = min(len(units), 12)
    for index, (sentence, start_offset) in enumerate(units[:max_units]):
        title_words = sentence.split()[:8]
        claims.append(
            {
                "id": f"heuristic_{index + 1}",
                "title": " ".join(title_words)[:96] or f"claim {index + 1}",
                "statement": sentence,
                "role": _heuristic_role_for_sentence(
                    domain=project.domain,
                    sentence=sentence,
                    index=index,
                    total=max_units,
                ),
                "status": "stated",
                "source_location": f"offset:{start_offset}",
                "source_text": sentence,
                "scope": project.description or project.domain.value,
                "depth": index,
                "notes": [
                    "Heuristic extraction fallback used because the configured model path was unavailable.",
                ],
            }
        )
        if index == 0:
            continue
        relations.append(
            {
                "source_id": f"heuristic_{index}",
                "target_id": f"heuristic_{index + 1}",
                "relation_type": "supports",
                "strength": "unknown",
                "rationale": "Deterministic sentence-order fallback support chain.",
            }
        )
    return {
        "claims": claims,
        "relations": relations,
        "fallback": {
            "used": True,
            "reason": "llm_unavailable_or_invalid_output",
            "unit_count": max_units,
        },
    }


def _extract_tag(tags: list[str] | None, prefix: str, default: str) -> str:
    for tag in tags or []:
        if tag.startswith(prefix):
            return tag[len(prefix) :]
    return default


def _normalize_trace_depth(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return 0
        if normalized.isdigit():
            return max(0, int(normalized))
        labels = {
            "foundational": 0,
            "foundation": 0,
            "root": 0,
            "base": 0,
            "premise": 0,
            "supporting": 1,
            "intermediate": 1,
            "mid": 1,
            "derived": 2,
            "conclusion": 2,
            "holding": 2,
            "top": 2,
        }
        return labels.get(normalized, 0)
    return 0


def _normalize_trace_role(value: Any) -> str:
    if isinstance(value, ClaimRole):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "fact": ClaimRole.premise.value,
            "background": ClaimRole.premise.value,
            "issue": ClaimRole.holding.value,
            "rule": ClaimRole.statute.value,
            "reasoning": ClaimRole.interpretation.value,
            "analysis": ClaimRole.interpretation.value,
            "application": ClaimRole.interpretation.value,
            "standard": ClaimRole.precedent.value,
            "principle": ClaimRole.precedent.value,
            "doctrine": ClaimRole.precedent.value,
        }
        if normalized in ClaimRole._value2member_map_:
            return normalized
        return aliases.get(normalized, ClaimRole.premise.value)
    return ClaimRole.premise.value


def _normalize_trace_status(value: Any) -> str:
    if isinstance(value, ClaimStatus):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "asserted": ClaimStatus.stated.value,
            "grounded": ClaimStatus.supported.value,
            "contested": ClaimStatus.challenged.value,
            "disproven": ClaimStatus.refuted.value,
        }
        if normalized in ClaimStatus._value2member_map_:
            return normalized
        return aliases.get(normalized, ClaimStatus.stated.value)
    return ClaimStatus.stated.value


def _normalize_trace_relation_type(value: Any) -> str:
    if isinstance(value, RelationType):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return RelationType.derives.value
        aliases = {
            "elaborates": RelationType.supports.value,
            "elaboration": RelationType.supports.value,
            "explains": RelationType.supports.value,
            "grounds": RelationType.supports.value,
            "depends_on": RelationType.requires.value,
            "depends-on": RelationType.requires.value,
            "depends": RelationType.requires.value,
            "implies": RelationType.derives.value,
            "shows": RelationType.supports.value,
            "extends": RelationType.generalizes.value,
            "narrows": RelationType.specializes.value,
        }
        if normalized in RelationType._value2member_map_:
            return normalized
        return aliases.get(normalized, RelationType.supports.value)
    return RelationType.supports.value


def _normalize_trace_strength(value: Any) -> str:
    if isinstance(value, Strength):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "strong": Strength.deductive.value,
            "medium": Strength.inductive.value,
            "textual": Strength.authoritative.value,
            "precedential": Strength.authoritative.value,
            "logical": Strength.deductive.value,
        }
        if normalized in Strength._value2member_map_:
            return normalized
        return aliases.get(normalized, Strength.unknown.value)
    return Strength.unknown.value


def _claim_ids(graph_data: dict[str, Any] | None) -> list[str]:
    if not graph_data:
        return []
    return [claim["claim_id"] for claim in graph_data.get("claims", [])]


def _trace_children(relations: list[Relation], claim_id: str) -> list[str]:
    return [
        relation.target_id
        for relation in relations
        if relation.source_id == claim_id and relation.relation_type in DEPENDENCY_TYPES
    ]


def _trace_parents(relations: list[Relation], claim_id: str) -> list[str]:
    return [
        relation.source_id
        for relation in relations
        if relation.target_id == claim_id and relation.relation_type in DEPENDENCY_TYPES
    ]


def _forward_trace(
    relations: list[Relation], claim_id: str, visited: set[str] | None = None
) -> list[str]:
    visited = visited or set()
    if claim_id in visited:
        return []
    visited.add(claim_id)
    children = _trace_children(relations, claim_id)
    result = list(children)
    for child in children:
        result.extend(_forward_trace(relations, child, visited))
    return result


def _backward_trace(
    relations: list[Relation], claim_id: str, visited: set[str] | None = None
) -> list[str]:
    visited = visited or set()
    if claim_id in visited:
        return []
    visited.add(claim_id)
    parents = _trace_parents(relations, claim_id)
    result = list(parents)
    for parent in parents:
        result.extend(_backward_trace(relations, parent, visited))
    return result


def _dependency_targets(relations: list[Relation]) -> set[str]:
    return {
        relation.target_id
        for relation in relations
        if relation.relation_type in DEPENDENCY_TYPES
    }


def _dependency_sources(relations: list[Relation]) -> set[str]:
    return {
        relation.source_id
        for relation in relations
        if relation.relation_type in DEPENDENCY_TYPES
    }


def _roots(relations: list[Relation], claims: list[Claim]) -> list[Claim]:
    targets = _dependency_targets(relations)
    return [claim for claim in claims if claim.id not in targets]


def _leaves(relations: list[Relation], claims: list[Claim]) -> list[Claim]:
    sources = _dependency_sources(relations)
    return [claim for claim in claims if claim.id not in sources]


def _role_to_claim_class(role: ClaimRole) -> str:
    if role in {
        ClaimRole.axiom,
        ClaimRole.premise,
        ClaimRole.hypothesis,
        ClaimRole.hidden_assumption,
        ClaimRole.statute,
        ClaimRole.precedent,
    }:
        return "assumption"
    if role in {ClaimRole.theorem, ClaimRole.conclusion, ClaimRole.holding}:
        return "core_claim"
    if role == ClaimRole.observation:
        return "metric"
    return "enabling_claim"


def _role_to_claim_kind(role: ClaimRole) -> str:
    if role == ClaimRole.definition:
        return "definition_candidate"
    if role in {
        ClaimRole.theorem,
        ClaimRole.lemma,
        ClaimRole.corollary,
        ClaimRole.conclusion,
        ClaimRole.holding,
    }:
        return "theorem_candidate"
    if role == ClaimRole.observation:
        return "evaluation_criterion"
    return "hypothesis"


def _trace_status_to_canonical(status: ClaimStatus) -> str:
    if status == ClaimStatus.refuted:
        return "rejected"
    if status in {ClaimStatus.challenged, ClaimStatus.circular, ClaimStatus.unsupported}:
        return "blocked"
    if status == ClaimStatus.supported:
        return "formalizing"
    return "candidate"


def _canonical_to_trace_status(claim_data: dict[str, Any]) -> ClaimStatus:
    explicit = _extract_tag(claim_data.get("tags"), TRACE_STATUS_PREFIX, "")
    if explicit:
        return ClaimStatus(explicit)

    status = claim_data.get("status", "candidate")
    if status == "rejected":
        return ClaimStatus.refuted
    if status == "blocked":
        return ClaimStatus.challenged
    if status == "formalizing":
        return ClaimStatus.supported
    return ClaimStatus.stated


def _default_graph_policy(domain: Domain | str) -> dict[str, Any]:
    normalized = str(getattr(domain, "value", domain) or "general")
    return {
        "default_assumption_carrier": "locale"
        if normalized == Domain.formal_proof.value
        else "premise",
        "allow_global_axioms": False,
        "require_backtranslation_review": True,
        "require_dual_formalization_for_core_claims": (
            normalized == Domain.formal_proof.value
        ),
    }


def _canonical_to_trace_role(claim_data: dict[str, Any]) -> ClaimRole:
    explicit = _extract_tag(claim_data.get("tags"), TRACE_ROLE_PREFIX, "")
    if explicit:
        return ClaimRole(explicit)

    claim_class = claim_data.get("claim_class")
    claim_kind = claim_data.get("claim_kind")
    if claim_kind == "definition_candidate":
        return ClaimRole.definition
    if claim_class == "core_claim":
        return ClaimRole.conclusion
    if claim_class == "metric":
        return ClaimRole.observation
    if claim_class == "assumption":
        return ClaimRole.premise
    return ClaimRole.lemma


def _relation_to_canonical(relation: Relation) -> dict[str, Any]:
    rationale = relation.rationale or None
    if relation.gap_note:
        rationale = (
            f"{rationale} | gap={relation.gap_note}"
            if rationale
            else f"gap={relation.gap_note}"
        )
    mapped = trace_relation_to_canonical(
        {
            "relation_id": relation.id,
            "source_id": relation.source_id,
            "target_id": relation.target_id,
            "relation_type": relation.relation_type.value,
            "strength": relation.strength.value,
            "rationale": rationale,
        }
    )
    mapped["status"] = "active"
    return mapped


def _claim_to_canonical(project: TraceProjectRecord, claim: Claim) -> dict[str, Any]:
    mapped = trace_claim_to_canonical(
        project_id=project.id,
        domain=project.domain.value,
        claim={
            "id": claim.id,
            "title": claim.title,
            "statement": claim.statement,
            "role": claim.role.value,
            "status": claim.status.value,
            "source_location": claim.source_location,
            "source_text": claim.source_text,
            "scope": claim.scope,
            "notes": claim.notes,
            "depth": claim.depth,
        },
        canonical_claim_id=claim.id,
        default_source_ref=f"project:{project.id}",
    )
    mapped["provenance"]["created_by_role"] = "system"
    return mapped


def _claim_from_canonical(project: TraceProjectRecord, claim_data: dict[str, Any]) -> Claim:
    anchor = ((claim_data.get("provenance") or {}).get("source_anchors") or [{}])[0]
    depth = int(_extract_tag(claim_data.get("tags"), TRACE_DEPTH_PREFIX, "0"))
    source_location = anchor.get("source_ref")
    if source_location and source_location.startswith("project:"):
        source_location = None

    scope_value = (claim_data.get("scope") or {}).get("domain")
    if scope_value == project.domain.value:
        scope_value = None

    return Claim(
        id=claim_data["claim_id"],
        title=claim_data["title"],
        statement=claim_data["nl_statement"],
        role=_canonical_to_trace_role(claim_data),
        status=_canonical_to_trace_status(claim_data),
        domain=project.domain,
        source_location=source_location,
        source_text=anchor.get("excerpt"),
        scope=scope_value,
        notes=claim_data.get("notes") or [],
        depth=depth,
    )


def _empty_graph_data(project: TraceProjectRecord) -> dict[str, Any]:
    timestamp = now().isoformat()
    return {
        "schema_version": "1.0.0",
        "graph_id": f"tracer.{project.id}",
        "project_id": project.id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "description": project.description or None,
        "claims": [],
        "relations": [],
        "root_claim_ids": [],
        "graph_policy": _default_graph_policy(project.domain),
    }


class ClaimTraceService:
    """Engine-owned claim tracing service consumed by the MCP facade."""

    def __init__(
        self,
        *,
        config: PipelineConfig | None = None,
        llm: LLMClient | None = None,
        data_dir: str | None = None,
    ):
        self.config = config or PipelineConfig()
        self.llm = llm or llm_client
        self.repository = ClaimTraceRepository(
            data_dir or os.environ.get("TRACER_DATA_DIR", "./tracer_data")
        )

    async def _llm_call(self, system: str, user: str) -> str:
        slot = self.config.model_routing.get(
            "claim_tracer", self.config.model_routing["claim_graph_agent"]
        )
        model_override = os.environ.get("TRACER_MODEL")
        if model_override:
            slot = replace(slot, model=model_override)
        request = prepare_completion_request(
            slot=slot,
            system=system,
            messages=[{"role": "user", "content": user}],
            expect_json=True,
        )
        response = await self.llm.complete(
            slot=slot,
            system=request.system,
            messages=request.messages,
            response_format=request.response_format,
        )
        return response.text

    def _load_project(
        self, project_id: str
    ) -> tuple[TraceProjectRecord, dict[str, Any] | None]:
        project, graph_data = self.repository.load(project_id)
        if project is None:
            raise ValueError(f"Project '{project_id}' not found. Use create_project first.")
        return project, graph_data

    def _claims_from_graph(
        self, project: TraceProjectRecord, graph_data: dict[str, Any] | None
    ) -> list[Claim]:
        if not graph_data:
            return []
        return [
            _claim_from_canonical(project, claim_data)
            for claim_data in graph_data.get("claims", [])
        ]

    def _claims_index(
        self, project: TraceProjectRecord, graph_data: dict[str, Any] | None
    ) -> dict[str, Claim]:
        return {claim.id: claim for claim in self._claims_from_graph(project, graph_data)}

    def _sync_graph(
        self, project: TraceProjectRecord, graph_data: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if graph_data is None:
            return None

        claim_ids = _claim_ids(graph_data)
        graph_data["project_id"] = project.id
        graph_data["updated_at"] = now().isoformat()
        graph_data["description"] = project.description or None
        graph_data["relations"] = [
            _relation_to_canonical(relation)
            for relation in project.relations
            if relation.source_id in claim_ids and relation.target_id in claim_ids
        ]
        graph_data["root_claim_ids"] = sorted(
            set(claim_ids).difference(_dependency_targets(project.relations))
        ) or None
        return graph_data

    def _save_project(
        self, project: TraceProjectRecord, graph_data: dict[str, Any] | None
    ) -> None:
        self.repository.save(project, self._sync_graph(project, graph_data))

    def _append_claim(
        self, project: TraceProjectRecord, graph_data: dict[str, Any] | None, claim: Claim
    ) -> dict[str, Any]:
        graph_data = graph_data or _empty_graph_data(project)
        graph_data["claims"] = [*graph_data.get("claims", []), _claim_to_canonical(project, claim)]
        return graph_data

    def _update_claim(
        self,
        project: TraceProjectRecord,
        graph_data: dict[str, Any] | None,
        claim_id: str,
        *,
        status: ClaimStatus | None = None,
        notes_append: list[str] | None = None,
    ) -> dict[str, Any]:
        if graph_data is None:
            raise ValueError(f"Claim {claim_id} not found")

        updated_claims = []
        found = False
        for claim_data in graph_data.get("claims", []):
            if claim_data["claim_id"] != claim_id:
                updated_claims.append(claim_data)
                continue
            claim = _claim_from_canonical(project, claim_data)
            if status is not None:
                claim.status = status
            if notes_append:
                claim.notes.extend(notes_append)
            updated_claims.append(_claim_to_canonical(project, claim))
            found = True

        if not found:
            raise ValueError(f"Claim {claim_id} not found")

        graph_data["claims"] = updated_claims
        return graph_data

    def create_project(
        self, name: str, domain: str, description: str = ""
    ) -> TraceProjectRecord:
        project = TraceProjectRecord(
            name=name,
            domain=Domain(domain),
            description=description,
        )
        self.repository.save(project, None)
        return project

    def list_projects(self) -> list[dict[str, Any]]:
        return self.repository.list_projects()

    def get_graph(self, project_id: str, depth_filter: int = -1) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_from_graph(project, graph_data)
        if depth_filter >= 0:
            claims = [claim for claim in claims if claim.depth == depth_filter]
        return {
            "project": project.name,
            "domain": project.domain.value,
            "total_claims": len(self._claims_from_graph(project, graph_data)),
            "showing": len(claims),
            "claims": [claim.model_dump(exclude_none=True) for claim in claims],
            "relations": [
                relation.model_dump(exclude_none=True) for relation in project.relations
            ],
            "gaps": [gap.model_dump(exclude_none=True) for gap in project.gaps],
        }

    def get_claim(self, project_id: str, claim_id: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_index(project, graph_data)
        claim = claims.get(claim_id)
        if claim is None:
            return {"error": f"Claim {claim_id} not found"}

        return {
            "claim": claim.model_dump(exclude_none=True),
            "parents": [
                claims[parent_id].model_dump(exclude_none=True)
                for parent_id in _trace_parents(project.relations, claim_id)
                if parent_id in claims
            ],
            "children": [
                claims[child_id].model_dump(exclude_none=True)
                for child_id in _trace_children(project.relations, claim_id)
                if child_id in claims
            ],
            "incoming_relations": [
                relation.model_dump(exclude_none=True)
                for relation in project.relations
                if relation.target_id == claim_id
            ],
            "outgoing_relations": [
                relation.model_dump(exclude_none=True)
                for relation in project.relations
                if relation.source_id == claim_id
            ],
            "related_gaps": [
                gap.model_dump(exclude_none=True)
                for gap in project.gaps
                if claim_id in gap.affected_claim_ids
            ],
        }

    def get_axioms(self, project_id: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_from_graph(project, graph_data)
        roots = _roots(project.relations, claims)
        axiom_roles = {
            ClaimRole.axiom,
            ClaimRole.premise,
            ClaimRole.statute,
            ClaimRole.precedent,
            ClaimRole.definition,
        }
        axioms = [claim for claim in roots if claim.role in axiom_roles]
        other_roots = [claim for claim in roots if claim.role not in axiom_roles]
        return {
            "axioms": [claim.model_dump(exclude_none=True) for claim in axioms],
            "other_roots": [
                claim.model_dump(exclude_none=True) for claim in other_roots
            ],
            "note": "other_roots are non-axiom claims with no incoming support and may indicate gaps",
        }

    def get_conclusions(self, project_id: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_from_graph(project, graph_data)
        leaves = _leaves(project.relations, claims)
        return {
            "conclusions": [claim.model_dump(exclude_none=True) for claim in leaves],
            "count": len(leaves),
        }

    def _build_graph_context(
        self,
        project: TraceProjectRecord,
        graph_data: dict[str, Any] | None,
        claim_ids: list[str],
    ) -> str:
        claims = self._claims_index(project, graph_data)
        relevant_claims = [
            claims[claim_id].model_dump(exclude_none=True)
            for claim_id in claim_ids
            if claim_id in claims
        ]
        relevant_relations = [
            relation.model_dump(exclude_none=True)
            for relation in project.relations
            if relation.source_id in claim_ids or relation.target_id in claim_ids
        ]
        return json.dumps(
            {"claims": relevant_claims, "relations": relevant_relations},
            indent=2,
            default=str,
        )

    def _build_document_ingest_request(
        self,
        *,
        project: TraceProjectRecord,
        graph_data: dict[str, Any],
        extraction_data: dict[str, Any],
        source_document: TraceSourceDocument,
        document_text: str,
    ) -> TraceDocumentIngestRequest:
        return TraceDocumentIngestRequest(
            project_id=project.id,
            domain=project.domain.value,
            document_ref=source_document.document_ref,
            description=project.description or "",
            source_document=source_document,
            document_text=document_text,
            existing_claim_ids=_claim_ids(graph_data),
            claims=[
                TraceDocumentClaim.model_validate(
                    {
                        "id": raw_claim.get("id") or new_id("raw"),
                        "title": raw_claim.get("title", "untitled"),
                        "statement": raw_claim.get("statement", ""),
                        "role": _normalize_trace_role(raw_claim.get("role", "premise")),
                        "status": _normalize_trace_status(raw_claim.get("status", "stated")),
                        "source_location": raw_claim.get("source_location"),
                        "source_text": raw_claim.get("source_text"),
                        "scope": raw_claim.get("scope"),
                        "depth": _normalize_trace_depth(raw_claim.get("depth", 0)),
                        "notes": raw_claim.get("notes") or [],
                        "span_start": raw_claim.get("span_start"),
                        "span_end": raw_claim.get("span_end"),
                    }
                )
                for raw_claim in extraction_data.get("claims", [])
            ],
            relations=[
                TraceDocumentRelation.model_validate(
                    {
                        "source_id": raw_relation["source_id"],
                        "target_id": raw_relation["target_id"],
                        "relation_type": _normalize_trace_relation_type(
                            raw_relation.get("relation_type", "derives")
                        ),
                        "strength": _normalize_trace_strength(
                            raw_relation.get("strength", "unknown")
                        ),
                        "rationale": raw_relation.get("rationale"),
                    }
                )
                for raw_relation in extraction_data.get("relations", [])
            ],
        )

    def _persist_source_mapping_bundle(
        self,
        *,
        project: TraceProjectRecord,
        bundle_data: dict[str, Any],
        mapping_report: dict[str, Any],
    ) -> dict[str, Any]:
        source_document = dict(bundle_data.get("source_document") or {})
        document_id = str(
            source_document.get("document_id")
            or bundle_data.get("document_ref")
            or f"document.ingest.{project.id}"
        )
        return self.repository.artifact_store.save_json_artifact(
            "source_mapping_bundles",
            document_id,
            bundle_data,
            actor="document_ingest",
            reason="source mapping ingest",
            metadata={
                "project_id": project.id,
                "document_ref": str(
                    source_document.get("document_ref") or bundle_data.get("document_ref") or document_id
                ),
                "source_kind": str(source_document.get("source_kind") or "document"),
                "text_sha256": str(source_document.get("text_sha256") or ""),
                "imported_claim_count": int(mapping_report.get("imported_claim_count") or 0),
                "unresolved_reference_count": int(
                    mapping_report.get("unresolved_reference_count") or 0
                ),
            },
        )

    def _evaluation_evidence_preview(self, evidence: dict[str, Any]) -> dict[str, Any]:
        evidence_id = canonical_artifact_id(evidence.get("evidence_id") or "")
        metric_name = str(evidence.get("metric_name") or evidence.get("title") or evidence_id)
        baseline_text = str(evidence.get("baseline_value_text") or "") or None
        reported_text = str(evidence.get("reported_value_text") or "") or None
        unit = str(evidence.get("unit") or "") or None
        if baseline_text and reported_text:
            summary = f"{metric_name}: {baseline_text} -> {reported_text}"
            if unit:
                summary = f"{summary} {unit}"
        else:
            summary = str(evidence.get("summary") or metric_name)
        return ExternalReferenceArtifactPreview(
            artifact_kind="evaluation_evidence",
            artifact_id=evidence_id,
            claim_id=(list(evidence.get("linked_claim_ids") or [None]) or [None])[0],
            title=str(evidence.get("title") or metric_name or evidence_id),
            summary=summary,
            metadata={
                "metric_name": str(evidence.get("metric_name") or ""),
                "comparison_target": str(evidence.get("comparison_target") or ""),
                "dataset": str(evidence.get("dataset") or ""),
                "direction": str(evidence.get("direction") or ""),
                "status": str(evidence.get("status") or ""),
            },
        ).model_dump(mode="json", exclude_none=True)

    def _build_evaluation_evidence_bundle_payload(
        self,
        *,
        project: TraceProjectRecord,
        bundle_data: dict[str, Any],
        source_mapping_ref: dict[str, Any],
    ) -> dict[str, Any]:
        ingest_bundle = TraceDocumentIngestBundle.model_validate(bundle_data)
        extracted_items = extract_evaluation_evidence(ingest_bundle)
        source_document = dict(bundle_data.get("source_document") or {})
        document_id = str(
            source_document.get("document_id")
            or source_mapping_ref.get("artifact_id")
            or f"document.ingest.{project.id}"
        )
        source_summary = source_document_summary(
            source_document,
            current_revision_id=str(source_mapping_ref.get("revision_id") or ""),
        )
        items: list[dict[str, Any]] = []
        links: list[dict[str, Any]] = []
        status_counts: dict[str, int] = {}
        for item in extracted_items:
            item_data = item.model_dump(mode="json", exclude_none=True)
            evidence_id = canonical_artifact_id(item_data.get("evidence_id") or "")
            claim_id = canonical_artifact_id(item_data.get("claim_candidate_id") or "")
            anchor = dict(item_data.get("citation_anchor") or {})
            reference_id = canonical_artifact_id(anchor.get("anchor_id") or "")
            status = str(anchor.get("status") or "resolved")
            status_counts[status] = status_counts.get(status, 0) + 1
            record = EvaluationEvidenceRecord(
                evidence_id=evidence_id,
                status=status,
                source_document=source_summary,
                source_mapping_ref=source_mapping_ref,
                citation_anchor=anchor,
                title=str(item_data.get("title") or evidence_id),
                summary=str(item_data.get("summary") or ""),
                evidence_kind=str(item_data.get("evidence_kind") or "evaluation_measurement"),
                dataset=item_data.get("dataset"),
                metric_name=item_data.get("metric_name"),
                split=item_data.get("split"),
                comparison_target=item_data.get("comparison_target"),
                baseline_value=item_data.get("baseline_value"),
                baseline_value_text=item_data.get("baseline_value_text"),
                reported_value=item_data.get("reported_value"),
                reported_value_text=item_data.get("reported_value_text"),
                delta_value=item_data.get("delta_value"),
                unit=item_data.get("unit"),
                direction=item_data.get("direction"),
                table_figure_anchor=item_data.get("table_figure_anchor"),
                cited_table_label=item_data.get("cited_table_label"),
                cited_figure_label=item_data.get("cited_figure_label"),
                confidence_interval=dict(item_data.get("confidence_interval") or {}),
                provenance=dict(item_data.get("provenance") or {}),
                uncertainty=dict(item_data.get("uncertainty") or {}),
                linked_claim_ids=[claim_id] if claim_id else [],
                linked_reference_ids=[reference_id] if reference_id else [],
            ).model_dump(mode="json", exclude_none=True)
            items.append(record)
            preview = self._evaluation_evidence_preview(record)
            if claim_id:
                links.append(
                    EvaluationEvidenceLink(
                        link_id=build_evidence_link_id(
                            evidence_id,
                            "claim",
                            claim_id,
                            "evidence_for_claim",
                        ),
                        evidence_id=evidence_id,
                        status=status,
                        subject_kind="claim",
                        subject_id=claim_id,
                        relation="evidence_for_claim",
                        claim_id=claim_id,
                        source_document=source_summary,
                        artifact_preview=preview,
                    ).model_dump(mode="json", exclude_none=True)
                )
            if reference_id:
                links.append(
                    EvaluationEvidenceLink(
                        link_id=build_evidence_link_id(
                            evidence_id,
                            "reference",
                            reference_id,
                            "evidence_for_reference",
                        ),
                        evidence_id=evidence_id,
                        status=status,
                        subject_kind="reference",
                        subject_id=reference_id,
                        relation="evidence_for_reference",
                        claim_id=claim_id or None,
                        reference_id=reference_id,
                        source_document=source_summary,
                        artifact_preview=preview,
                    ).model_dump(mode="json", exclude_none=True)
                )
        return EvaluationEvidenceBundle(
            bundle_id=evaluation_evidence_artifact_id(document_id),
            project_id=project.id,
            document_id=document_id,
            generated_at=now_utc_iso(),
            evidence_count=len(items),
            link_count=len(links),
            status_counts=status_counts,
            items=items,
            links=links,
        ).model_dump(mode="json", exclude_none=True)

    def _persist_evaluation_evidence_bundle(
        self,
        *,
        project: TraceProjectRecord,
        bundle_data: dict[str, Any],
        source_mapping_ref: dict[str, Any],
    ) -> dict[str, Any]:
        source_document = dict(bundle_data.get("source_document") or {})
        document_id = str(
            source_document.get("document_id")
            or source_mapping_ref.get("artifact_id")
            or f"document.ingest.{project.id}"
        )
        payload = self._build_evaluation_evidence_bundle_payload(
            project=project,
            bundle_data=bundle_data,
            source_mapping_ref=source_mapping_ref,
        )
        latest = self.repository.artifact_store.get_latest_artifact(
            "evaluation_evidence_bundles",
            document_id,
        )
        if latest is not None and evaluation_evidence_signature(
            dict(latest.get("payload") or {})
        ) == evaluation_evidence_signature(payload):
            current_revision_id = latest.get("current_revision_id")
            return {
                "artifact_id": document_id,
                "revision_id": current_revision_id,
                "payload": dict(latest.get("payload") or {}),
            }
        saved = self.repository.artifact_store.save_json_artifact(
            "evaluation_evidence_bundles",
            document_id,
            payload,
            actor="evaluation_evidence",
            reason="extract evaluation evidence",
            metadata={
                "project_id": project.id,
                "document_ref": str(
                    source_document.get("document_ref") or document_id
                ),
                "evidence_count": int(payload.get("evidence_count") or 0),
                "link_count": int(payload.get("link_count") or 0),
            },
        )
        return {
            "artifact_id": saved["artifact_id"],
            "revision_id": saved["revision_id"],
            "payload": payload,
        }

    def _apply_document_ingest(
        self,
        *,
        project: TraceProjectRecord,
        graph_data: dict[str, Any] | None,
        request: TraceDocumentIngestRequest,
    ) -> dict[str, Any]:
        graph_data = graph_data or _empty_graph_data(project)
        ingest_result = ingest_trace_document(request)
        bundle = ingest_result.bundle
        bundle_data = bundle.model_dump(mode="json", exclude_none=True)
        mapping_report = bundle.mapping_report.model_dump(mode="json", exclude_none=True)
        source_to_candidate = {
            item["source_claim_id"]: item["proposed_claim_id"]
            for item in mapping_report["claim_mappings"]
        }
        graph_data.setdefault("graph_policy", _default_graph_policy(project.domain))
        graph_data["claims"] = [
            *graph_data.get("claims", []),
            *bundle_data["claim_candidates"],
        ]
        added_claim_ids = [
            str(candidate["claim_id"]) for candidate in bundle_data["claim_candidates"]
        ]

        for raw_relation in request.relations:
            if (
                raw_relation.source_id not in source_to_candidate
                or raw_relation.target_id not in source_to_candidate
            ):
                continue
            project.relations.append(
                Relation(
                    source_id=source_to_candidate[raw_relation.source_id],
                    target_id=source_to_candidate[raw_relation.target_id],
                    relation_type=RelationType(raw_relation.relation_type),
                    strength=Strength(raw_relation.strength),
                    rationale=raw_relation.rationale,
                )
            )

        self._save_project(project, graph_data)
        source_mapping_ref = self._persist_source_mapping_bundle(
            project=project,
            bundle_data=bundle_data,
            mapping_report=mapping_report,
        )
        evaluation_evidence_ref = self._persist_evaluation_evidence_bundle(
            project=project,
            bundle_data=bundle_data,
            source_mapping_ref={
                "artifact_kind": "source_mapping_bundle",
                "artifact_id": source_mapping_ref["artifact_id"],
                "revision_id": source_mapping_ref["revision_id"],
                "project_id": project.id,
                "uri": f"source-mapping://{project.id}/{source_mapping_ref['artifact_id']}",
            },
        )
        return {
            "claims_added": len(added_claim_ids),
            "relations_added": len(mapping_report["relation_mappings"]),
            "claim_ids": added_claim_ids,
            "claim_graph_id": project.claim_graph_id or graph_data["graph_id"],
            "mapping_report": mapping_report,
            "ingest_bundle": bundle_data,
            "unresolved_references": bundle_data["unresolved_references"],
            "evidence_items_added": len(bundle_data["evidence_items"]),
            "source_document": bundle_data.get("source_document", {}),
            "source_mapping_ref": {
                "artifact_kind": "source_mapping_bundle",
                "artifact_id": source_mapping_ref["artifact_id"],
                "revision_id": source_mapping_ref["revision_id"],
                "project_id": project.id,
                "uri": f"source-mapping://{project.id}/{source_mapping_ref['artifact_id']}",
            },
            "evaluation_evidence_added": int(
                (evaluation_evidence_ref.get("payload") or {}).get("evidence_count") or 0
            ),
            "evaluation_evidence_ref": {
                "artifact_kind": "evaluation_evidence_bundle",
                "artifact_id": evaluation_evidence_ref["artifact_id"],
                "revision_id": evaluation_evidence_ref["revision_id"],
                "project_id": project.id,
                "uri": f"evaluation-evidence://{project.id}/{evaluation_evidence_ref['artifact_id']}",
            },
        }

    async def _extract_document_request(
        self,
        *,
        project: TraceProjectRecord,
        graph_data: dict[str, Any] | None,
        source_document: TraceSourceDocument,
        document_text: str,
    ) -> TraceDocumentIngestRequest:
        system = INGEST_SYSTEM.format(
            domain_context=DOMAIN_CONTEXT.get(project.domain, "")
        )
        fallback_reason = None
        try:
            raw = await self._llm_call(
                system,
                "## Source document metadata\n"
                f"{json.dumps(source_document.model_dump(mode='json', exclude_none=True), indent=2)}\n\n"
                f"## Document to analyze\n\n{document_text[:30000]}",
            )
            extraction_data = parse_json(raw)
            if not isinstance(extraction_data, dict):
                raise ValueError("Document extractors must return a JSON object.")
        except Exception as exc:
            fallback_reason = str(exc)
            extraction_data = _heuristic_document_extraction(
                project=project,
                text=document_text,
            )

        request = self._build_document_ingest_request(
            project=project,
            graph_data=graph_data or _empty_graph_data(project),
            extraction_data=extraction_data,
            source_document=source_document,
            document_text=document_text,
        )
        if fallback_reason:
            for claim in request.claims:
                claim.notes.append(f"fallback_reason:{fallback_reason[:240]}")
        return request

    async def ingest_document(self, project_id: str, text: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        source_document = build_inline_source_document(
            project.id,
            text,
            label=f"{project.name} inline ingest",
        )
        request = await self._extract_document_request(
            project=project,
            graph_data=graph_data,
            source_document=source_document,
            document_text=text,
        )
        return self._apply_document_ingest(
            project=project,
            graph_data=graph_data,
            request=request,
        )

    async def import_local_document(self, project_id: str, path: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        source_document, document_text = load_local_text_document(project.id, path)
        request = await self._extract_document_request(
            project=project,
            graph_data=graph_data,
            source_document=source_document,
            document_text=document_text,
        )
        return self._apply_document_ingest(
            project=project,
            graph_data=graph_data,
            request=request,
        )

    async def import_uploaded_document(
        self,
        project_id: str,
        *,
        file_name: str,
        raw_bytes: bytes,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        source_document, document_text = load_uploaded_document(
            project.id,
            file_name=file_name,
            raw_bytes=raw_bytes,
            media_type=media_type,
        )
        request = await self._extract_document_request(
            project=project,
            graph_data=graph_data,
            source_document=source_document,
            document_text=document_text,
        )
        return self._apply_document_ingest(
            project=project,
            graph_data=graph_data,
            request=request,
        )

    def list_source_documents(self, project_id: str) -> list[dict[str, Any]]:
        project, _ = self._load_project(project_id)
        records = self.repository.artifact_store.list_latest_artifacts(
            "source_mapping_bundles",
            project_id=project.id,
        )
        documents: list[dict[str, Any]] = []
        for record in records:
            payload = dict(record.get("payload") or {})
            source_document = dict(payload.get("source_document") or {})
            mapping_report = dict(payload.get("mapping_report") or {})
            documents.append(
                {
                    "document_id": str(source_document.get("document_id") or record["artifact_id"]),
                    "document_ref": str(source_document.get("document_ref") or ""),
                    "source_kind": str(source_document.get("source_kind") or "document"),
                    "title": str(source_document.get("title") or source_document.get("display_name") or record["artifact_id"]),
                    "display_name": str(source_document.get("display_name") or source_document.get("title") or record["artifact_id"]),
                    "origin_path": source_document.get("origin_path"),
                    "canonical_path": source_document.get("canonical_path"),
                    "text_sha256": str(source_document.get("text_sha256") or ""),
                    "imported_at": str(source_document.get("imported_at") or record["updated_at"]),
                    "current_revision_id": record["current_revision_id"],
                    "imported_claim_count": int(mapping_report.get("imported_claim_count") or 0),
                    "imported_relation_count": int(mapping_report.get("imported_relation_count") or 0),
                    "unresolved_reference_count": int(mapping_report.get("unresolved_reference_count") or 0),
                    "ambiguous_anchor_count": int(mapping_report.get("ambiguous_anchor_count") or 0),
                    "unresolved_anchor_count": int(mapping_report.get("unresolved_anchor_count") or 0),
                }
            )
        return documents

    def load_source_mapping_bundle(
        self,
        project_id: str,
        document_id: str,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        project, _ = self._load_project(project_id)
        if revision_id is None:
            latest = self.repository.artifact_store.get_latest_artifact(
                "source_mapping_bundles",
                document_id,
            )
            if latest is None:
                raise FileNotFoundError(
                    f"Source mapping bundle not found for document:{document_id}"
                )
            current_revision_id = latest.get("current_revision_id")
            if current_revision_id is None:
                return latest
            payload = self.repository.artifact_store.load_revision(
                "source_mapping_bundles",
                document_id,
                current_revision_id,
            )
        else:
            payload = self.repository.artifact_store.load_revision(
                "source_mapping_bundles",
                document_id,
                revision_id,
            )

        artifact = dict(payload.get("artifact") or payload.get("payload") or {})
        if str(artifact.get("project_id") or "") != project.id:
            raise FileNotFoundError(
                f"Source mapping bundle {document_id} does not belong to project {project.id}."
            )
        return payload

    def list_evaluation_evidence(self, project_id: str) -> list[dict[str, Any]]:
        project, _ = self._load_project(project_id)
        records = self.repository.artifact_store.list_latest_artifacts(
            "evaluation_evidence_bundles",
            project_id=project.id,
        )
        evidence: list[dict[str, Any]] = []
        for record in records:
            payload = dict(record.get("payload") or {})
            evidence.extend(dict(item) for item in list(payload.get("items") or []))
        return sorted(
            evidence,
            key=lambda item: (
                str(item.get("status") or "resolved"),
                str(item.get("metric_name") or item.get("title") or ""),
                str(item.get("evidence_id") or ""),
            ),
        )

    def get_evaluation_evidence(
        self,
        project_id: str,
        evidence_id: str,
    ) -> dict[str, Any]:
        canonical_evidence_id = canonical_artifact_id(evidence_id)
        for item in self.list_evaluation_evidence(project_id):
            if canonical_artifact_id(item.get("evidence_id") or "") == canonical_evidence_id:
                return item
        raise FileNotFoundError(f"Evaluation evidence not found: {evidence_id}")

    def get_claim_evidence_links(
        self,
        project_id: str,
        claim_id: str,
    ) -> list[dict[str, Any]]:
        project, _ = self._load_project(project_id)
        canonical_claim = canonical_artifact_id(claim_id)
        registry = self.load_external_reference_registry(project.id)
        claim_reference_ids = {
            canonical_artifact_id(item.get("reference_id") or "")
            for item in list((registry.get("artifact") or {}).get("links") or [])
            if str(item.get("subject_kind") or "") == "claim"
            and canonical_artifact_id(item.get("subject_id") or "") == canonical_claim
        }
        records = self.repository.artifact_store.list_latest_artifacts(
            "evaluation_evidence_bundles",
            project_id=project.id,
        )
        links_by_id: dict[str, dict[str, Any]] = {}
        for record in records:
            payload = dict(record.get("payload") or {})
            for item in list(payload.get("links") or []):
                item_data = dict(item)
                subject_kind = str(item_data.get("subject_kind") or "")
                if subject_kind == "claim" and canonical_artifact_id(
                    item_data.get("subject_id") or ""
                ) == canonical_claim:
                    links_by_id[str(item_data.get("link_id") or len(links_by_id))] = item_data
                    continue
                if subject_kind != "reference":
                    continue
                reference_id = canonical_artifact_id(item_data.get("subject_id") or "")
                if reference_id not in claim_reference_ids:
                    continue
                linked = dict(item_data)
                linked["claim_id"] = claim_id
                links_by_id[str(linked.get("link_id") or len(links_by_id))] = linked
        return sorted(
            links_by_id.values(),
            key=lambda item: (
                str(item.get("status") or ""),
                str(item.get("reference_id") or ""),
                str(item.get("evidence_id") or ""),
            ),
        )

    def get_reference_evidence_links(
        self,
        project_id: str,
        reference_id: str,
    ) -> list[dict[str, Any]]:
        project, _ = self._load_project(project_id)
        canonical_reference = canonical_artifact_id(reference_id)
        records = self.repository.artifact_store.list_latest_artifacts(
            "evaluation_evidence_bundles",
            project_id=project.id,
        )
        links: list[dict[str, Any]] = []
        for record in records:
            payload = dict(record.get("payload") or {})
            links.extend(
                dict(item)
                for item in list(payload.get("links") or [])
                if str(item.get("subject_kind") or "") == "reference"
                and canonical_artifact_id(item.get("subject_id") or "") == canonical_reference
            )
        return links

    def _claim_preview(self, claim: dict[str, Any]) -> dict[str, Any]:
        claim_id = canonical_artifact_id(claim.get("claim_id") or claim.get("id") or "")
        return ExternalReferenceArtifactPreview(
            artifact_kind="claim",
            artifact_id=claim_id,
            claim_id=claim_id,
            title=str(claim.get("title") or claim_id),
            summary=str(
                claim.get("normalized_statement")
                or claim.get("nl_statement")
                or claim.get("statement")
                or claim_id
            ),
            metadata={
                "status": str(claim.get("status") or ""),
                "claim_kind": str(claim.get("claim_kind") or ""),
                "claim_class": str(claim.get("claim_class") or ""),
            },
        ).model_dump(mode="json", exclude_none=True)

    def _profile_preview(self, claim_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        profile_id = str(profile.get("profile_id") or claim_id)
        return ExternalReferenceArtifactPreview(
            artifact_kind="assurance_profile",
            artifact_id=profile_id,
            claim_id=claim_id,
            title=str(profile.get("profile_id") or f"Profile for {claim_id}"),
            summary=str(
                profile.get("decision_rationale")
                or profile.get("overall_status")
                or "Assurance profile recorded."
            ),
            metadata={
                "gate": str(profile.get("gate") or ""),
                "recommended_gate": str(profile.get("recommended_gate") or ""),
                "formal_status": str(profile.get("formal_status") or ""),
                "support_status": str(profile.get("support_status") or ""),
                "intent_status": str(profile.get("intent_status") or ""),
            },
        ).model_dump(mode="json", exclude_none=True)

    def _audit_preview(self, claim_id: str, event: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(event.get("metadata") or {})
        return ExternalReferenceArtifactPreview(
            artifact_kind="audit_report",
            artifact_id=str(event.get("event_id") or claim_id),
            claim_id=claim_id,
            title=str(metadata.get("target_theorem") or f"Audit report for {claim_id}"),
            summary=str(event.get("notes") or metadata.get("summary") or "Audit workflow recorded."),
            created_at=str(event.get("created_at") or ""),
            metadata={
                "event_type": str(event.get("event_type") or ""),
                "session_name": str(metadata.get("session_name") or ""),
                "target_theorem": str(metadata.get("target_theorem") or ""),
                "status": str(metadata.get("status") or ""),
            },
        ).model_dump(mode="json", exclude_none=True)

    def _review_event_preview(self, claim_id: str, event: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(event.get("metadata") or {})
        return ExternalReferenceArtifactPreview(
            artifact_kind="review_event",
            artifact_id=str(event.get("event_id") or claim_id),
            claim_id=claim_id,
            title=str(event.get("event_type") or "review_event"),
            summary=str(event.get("notes") or metadata.get("summary") or "Review note recorded."),
            created_at=str(event.get("created_at") or ""),
            metadata={
                "actor": str(event.get("actor") or ""),
                "actor_role": str(event.get("actor_role") or ""),
                "event_type": str(event.get("event_type") or ""),
            },
        ).model_dump(mode="json", exclude_none=True)

    def _build_external_reference_registry_payload(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        graph_data = graph_data or _empty_graph_data(project)
        store = self.repository.artifact_store
        bundle_records = store.list_latest_artifacts(
            "source_mapping_bundles",
            project_id=project.id,
        )
        claims = [dict(item) for item in list(graph_data.get("claims") or [])]
        claim_ids = {
            canonical_artifact_id(claim.get("claim_id") or "")
            for claim in claims
            if claim.get("claim_id")
        }
        claim_index = {
            canonical_artifact_id(claim.get("claim_id") or ""): claim for claim in claims
        }
        references_by_id: dict[str, dict[str, Any]] = {}
        match_index: dict[str, str] = {}
        links_by_id: dict[str, dict[str, Any]] = {}
        source_documents_by_ref: dict[str, dict[str, Any]] = {}
        direct_claim_link_ids: set[tuple[str, str]] = set()

        def upsert_reference(reference: dict[str, Any]) -> None:
            reference_id = str(reference["reference_id"])
            existing = references_by_id.get(reference_id)
            if existing is None:
                references_by_id[reference_id] = reference
            else:
                existing["status"] = pick_status(
                    str(existing.get("status") or ""),
                    str(reference.get("status") or ""),
                )
                for key in ("citation_anchor", "citation", "provenance", "uncertainty"):
                    incoming = dict(reference.get(key) or {})
                    current = dict(existing.get(key) or {})
                    for field, value in incoming.items():
                        if current.get(field) in (None, "", [], {}):
                            current[field] = value
                    existing[key] = current
            stored = references_by_id[reference_id]
            for match_key in reference_match_keys(dict(stored.get("citation_anchor") or {})):
                match_index.setdefault(match_key, reference_id)

        def add_link(
            *,
            reference_id: str,
            status: str,
            subject_kind: str,
            subject_id: str,
            claim_id: str | None,
            relation: str,
            source_document: dict[str, Any],
            artifact_preview: dict[str, Any],
        ) -> None:
            link_id = build_link_id(reference_id, subject_kind, subject_id, relation)
            if link_id in links_by_id:
                return
            links_by_id[link_id] = ExternalReferenceLink(
                link_id=link_id,
                reference_id=reference_id,
                status=status,
                subject_kind=subject_kind,
                subject_id=subject_id,
                claim_id=claim_id,
                relation=relation,
                source_document=source_document,
                artifact_preview=artifact_preview,
            ).model_dump(mode="json", exclude_none=True)
            reference = references_by_id.get(reference_id)
            if reference is not None:
                if claim_id and claim_id not in reference["linked_claim_ids"]:
                    reference["linked_claim_ids"].append(claim_id)
                artifact_kind = str(artifact_preview.get("artifact_kind") or subject_kind)
                if artifact_kind not in reference["linked_artifact_kinds"]:
                    reference["linked_artifact_kinds"].append(artifact_kind)

        def build_reference_from_anchor(
            *,
            source_document: dict[str, Any],
            source_mapping_ref: dict[str, Any],
            anchor: dict[str, Any],
            citation: dict[str, Any],
            provenance: dict[str, Any] | None = None,
            uncertainty: dict[str, Any] | None = None,
            status_override: str | None = None,
        ) -> dict[str, Any]:
            reference_id = str(
                anchor.get("anchor_id")
                or derive_reference_id(
                    source_ref=str(anchor.get("source_ref") or source_document["document_ref"]),
                    excerpt=str(anchor.get("excerpt") or citation.get("excerpt") or "") or None,
                    span_start=anchor.get("span_start"),
                    span_end=anchor.get("span_end"),
                )
            )
            status = str(status_override or anchor.get("status") or "resolved")
            return build_reference_record(
                reference_id=reference_id,
                status=status,
                source_document=source_document_summary(
                    source_document,
                    current_revision_id=str(source_mapping_ref.get("revision_id") or ""),
                ),
                citation_anchor=anchor,
                citation=citation,
                source_mapping_ref=source_mapping_ref,
                provenance=provenance,
                uncertainty=uncertainty,
            ).model_dump(mode="json", exclude_none=True)

        for record in bundle_records:
            payload = dict(record.get("payload") or {})
            source_document = dict(payload.get("source_document") or {})
            document_id = str(source_document.get("document_id") or record["artifact_id"])
            current_revision_id = str(record.get("current_revision_id") or "")
            source_document["document_id"] = document_id
            source_document["document_ref"] = str(source_document.get("document_ref") or document_id)
            source_mapping_ref = {
                "artifact_kind": "source_mapping_bundle",
                "artifact_id": document_id,
                "revision_id": current_revision_id,
                "project_id": project.id,
                "uri": f"source-mapping://{project.id}/{document_id}",
            }
            source_documents_by_ref[source_document["document_ref"]] = source_document

            for item in list((payload.get("evidence_items") or [])):
                anchor = dict(item.get("citation_anchor") or {})
                if not anchor:
                    continue
                upsert_reference(
                    build_reference_from_anchor(
                        source_document=source_document,
                        source_mapping_ref=source_mapping_ref,
                        anchor=anchor,
                        citation={
                            "title": str(item.get("title") or ""),
                            "summary": str(item.get("summary") or ""),
                            "excerpt": item.get("excerpt"),
                            "source_location": anchor.get("source_location"),
                        },
                        provenance=dict(item.get("provenance") or {}),
                        uncertainty=dict(item.get("uncertainty") or {}),
                    )
                )

            mapping_report = dict(payload.get("mapping_report") or {})
            for mapping in list(mapping_report.get("claim_mappings") or []):
                claim_id = canonical_artifact_id(mapping.get("proposed_claim_id") or "")
                if claim_id and claim_id not in claim_ids:
                    continue
                anchor = dict(mapping.get("citation_anchor") or {})
                if not anchor:
                    continue
                reference = build_reference_from_anchor(
                    source_document=source_document,
                    source_mapping_ref=source_mapping_ref,
                    anchor=anchor,
                    citation={
                        "mapping_rationale": str(mapping.get("mapping_rationale") or ""),
                        "source_claim_id": str(mapping.get("source_claim_id") or ""),
                        "excerpt": anchor.get("excerpt"),
                        "source_location": anchor.get("source_location"),
                    },
                    uncertainty=dict(mapping.get("uncertainty") or {}),
                )
                upsert_reference(reference)
                if claim_id:
                    direct_claim_link_ids.add((claim_id, reference["reference_id"]))

            for unresolved in list(payload.get("unresolved_references") or []):
                anchor = dict(unresolved.get("citation_anchor") or {})
                if anchor:
                    reference = build_reference_from_anchor(
                        source_document=source_document,
                        source_mapping_ref=source_mapping_ref,
                        anchor=anchor,
                        citation={
                            "description": str(unresolved.get("description") or ""),
                            "suggested_resolution": unresolved.get("suggested_resolution"),
                            "excerpt": anchor.get("excerpt"),
                            "source_location": anchor.get("source_location"),
                        },
                        uncertainty={"confidence": unresolved.get("confidence")},
                    )
                else:
                    reference = build_reference_record(
                        reference_id=str(unresolved.get("reference_id") or document_id),
                        status="unresolved",
                        source_document=source_document_summary(
                            source_document,
                            current_revision_id=current_revision_id,
                        ),
                        citation_anchor={},
                        citation={
                            "description": str(unresolved.get("description") or ""),
                            "suggested_resolution": unresolved.get("suggested_resolution"),
                        },
                        source_mapping_ref=source_mapping_ref,
                        uncertainty={"confidence": unresolved.get("confidence")},
                    ).model_dump(mode="json", exclude_none=True)
                upsert_reference(reference)

        for claim_id, claim in claim_index.items():
            claim_preview = self._claim_preview(claim)
            source_anchors = list(
                ((claim.get("provenance") or {}).get("source_anchors") or [])
            )
            linked_reference_ids: list[str] = []
            for anchor in source_anchors:
                anchor_data = dict(anchor or {})
                source_ref = str(anchor_data.get("source_ref") or "")
                if not source_ref or source_ref.startswith("project:"):
                    continue
                reference_id = None
                for match_key in reference_match_keys(anchor_data):
                    reference_id = match_index.get(match_key)
                    if reference_id:
                        break
                if reference_id is None and source_ref in source_documents_by_ref:
                    source_document = source_documents_by_ref[source_ref]
                    source_mapping_ref = {
                        "artifact_kind": "source_mapping_bundle",
                        "artifact_id": str(source_document.get("document_id") or ""),
                        "revision_id": str(
                            store.get_latest_artifact(
                                "source_mapping_bundles",
                                str(source_document.get("document_id") or ""),
                            )["current_revision_id"]
                        ),
                        "project_id": project.id,
                        "uri": f"source-mapping://{project.id}/{source_document.get('document_id')}",
                    }
                    reference = build_reference_from_anchor(
                        source_document=source_document,
                        source_mapping_ref=source_mapping_ref,
                        anchor={
                            **anchor_data,
                            "status": "stale",
                            "anchor_id": derive_reference_id(
                                source_ref=source_ref,
                                excerpt=str(anchor_data.get("excerpt") or "") or None,
                                span_start=anchor_data.get("span_start"),
                                span_end=anchor_data.get("span_end"),
                            ),
                        },
                        citation={
                            "excerpt": anchor_data.get("excerpt"),
                            "source_location": anchor_data.get("source_location"),
                            "reason": "Anchor is not present in the latest source-mapping bundle.",
                        },
                        provenance={"derived_from": "claim_graph_provenance"},
                        status_override="stale",
                    )
                    upsert_reference(reference)
                    reference_id = reference["reference_id"]
                if reference_id is None:
                    continue
                linked_reference_ids.append(reference_id)
                reference = references_by_id[reference_id]
                add_link(
                    reference_id=reference_id,
                    status=str(reference.get("status") or "resolved"),
                    subject_kind="claim",
                    subject_id=claim_id,
                    claim_id=claim_id,
                    relation="supports_claim",
                    source_document=dict(reference["source_document"]),
                    artifact_preview=claim_preview,
                )

            if not linked_reference_ids:
                continue
            try:
                profile = store.load_assurance_profile_for_claim(claim_id).model_dump(
                    mode="json",
                    exclude_none=True,
                )
            except FileNotFoundError:
                profile = None
            review_events = store.query_review_events(claim_id)
            audit_events = [
                event for event in review_events if event.get("event_type") == "audit_workflow"
            ]
            latest_audit = audit_events[-1] if audit_events else None
            for reference_id in linked_reference_ids:
                reference = references_by_id[reference_id]
                if profile is not None:
                    add_link(
                        reference_id=reference_id,
                        status=str(reference.get("status") or "resolved"),
                        subject_kind="assurance_profile",
                        subject_id=str(profile.get("profile_id") or claim_id),
                        claim_id=claim_id,
                        relation="profile_for_claim",
                        source_document=dict(reference["source_document"]),
                        artifact_preview=self._profile_preview(claim_id, profile),
                    )
                if latest_audit is not None:
                    add_link(
                        reference_id=reference_id,
                        status=str(reference.get("status") or "resolved"),
                        subject_kind="audit_report",
                        subject_id=str(latest_audit.get("event_id") or claim_id),
                        claim_id=claim_id,
                        relation="audit_for_claim",
                        source_document=dict(reference["source_document"]),
                        artifact_preview=self._audit_preview(claim_id, latest_audit),
                    )
                for event in review_events:
                    if event.get("event_type") == "audit_workflow":
                        continue
                    add_link(
                        reference_id=reference_id,
                        status=str(reference.get("status") or "resolved"),
                        subject_kind="review_event",
                        subject_id=str(event.get("event_id") or claim_id),
                        claim_id=claim_id,
                        relation="review_for_claim",
                        source_document=dict(reference["source_document"]),
                        artifact_preview=self._review_event_preview(claim_id, event),
                    )

        evaluation_bundle_records = store.list_latest_artifacts(
            "evaluation_evidence_bundles",
            project_id=project.id,
        )
        for record in evaluation_bundle_records:
            payload = dict(record.get("payload") or {})
            for evidence in list(payload.get("items") or []):
                evidence_record = dict(evidence)
                evidence_id = canonical_artifact_id(evidence_record.get("evidence_id") or "")
                evidence_status = str(evidence_record.get("status") or "resolved")
                linked_claim_ids = [
                    canonical_artifact_id(item)
                    for item in list(evidence_record.get("linked_claim_ids") or [])
                    if item
                ]
                preview = self._evaluation_evidence_preview(evidence_record)
                for reference_id in list(evidence_record.get("linked_reference_ids") or []):
                    canonical_reference_id = canonical_artifact_id(reference_id)
                    reference = references_by_id.get(canonical_reference_id)
                    if reference is None:
                        continue
                    add_link(
                        reference_id=canonical_reference_id,
                        status=pick_status(
                            str(reference.get("status") or ""),
                            evidence_status,
                        ),
                        subject_kind="evaluation_evidence",
                        subject_id=evidence_id,
                        claim_id=linked_claim_ids[0] if linked_claim_ids else None,
                        relation=(
                            "evaluation_for_claim"
                            if linked_claim_ids
                            else "evaluation_for_reference"
                        ),
                        source_document=dict(reference["source_document"]),
                        artifact_preview=preview,
                    )

        references = sorted(
            references_by_id.values(),
            key=lambda item: (
                {"resolved": 0, "ambiguous": 1, "unresolved": 2, "stale": 3}.get(
                    str(item.get("status") or "resolved"),
                    0,
                ),
                str(((item.get("source_document") or {}).get("display_name") or "")),
                str(item.get("reference_id") or ""),
            ),
        )
        for reference in references:
            reference["linked_claim_ids"] = sorted(
                {canonical_artifact_id(item) for item in list(reference.get("linked_claim_ids") or [])}
            )
            reference["linked_artifact_kinds"] = sorted(
                {str(item) for item in list(reference.get("linked_artifact_kinds") or [])}
            )
        links = sorted(
            links_by_id.values(),
            key=lambda item: (
                str(item.get("subject_kind") or ""),
                str(item.get("claim_id") or ""),
                str(item.get("subject_id") or ""),
                str(item.get("reference_id") or ""),
            ),
        )
        status_counts: dict[str, int] = {}
        for reference in references:
            status = str(reference.get("status") or "resolved")
            status_counts[status] = status_counts.get(status, 0) + 1
        return ExternalReferenceRegistry(
            registry_id=reference_registry_artifact_id(project.id),
            project_id=project.id,
            generated_at=now_utc_iso(),
            reference_count=len(references),
            link_count=len(links),
            source_document_count=len(bundle_records),
            status_counts=status_counts,
            references=references,
            links=links,
        ).model_dump(mode="json", exclude_none=True)

    def load_external_reference_registry(
        self,
        project_id: str,
        *,
        revision_id: str | None = None,
    ) -> dict[str, Any]:
        project, _ = self._load_project(project_id)
        artifact_id = reference_registry_artifact_id(project.id)
        if revision_id is not None:
            payload = self.repository.artifact_store.load_revision(
                "external_reference_registries",
                artifact_id,
                revision_id,
            )
            artifact = dict(payload.get("artifact") or {})
            if str(artifact.get("project_id") or "") != project.id:
                raise FileNotFoundError(
                    f"Reference registry revision {revision_id} does not belong to project {project.id}."
                )
            return payload

        payload = self._build_external_reference_registry_payload(project.id)
        latest = self.repository.artifact_store.get_latest_artifact(
            "external_reference_registries",
            artifact_id,
        )
        payload_signature = reference_registry_signature(payload)
        if latest is not None:
            latest_payload = dict(latest.get("payload") or {})
            if reference_registry_signature(latest_payload) == payload_signature:
                current_revision_id = latest.get("current_revision_id")
                if current_revision_id:
                    return self.repository.artifact_store.load_revision(
                        "external_reference_registries",
                        artifact_id,
                        str(current_revision_id),
                    )
                return {
                    "revision": {
                        "artifact_kind": "external_reference_registries",
                        "artifact_id": artifact_id,
                        "project_id": project.id,
                    },
                    "artifact": latest_payload,
                }

        saved = self.repository.artifact_store.save_json_artifact(
            "external_reference_registries",
            artifact_id,
            payload,
            actor="reference_registry",
            reason="rebuild external reference registry",
            metadata={
                "project_id": project.id,
                "reference_count": int(payload.get("reference_count") or 0),
                "link_count": int(payload.get("link_count") or 0),
            },
        )
        return self.repository.artifact_store.load_revision(
            "external_reference_registries",
            artifact_id,
            str(saved["revision_id"]),
        )

    async def trace_forward(self, project_id: str, claim_id: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_index(project, graph_data)
        start = claims.get(claim_id)
        if start is None:
            return {"error": f"Claim {claim_id} not found"}

        reachable_ids = _forward_trace(project.relations, claim_id)
        graph_context = self._build_graph_context(
            project, graph_data, [claim_id, *reachable_ids]
        )
        system = FORWARD_SYSTEM.format(
            domain_context=DOMAIN_CONTEXT.get(project.domain, "")
        )
        user = (
            f"## Starting claim\n{json.dumps(start.model_dump(exclude_none=True), indent=2)}\n\n"
            f"## Reachable subgraph\n{graph_context}\n\n"
            f"Trace forward from '{start.title}' and check each derivation step."
        )
        result = parse_json(await self._llm_call(system, user))

        graph_data = graph_data or _empty_graph_data(project)
        for hidden in result.get("new_hidden_assumptions", []):
            hidden_claim = Claim(
                title=hidden["title"],
                statement=hidden["statement"],
                role=ClaimRole.hidden_assumption,
                status=ClaimStatus.inferred,
                domain=project.domain,
                notes=[f"Surfaced during forward trace from {claim_id}"],
            )
            graph_data = self._append_claim(project, graph_data, hidden_claim)
            project.relations.append(
                Relation(
                    source_id=hidden_claim.id,
                    target_id=hidden.get("needed_for", claim_id),
                    relation_type=RelationType.assumes,
                    strength=Strength.unknown,
                    rationale="Hidden assumption surfaced during analysis",
                )
            )

        for gap_data in result.get("gaps", []):
            project.gaps.append(
                Gap(
                    kind=gap_data.get("kind", "logical_leap"),
                    description=gap_data["description"],
                    affected_claim_ids=gap_data.get("between", []),
                    severity=gap_data.get("severity", "major"),
                    suggested_fix=gap_data.get("suggested_fix"),
                )
            )

        self._save_project(project, graph_data)
        return result

    async def trace_backward(self, project_id: str, claim_id: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_index(project, graph_data)
        target = claims.get(claim_id)
        if target is None:
            return {"error": f"Claim {claim_id} not found"}

        ancestor_ids = _backward_trace(project.relations, claim_id)
        graph_context = self._build_graph_context(
            project, graph_data, [*ancestor_ids, claim_id]
        )
        system = BACKWARD_SYSTEM.format(
            domain_context=DOMAIN_CONTEXT.get(project.domain, "")
        )
        user = (
            f"## Conclusion claim\n{json.dumps(target.model_dump(exclude_none=True), indent=2)}\n\n"
            f"## Support subgraph\n{graph_context}\n\n"
            f"Trace backward from '{target.title}' to its foundations."
        )
        result = parse_json(await self._llm_call(system, user))

        graph_data = graph_data or _empty_graph_data(project)
        for hidden in result.get("new_hidden_assumptions", []):
            hidden_claim = Claim(
                title=hidden["title"],
                statement=hidden["statement"],
                role=ClaimRole.hidden_assumption,
                status=ClaimStatus.inferred,
                domain=project.domain,
                notes=[f"Surfaced during backward trace from {claim_id}"],
            )
            graph_data = self._append_claim(project, graph_data, hidden_claim)
            project.relations.append(
                Relation(
                    source_id=hidden_claim.id,
                    target_id=hidden.get("needed_for", claim_id),
                    relation_type=RelationType.assumes,
                    strength=Strength.unknown,
                )
            )

        for gap_data in result.get("gaps", []):
            project.gaps.append(
                Gap(
                    kind=gap_data.get("kind", "logical_leap"),
                    description=gap_data["description"],
                    affected_claim_ids=gap_data.get("between", []),
                    severity=gap_data.get("severity", "major"),
                    suggested_fix=gap_data.get("suggested_fix"),
                )
            )

        self._save_project(project, graph_data)
        return result

    async def find_gaps(self, project_id: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        graph_context = self._build_graph_context(
            project, graph_data, _claim_ids(graph_data)
        )
        system = GAPS_SYSTEM.format(
            domain_context=DOMAIN_CONTEXT.get(project.domain, "")
        )
        result = parse_json(
            await self._llm_call(system, f"## Full argument graph\n{graph_context}")
        )
        project.gaps = [
            Gap(
                kind=gap.get("kind", "unknown"),
                description=gap["description"],
                affected_claim_ids=gap.get("affected_claim_ids", []),
                severity=gap.get("severity", "major"),
                suggested_fix=gap.get("suggested_fix"),
            )
            for gap in result.get("gaps", [])
        ]
        self._save_project(project, graph_data)
        return result

    async def assess_soundness(
        self, project_id: str, claim_id: str | None = None
    ) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        if claim_id:
            graph_claims = self._claims_index(project, graph_data)
            if claim_id not in graph_claims:
                return {"error": f"Claim {claim_id} not found"}
            subset = [*_backward_trace(project.relations, claim_id), claim_id]
            focus = (
                f"Assess the soundness of claim '{graph_claims[claim_id].title}' and its support chain."
            )
        else:
            subset = _claim_ids(graph_data)
            focus = "Assess the overall soundness of this entire argument."

        graph_context = self._build_graph_context(project, graph_data, subset)
        system = ASSESS_SYSTEM.format(
            domain_context=DOMAIN_CONTEXT.get(project.domain, "")
        )
        user = (
            f"## Argument graph\n{graph_context}\n\n"
            f"## Known gaps\n{json.dumps([gap.model_dump() for gap in project.gaps], indent=2)}\n\n"
            f"{focus}"
        )
        result = parse_json(await self._llm_call(system, user))
        score = SoundnessScore(
            completeness=result.get("completeness", 0),
            logical_validity=result.get("logical_validity", 0),
            evidential_strength=result.get("evidential_strength", 0),
            transparency=result.get("transparency", 0),
            overall=result.get("overall", 0),
            rationale=result.get("rationale", ""),
        )
        if claim_id is None:
            project.soundness = score
            self._save_project(project, graph_data)
        result["score"] = score.model_dump()
        return result

    def add_claim(
        self,
        project_id: str,
        title: str,
        statement: str,
        role: str,
        source_location: str = "",
        scope: str = "",
        depth: int = 0,
    ) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claim = Claim(
            title=title,
            statement=statement,
            role=ClaimRole(role),
            status=ClaimStatus.stated,
            domain=project.domain,
            source_location=source_location or None,
            scope=scope or None,
            depth=depth,
        )
        graph_data = self._append_claim(project, graph_data, claim)
        self._save_project(project, graph_data)
        return {
            "claim_id": claim.id,
            "message": f"Claim '{title}' added. Use link_claims to connect it.",
        }

    def link_claims(
        self,
        project_id: str,
        source_id: str,
        target_id: str,
        relation_type: str,
        strength: str = "unknown",
        rationale: str = "",
    ) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claim_ids = set(_claim_ids(graph_data))
        if source_id not in claim_ids:
            return {"error": f"Source claim {source_id} not found"}
        if target_id not in claim_ids:
            return {"error": f"Target claim {target_id} not found"}

        relation = Relation(
            source_id=source_id,
            target_id=target_id,
            relation_type=RelationType(relation_type),
            strength=Strength(strength),
            rationale=rationale or None,
        )
        project.relations.append(relation)
        self._save_project(project, graph_data)
        return {
            "relation_id": relation.id,
            "message": f"Linked {source_id} -> {target_id} ({relation_type})",
        }

    def challenge_claim(self, project_id: str, claim_id: str, reason: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        graph_data = self._update_claim(
            project,
            graph_data,
            claim_id,
            status=ClaimStatus.challenged,
            notes_append=[f"Challenged: {reason}"],
        )
        self._save_project(project, graph_data)
        claim = self._claims_index(project, graph_data)[claim_id]
        return {
            "claim_id": claim_id,
            "status": "challenged",
            "message": f"Claim '{claim.title}' marked as challenged.",
        }

    def get_summary(self, project_id: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_from_graph(project, graph_data)
        role_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        for claim in claims:
            role_counts[claim.role.value] = role_counts.get(claim.role.value, 0) + 1
            status_counts[claim.status.value] = status_counts.get(claim.status.value, 0) + 1

        gap_severity: dict[str, int] = {}
        for gap in project.gaps:
            gap_severity[gap.severity] = gap_severity.get(gap.severity, 0) + 1

        result: dict[str, Any] = {
            "project": project.name,
            "domain": project.domain.value,
            "total_claims": len(claims),
            "total_relations": len(project.relations),
            "total_gaps": len(project.gaps),
            "claims_by_role": role_counts,
            "claims_by_status": status_counts,
            "gaps_by_severity": gap_severity,
            "max_derivation_depth": max((claim.depth for claim in claims), default=0),
            "axioms": len(_roots(project.relations, claims)),
            "conclusions": len(_leaves(project.relations, claims)),
        }
        if project.soundness:
            result["soundness"] = project.soundness.model_dump()
        return result

    def snapshot(self, project_id: str) -> dict[str, Any]:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_from_graph(project, graph_data)
        return {
            "id": project.id,
            "name": project.name,
            "domain": project.domain.value,
            "description": project.description,
            "created_at": project.created_at,
            "claims": {
                claim.id: claim.model_dump(exclude_none=True) for claim in claims
            },
            "relations": [
                relation.model_dump(exclude_none=True) for relation in project.relations
            ],
            "gaps": [gap.model_dump(exclude_none=True) for gap in project.gaps],
            "soundness": project.soundness.model_dump(exclude_none=True)
            if project.soundness
            else None,
        }

    def export_graph(self, project_id: str, format: str = "json") -> str:
        project, graph_data = self._load_project(project_id)
        claims = self._claims_from_graph(project, graph_data)
        if format == "mermaid":
            return self._export_mermaid(claims, project.relations)
        if format == "outline":
            return self._export_outline(claims, project.gaps)
        return json.dumps(self.snapshot(project_id), indent=2, default=str)

    def _export_mermaid(self, claims: list[Claim], relations: list[Relation]) -> str:
        lines = ["graph TD"]
        for claim in claims:
            label = claim.title.replace('"', "'")
            shape = (
                "(["
                if claim.role
                in {
                    ClaimRole.axiom,
                    ClaimRole.premise,
                    ClaimRole.statute,
                    ClaimRole.precedent,
                }
                else "["
            )
            close = "])" if shape == "([" else "]"
            lines.append(f'    {claim.id}{shape}"{label}"{close}')

        for relation in relations:
            arrow = "-->" if relation.relation_type == RelationType.derives else "-.->"
            lines.append(
                f"    {relation.source_id} {arrow}|{relation.relation_type.value}| {relation.target_id}"
            )
        return "\n".join(lines)

    def _export_outline(self, claims: list[Claim], gaps: list[Gap]) -> str:
        lines: list[str] = []
        by_depth: dict[int, list[Claim]] = {}
        for claim in claims:
            by_depth.setdefault(claim.depth, []).append(claim)

        for depth in sorted(by_depth):
            indent = "  " * depth
            for claim in by_depth[depth]:
                if claim.status == ClaimStatus.supported:
                    marker = "[+]"
                elif claim.status == ClaimStatus.stated:
                    marker = "[~]"
                elif claim.status in {ClaimStatus.challenged, ClaimStatus.refuted}:
                    marker = "[-]"
                else:
                    marker = "[?]"
                lines.append(f"{indent}{marker} [{claim.role.value}] {claim.title}")
                lines.append(f"{indent}  {claim.statement}")

        if gaps:
            lines.append(f"\n## Gaps ({len(gaps)})")
            for gap in gaps:
                lines.append(f"  [{gap.severity}] {gap.kind}: {gap.description}")

        return "\n".join(lines)


__all__ = ["ClaimTraceService", "parse_json"]
