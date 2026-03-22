"""Workflow DTOs for deterministic FWP-backed audit execution."""

from __future__ import annotations

import uuid
from enum import Enum

from pydantic import BaseModel, Field

from .store import now_utc


class AuditWorkflowStage(str, Enum):
    pending = "pending"
    selecting_artifact = "selecting_artifact"
    proof_audit = "proof_audit"
    profiling = "profiling"
    completed = "completed"
    failed = "failed"


class AuditWorkflowTransition(BaseModel):
    stage: AuditWorkflowStage
    note: str | None = None
    created_at: str = Field(default_factory=lambda: now_utc().isoformat())


class AuditWorkflowState(BaseModel):
    workflow_id: str = Field(
        default_factory=lambda: f"workflow.audit.{uuid.uuid4().hex[:10]}"
    )
    project_id: str
    claim_graph_id: str
    claim_id: str
    state: AuditWorkflowStage = AuditWorkflowStage.pending
    selected_formalizer: str | None = None
    session_name: str | None = None
    session_dir: str | None = None
    target_theory: str | None = None
    target_theorem: str | None = None
    proof_request_path: str | None = None
    proof_audit_success: bool | None = None
    profile_id: str | None = None
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    transitions: list[AuditWorkflowTransition] = Field(default_factory=list)

    def transition(self, stage: AuditWorkflowStage, note: str | None = None) -> None:
        self.state = stage
        self.transitions.append(AuditWorkflowTransition(stage=stage, note=note))

    def mark_failed(self, reason: str) -> None:
        self.failure_reason = reason
        self.transition(AuditWorkflowStage.failed, note=reason)

    def mark_completed(
        self,
        *,
        profile_id: str,
        blocking_issues: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.profile_id = profile_id
        self.failure_reason = None
        self.blocking_issues = list(blocking_issues or [])
        self.warnings = list(warnings or [])
        self.transition(
            AuditWorkflowStage.completed,
            note="deterministic audit workflow completed",
        )
