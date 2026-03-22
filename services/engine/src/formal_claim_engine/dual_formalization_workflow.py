"""Workflow DTOs for dual formalization and divergence capture."""

from __future__ import annotations

import hashlib
import json
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .store import now_utc


class DualFormalizationStage(str, Enum):
    pending = "pending"
    formalizing = "formalizing"
    completed = "completed"
    failed = "failed"


class FormalizationAttemptStatus(str, Enum):
    succeeded = "succeeded"
    failed = "failed"


class DualFormalizationTransition(BaseModel):
    stage: DualFormalizationStage
    note: str | None = None
    created_at: str = Field(default_factory=lambda: now_utc().isoformat())


class FormalizationAttemptLineage(BaseModel):
    project_id: str
    claim_graph_id: str
    claim_id: str
    workflow_id: str
    source_role: str


class FormalizationAttempt(BaseModel):
    attempt_id: str = Field(
        default_factory=lambda: f"formalization_attempt.{uuid.uuid4().hex[:10]}"
    )
    formalizer_label: str
    status: FormalizationAttemptStatus
    lineage: FormalizationAttemptLineage
    output: dict[str, Any] | None = None
    output_sha256: str | None = None
    session_name: str | None = None
    module_name: str | None = None
    primary_target: str | None = None
    back_translation: str | None = None
    divergence_notes: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    prompt_lineage: dict[str, str] | None = None
    created_at: str = Field(default_factory=lambda: now_utc().isoformat())


class FormalizationDivergence(BaseModel):
    classification: str
    summary: str
    successful_formalizers: list[str] = Field(default_factory=list)
    failed_formalizers: list[str] = Field(default_factory=list)
    primary_target_match: bool | None = None
    back_translation_match: bool | None = None
    code_sha_match: bool | None = None
    assumptions_only_in_a: list[str] = Field(default_factory=list)
    assumptions_only_in_b: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DualFormalizationWorkflowState(BaseModel):
    workflow_id: str = Field(
        default_factory=lambda: f"workflow.dual_formalization.{uuid.uuid4().hex[:10]}"
    )
    project_id: str
    claim_graph_id: str
    claim_id: str
    dual_required: bool
    selected_formalizers: list[str] = Field(default_factory=list)
    state: DualFormalizationStage = DualFormalizationStage.pending
    successful_formalizers: list[str] = Field(default_factory=list)
    failed_formalizers: list[str] = Field(default_factory=list)
    divergence: FormalizationDivergence | None = None
    failure_reason: str | None = None
    transitions: list[DualFormalizationTransition] = Field(default_factory=list)
    attempts: list[FormalizationAttempt] = Field(default_factory=list)

    def transition(self, stage: DualFormalizationStage, note: str | None = None) -> None:
        self.state = stage
        self.transitions.append(DualFormalizationTransition(stage=stage, note=note))

    def record_attempt(self, attempt: FormalizationAttempt) -> None:
        self.attempts.append(attempt)
        if attempt.status == FormalizationAttemptStatus.succeeded:
            if attempt.formalizer_label not in self.successful_formalizers:
                self.successful_formalizers.append(attempt.formalizer_label)
            if attempt.formalizer_label in self.failed_formalizers:
                self.failed_formalizers.remove(attempt.formalizer_label)
            return
        if attempt.formalizer_label not in self.failed_formalizers:
            self.failed_formalizers.append(attempt.formalizer_label)

    def mark_completed(
        self,
        divergence: FormalizationDivergence,
        *,
        note: str | None = None,
    ) -> None:
        self.divergence = divergence
        self.failure_reason = None
        self.transition(DualFormalizationStage.completed, note=note or divergence.summary)

    def mark_failed(
        self,
        reason: str,
        *,
        divergence: FormalizationDivergence | None = None,
    ) -> None:
        self.divergence = divergence
        self.failure_reason = reason
        self.transition(DualFormalizationStage.failed, note=reason)


def normalize_assumptions(output: dict[str, Any] | None) -> list[str]:
    normalized: list[str] = []
    for entry in list((output or {}).get("assumptions_used") or []):
        if not isinstance(entry, dict):
            continue
        carrier = str(entry.get("carrier") or "").strip()
        statement = str(entry.get("statement") or "").strip()
        if carrier or statement:
            normalized.append(f"{carrier}:{statement}")
    return sorted(dict.fromkeys(normalized))


def output_sha256(output: dict[str, Any] | None) -> str | None:
    if not isinstance(output, dict):
        return None
    serialized = json.dumps(output, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
