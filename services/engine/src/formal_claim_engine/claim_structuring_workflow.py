"""Workflow DTOs for claim-structuring admission and retry handling."""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field

from .store import now_utc

STRUCTURING_PLANNER_ACTIONS = {"admit_claims", "restructure"}


class ClaimStructuringStage(str, Enum):
    pending = "pending"
    structuring = "structuring"
    validating = "validating"
    admitted = "admitted"
    failed = "failed"


class ClaimStructuringTransition(BaseModel):
    stage: ClaimStructuringStage
    note: str | None = None
    created_at: str = Field(default_factory=lambda: now_utc().isoformat())


class ClaimStructuringAttempt(BaseModel):
    attempt_number: int
    source: str
    note: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    prompt_lineage: dict[str, str] | None = None
    created_at: str = Field(default_factory=lambda: now_utc().isoformat())


class ClaimStructuringWorkflowState(BaseModel):
    workflow_id: str = Field(
        default_factory=lambda: f"workflow.claim_structuring.{uuid.uuid4().hex[:10]}"
    )
    project_id: str
    user_input: str
    max_attempts: int
    state: ClaimStructuringStage = ClaimStructuringStage.pending
    planner_action: str | None = None
    planner_rationale: str = ""
    planner_warnings: list[str] = Field(default_factory=list)
    planner_prompt_lineage: dict[str, str] | None = None
    admitted_graph_id: str | None = None
    failure_reason: str | None = None
    last_validation_errors: list[str] = Field(default_factory=list)
    transitions: list[ClaimStructuringTransition] = Field(default_factory=list)
    attempts: list[ClaimStructuringAttempt] = Field(default_factory=list)

    def transition(self, stage: ClaimStructuringStage, note: str | None = None) -> None:
        self.state = stage
        self.transitions.append(ClaimStructuringTransition(stage=stage, note=note))

    def record_attempt(
        self,
        *,
        attempt_number: int,
        source: str,
        note: str | None = None,
        validation_errors: list[str] | None = None,
        prompt_lineage: dict[str, str] | None = None,
    ) -> None:
        self.attempts.append(
            ClaimStructuringAttempt(
                attempt_number=attempt_number,
                source=source,
                note=note,
                validation_errors=list(validation_errors or []),
                prompt_lineage=prompt_lineage,
            )
        )

    def mark_admitted(self, graph_id: str, note: str | None = None) -> None:
        self.admitted_graph_id = graph_id
        self.failure_reason = None
        self.last_validation_errors = []
        self.transition(ClaimStructuringStage.admitted, note=note)

    def mark_failed(
        self,
        reason: str,
        *,
        validation_errors: list[str] | None = None,
    ) -> None:
        self.failure_reason = reason
        self.last_validation_errors = list(validation_errors or [])
        self.transition(ClaimStructuringStage.failed, note=reason)
