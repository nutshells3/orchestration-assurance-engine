"""Adapter DTOs for the claim tracing MCP surface."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


TRACE_ROLE_PREFIX = "tracer_role:"
TRACE_STATUS_PREFIX = "tracer_status:"
TRACE_DEPTH_PREFIX = "tracer_depth:"
TRACE_DOMAIN_PREFIX = "tracer_domain:"


def new_id(prefix: str = "c") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:8]}"


def now() -> datetime:
    return datetime.now(timezone.utc)


class Domain(str, Enum):
    academic = "academic"
    legal = "legal"
    formal_proof = "formal_proof"
    general = "general"


class ClaimRole(str, Enum):
    axiom = "axiom"
    definition = "definition"
    premise = "premise"
    lemma = "lemma"
    theorem = "theorem"
    corollary = "corollary"
    hypothesis = "hypothesis"
    observation = "observation"
    interpretation = "interpretation"
    holding = "holding"
    statute = "statute"
    precedent = "precedent"
    conclusion = "conclusion"
    hidden_assumption = "hidden_assumption"


class ClaimStatus(str, Enum):
    stated = "stated"
    inferred = "inferred"
    supported = "supported"
    unsupported = "unsupported"
    challenged = "challenged"
    refuted = "refuted"
    circular = "circular"


class RelationType(str, Enum):
    derives = "derives"
    assumes = "assumes"
    supports = "supports"
    contradicts = "contradicts"
    specializes = "specializes"
    generalizes = "generalizes"
    cites = "cites"
    interprets = "interprets"
    applies_to = "applies_to"
    requires = "requires"
    weakens = "weakens"
    strengthens = "strengthens"


class Strength(str, Enum):
    deductive = "deductive"
    inductive = "inductive"
    abductive = "abductive"
    analogical = "analogical"
    authoritative = "authoritative"
    stipulative = "stipulative"
    weak = "weak"
    unknown = "unknown"


class Claim(BaseModel):
    id: str = Field(default_factory=lambda: new_id("c"))
    title: str
    statement: str
    role: ClaimRole
    status: ClaimStatus = ClaimStatus.stated
    domain: Domain = Domain.general
    source_location: str | None = None
    source_text: str | None = None
    scope: str | None = None
    notes: list[str] = Field(default_factory=list)
    depth: int = 0


class Relation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("r"))
    source_id: str
    target_id: str
    relation_type: RelationType
    strength: Strength = Strength.unknown
    rationale: str | None = None
    gap_note: str | None = None


class Gap(BaseModel):
    id: str = Field(default_factory=lambda: new_id("g"))
    kind: str
    description: str
    affected_claim_ids: list[str]
    severity: str
    suggested_fix: str | None = None


class SoundnessScore(BaseModel):
    completeness: float = 0.0
    logical_validity: float = 0.0
    evidential_strength: float = 0.0
    transparency: float = 0.0
    overall: float = 0.0
    rationale: str = ""


class TraceProjectRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("proj"))
    name: str
    domain: Domain
    description: str = ""
    created_at: datetime = Field(default_factory=now)
    claim_graph_id: str | None = None
    relations: list[Relation] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    soundness: SoundnessScore | None = None


DEPENDENCY_TYPES = {
    RelationType.derives,
    RelationType.assumes,
    RelationType.supports,
    RelationType.cites,
    RelationType.interprets,
    RelationType.applies_to,
    RelationType.requires,
    RelationType.strengthens,
}
