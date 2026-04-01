"""Control-plane DTOs for the canonical MCP facade."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class McpErrorCode(str, Enum):
    invalid_input = "invalid_input"
    precondition_failed = "precondition_failed"
    transition_denied = "transition_denied"
    artifact_missing = "artifact_missing"
    job_failed = "job_failed"
    capacity_exceeded = "capacity_exceeded"
    internal_error = "internal_error"


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class ArtifactRef(BaseModel):
    artifact_kind: str
    artifact_id: str
    revision_id: str | None = None
    project_id: str | None = None
    claim_id: str | None = None
    uri: str | None = None


class McpErrorPayload(BaseModel):
    code: McpErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class ToolResponseEnvelope(BaseModel):
    ok: bool
    request_id: str
    data: dict[str, Any] | None = None
    error: McpErrorPayload | None = None


class JobHandle(BaseModel):
    job_id: str
    operation: str
    status: JobState
    request_id: str
    queued_at: str
    started_at: str | None = None
    completed_at: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    error: McpErrorPayload | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ArtifactResourceEnvelope(BaseModel):
    uri: str
    artifact_kind: str
    artifact_id: str
    revision_id: str | None = None
    current_revision_id: str | None = None
    revision_status: str
    project_id: str
    claim_id: str | None = None
    payload: dict[str, Any]
    meta: dict[str, Any] = Field(default_factory=dict)


class ProjectResourceEnvelope(BaseModel):
    uri: str
    project_id: str
    payload: dict[str, Any]
    meta: dict[str, Any] = Field(default_factory=dict)
