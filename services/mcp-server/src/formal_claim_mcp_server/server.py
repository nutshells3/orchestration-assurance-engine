"""Thin MCP facade over the canonical engine API and read-only artifact surface."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError


def resolve_engine_src() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "services" / "engine" / "src"
        if candidate.exists():
            return candidate
    raise RuntimeError("Could not locate services/engine/src from the MCP server.")


ENGINE_SRC = resolve_engine_src()
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.engine_api import (  # noqa: E402
    AuditRunResult,
    ClaimStructuringRunResult,
    DocumentIngestRunResult,
    DualFormalizationRunResult,
    FormalClaimEngineAPI,
    PrefixSliceRunResult,
    ProfileRecomputeResult,
    ProjectBundleExport,
    TraceExportResult,
)
from formal_claim_engine.fixture_runtime import build_engine_api  # noqa: E402
from formal_claim_engine.config import PipelineConfig  # noqa: E402
from formal_claim_engine.proof_control import ProofControlPlane  # noqa: E402
from formal_claim_engine.promotion_state_machine import PromotionStateError  # noqa: E402
from formal_claim_engine.store import canonical_artifact_id  # noqa: E402

from .jobs import JobCapacityError, JobNotFoundError, McpJobStore
from .models import (
    ArtifactRef,
    ArtifactResourceEnvelope,
    JobHandle,
    McpErrorCode,
    McpErrorPayload,
    ProjectResourceEnvelope,
    ToolResponseEnvelope,
)


log = logging.getLogger(__name__)
mcp = FastMCP("Formal Claim MCP")


class RuntimeState:
    def __init__(
        self,
        *,
        data_dir: str | None = None,
        engine_api: FormalClaimEngineAPI | None = None,
    ) -> None:
        resolved_data_dir = data_dir or os.environ.get("TRACER_DATA_DIR", "./tracer_data")
        self.data_dir = resolved_data_dir
        self.engine_api = engine_api or build_engine_api(data_dir=resolved_data_dir)
        self.jobs = McpJobStore()
        proof_config = getattr(self.engine_api, "config", None) or PipelineConfig(
            data_dir=resolved_data_dir
        )
        self.proof_control = ProofControlPlane(
            config=proof_config,
            data_dir=resolved_data_dir,
        )

    def reset(
        self,
        *,
        data_dir: str | None = None,
        engine_api: FormalClaimEngineAPI | None = None,
    ) -> None:
        resolved_data_dir = data_dir or os.environ.get("TRACER_DATA_DIR", "./tracer_data")
        self.data_dir = resolved_data_dir
        self.engine_api = engine_api or build_engine_api(data_dir=resolved_data_dir)
        self.jobs = McpJobStore()
        proof_config = getattr(self.engine_api, "config", None) or PipelineConfig(
            data_dir=resolved_data_dir
        )
        self.proof_control = ProofControlPlane(
            config=proof_config,
            data_dir=resolved_data_dir,
        )


runtime = RuntimeState()


def reset_runtime(
    *,
    data_dir: str | None = None,
    engine_api: FormalClaimEngineAPI | None = None,
) -> None:
    runtime.reset(data_dir=data_dir, engine_api=engine_api)


def _request_id() -> str:
    return f"req.{uuid.uuid4().hex[:10]}"


def _tool_ok(request_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return ToolResponseEnvelope(
        ok=True,
        request_id=request_id,
        data=data,
    ).model_dump(mode="json", exclude_none=True)


def _tool_error(request_id: str, error: McpErrorPayload) -> dict[str, Any]:
    return ToolResponseEnvelope(
        ok=False,
        request_id=request_id,
        error=error,
    ).model_dump(mode="json", exclude_none=True)


def _map_exception(exc: Exception) -> McpErrorPayload:
    message = str(exc)
    if isinstance(exc, ValidationError):
        return McpErrorPayload(
            code=McpErrorCode.invalid_input,
            message="Input validation failed.",
            details={"errors": exc.errors()},
        )
    if isinstance(exc, PromotionStateError):
        return McpErrorPayload(
            code=McpErrorCode.transition_denied,
            message=message,
        )
    if isinstance(exc, FileNotFoundError):
        if "Proof job" in message:
            return McpErrorPayload(
                code=McpErrorCode.job_failed,
                message=message,
            )
        return McpErrorPayload(
            code=McpErrorCode.artifact_missing,
            message=message,
        )
    if isinstance(exc, JobNotFoundError):
        return McpErrorPayload(
            code=McpErrorCode.job_failed,
            message=f"Job '{message}' was not found.",
        )
    if isinstance(exc, JobCapacityError):
        return McpErrorPayload(
            code=McpErrorCode.capacity_exceeded,
            message=message,
            retryable=True,
        )
    if isinstance(exc, ValueError):
        lowered = message.lower()
        if "not found" in lowered:
            return McpErrorPayload(
                code=McpErrorCode.artifact_missing,
                message=message,
            )
        if "no claimgraph yet" in lowered or "requires completed" in lowered:
            return McpErrorPayload(
                code=McpErrorCode.precondition_failed,
                message=message,
            )
        return McpErrorPayload(
            code=McpErrorCode.invalid_input,
            message=message,
        )
    return McpErrorPayload(
        code=McpErrorCode.internal_error,
        message=message or exc.__class__.__name__,
    )


def _artifact_refs_from_result(operation: str, result: dict[str, Any]) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    project_id = str(result.get("project_id") or "")
    if project_id:
        refs.append(
            ArtifactRef(
                artifact_kind="project",
                artifact_id=project_id,
                project_id=project_id,
                uri=f"project://{project_id}",
            )
        )
    if operation in {"document.ingest", "claim.structure"}:
        claim_graph_id = str(
            result.get("claim_graph_id")
            or ((result.get("claim_graph") or {}).get("graph_id") or "")
        )
        if claim_graph_id:
            refs.append(
                ArtifactRef(
                    artifact_kind="claim_graph",
                    artifact_id=claim_graph_id,
                    project_id=project_id or None,
                    uri=f"claim-graph://{project_id}",
                )
            )
    if operation == "audit.run":
        claim_id = str(result.get("claim_id") or "")
        profile_id = str((result.get("profile") or {}).get("profile_id") or "")
        if profile_id:
            refs.append(
                ArtifactRef(
                    artifact_kind="assurance_profile",
                    artifact_id=profile_id,
                    project_id=project_id or None,
                    claim_id=claim_id or None,
                    uri=f"profile://{project_id}/{claim_id}",
                )
            )
        if claim_id:
            refs.append(
                ArtifactRef(
                    artifact_kind="audit_report",
                    artifact_id=f"audit.{claim_id}",
                    project_id=project_id or None,
                    claim_id=claim_id,
                    uri=f"audit-report://{project_id}/{claim_id}",
                )
            )
    if operation == "formalize.dual":
        claim_id = str(result.get("claim_id") or "")
        if claim_id:
            refs.append(
                ArtifactRef(
                    artifact_kind="formalization_workflow",
                    artifact_id=str((result.get("workflow") or {}).get("workflow_id") or ""),
                    project_id=project_id or None,
                    claim_id=claim_id,
                )
            )
    return [ref for ref in refs if ref.artifact_id]


def _profile_resource_envelope(
    project_id: str,
    claim_id: str,
    revision_id: str | None = None,
) -> dict[str, Any]:
    store = runtime.engine_api.claim_trace_service.repository.artifact_store
    canonical_claim_id = canonical_artifact_id(claim_id)
    profile = store.load_assurance_profile_for_claim(canonical_claim_id)
    payload = profile.model_dump(mode="json", exclude_none=True)
    profile_id = canonical_artifact_id(payload["profile_id"])
    revisions = store.list_revisions("assurance_profiles", profile_id)
    current_revision_id = str(revisions[-1]["revision_id"]) if revisions else None
    revision_status = "latest"
    if revision_id:
        revision_payload = store.load_revision("assurance_profiles", profile_id, revision_id)
        payload = dict(revision_payload["artifact"])
        revision_status = "latest" if revision_id == current_revision_id else "superseded"
    else:
        revision_id = current_revision_id
    uri = f"profile://{project_id}/{canonical_claim_id}"
    if revision_id and revision_id != current_revision_id:
        uri = f"{uri}/{revision_id}"
    return ArtifactResourceEnvelope(
        uri=uri,
        artifact_kind="assurance_profile",
        artifact_id=profile_id,
        revision_id=revision_id,
        current_revision_id=current_revision_id,
        revision_status=revision_status,
        project_id=project_id,
        claim_id=canonical_claim_id,
        payload=payload,
        meta={"read_only": True},
    ).model_dump(mode="json", exclude_none=True)


def _claim_graph_resource_envelope(
    project_id: str,
    revision_id: str | None = None,
) -> dict[str, Any]:
    project = runtime.engine_api.open_project(project_id)
    graph_id = project.claim_graph_id
    if not graph_id:
        raise ValueError(f"Project '{project_id}' has no ClaimGraph yet.")
    store = runtime.engine_api.claim_trace_service.repository.artifact_store
    revisions = store.list_revisions("claim_graphs", graph_id)
    current_revision_id = str(revisions[-1]["revision_id"]) if revisions else None
    payload = store.load_claim_graph(graph_id).model_dump(mode="json", exclude_none=True)
    revision_status = "latest"
    if revision_id:
        revision_payload = store.load_revision("claim_graphs", graph_id, revision_id)
        payload = dict(revision_payload["artifact"])
        revision_status = "latest" if revision_id == current_revision_id else "superseded"
    else:
        revision_id = current_revision_id
    uri = f"claim-graph://{project_id}"
    if revision_id and revision_id != current_revision_id:
        uri = f"{uri}/{revision_id}"
    return ArtifactResourceEnvelope(
        uri=uri,
        artifact_kind="claim_graph",
        artifact_id=graph_id,
        revision_id=revision_id,
        current_revision_id=current_revision_id,
        revision_status=revision_status,
        project_id=project_id,
        payload=payload,
        meta={"read_only": True},
    ).model_dump(mode="json", exclude_none=True)


def _audit_report_resource_envelope(project_id: str, claim_id: str) -> dict[str, Any]:
    store = runtime.engine_api.claim_trace_service.repository.artifact_store
    canonical_claim_id = canonical_artifact_id(claim_id)
    audit_events = [
        event
        for event in store.query_review_events(canonical_claim_id)
        if event.get("event_type") == "audit_workflow"
    ]
    if not audit_events:
        raise FileNotFoundError(
            f"Audit workflow event not found for claim:{canonical_claim_id}"
        )
    latest = audit_events[-1]
    return {
        "uri": f"audit-report://{project_id}/{canonical_claim_id}",
        "artifact_kind": "audit_report",
        "artifact_id": str(latest["artifact_id"]),
        "project_id": project_id,
        "claim_id": canonical_claim_id,
        "payload": latest,
        "meta": {"read_only": True},
    }


async def _start_job(
    *,
    operation: str,
    request_id: str,
    meta: dict[str, Any],
    coro_factory,
) -> dict[str, Any]:
    handle = runtime.jobs.start(
        operation=operation,
        request_id=request_id,
        meta=meta,
        coro_factory=coro_factory,
        error_mapper=_map_exception,
        artifact_ref_extractor=lambda result: _artifact_refs_from_result(operation, result),
    )
    log.info("[%s] queued %s as %s", request_id, operation, handle.job_id)
    return _tool_ok(
        request_id,
        {"job": handle.model_dump(mode="json", exclude_none=True)},
    )


@mcp.tool(name="project.create", structured_output=True)
def tool_project_create(name: str, domain: str, description: str = "") -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] project.create", request_id)
    try:
        project = runtime.engine_api.create_project(name=name, domain=domain, description=description)
        return _tool_ok(
            request_id,
            {
                "project": project.model_dump(mode="json", exclude_none=True),
                "resource_refs": [f"project://{project.project_id}"],
            },
        )
    except Exception as exc:
        log.exception("[%s] project.create failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="project.open", structured_output=True)
def tool_project_open(project_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] project.open %s", request_id, project_id)
    try:
        project = runtime.engine_api.open_project(project_id)
        data = project.model_dump(mode="json", exclude_none=True)
        refs = [f"project://{project_id}"]
        if project.claim_graph_id:
            refs.append(f"claim-graph://{project_id}")
        return _tool_ok(request_id, {"project": data, "resource_refs": refs})
    except Exception as exc:
        log.exception("[%s] project.open failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="project.list", structured_output=True)
def tool_project_list() -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] project.list", request_id)
    try:
        projects = runtime.engine_api.list_projects()
        return _tool_ok(
            request_id,
            {
                "projects": [
                    project.model_dump(mode="json", exclude_none=True)
                    for project in projects
                ]
            },
        )
    except Exception as exc:
        log.exception("[%s] project.list failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="document.ingest", structured_output=True)
async def tool_document_ingest(project_id: str, text: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] document.ingest %s", request_id, project_id)
    try:
        return await _start_job(
            operation="document.ingest",
            request_id=request_id,
            meta={"project_id": project_id},
            coro_factory=lambda: _run_document_ingest(project_id, text),
        )
    except Exception as exc:
        log.exception("[%s] document.ingest failed to queue", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="claim.structure", structured_output=True)
async def tool_claim_structure(project_id: str, user_input: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] claim.structure %s", request_id, project_id)
    try:
        return await _start_job(
            operation="claim.structure",
            request_id=request_id,
            meta={"project_id": project_id},
            coro_factory=lambda: _run_claim_structure(project_id, user_input),
        )
    except Exception as exc:
        log.exception("[%s] claim.structure failed to queue", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="formalize.dual", structured_output=True)
async def tool_formalize_dual(project_id: str, claim_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] formalize.dual %s %s", request_id, project_id, claim_id)
    try:
        return await _start_job(
            operation="formalize.dual",
            request_id=request_id,
            meta={"project_id": project_id, "claim_id": claim_id},
            coro_factory=lambda: _run_formalize_dual(project_id, claim_id),
        )
    except Exception as exc:
        log.exception("[%s] formalize.dual failed to queue", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="audit.run", structured_output=True)
async def tool_audit_run(project_id: str, claim_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] audit.run %s %s", request_id, project_id, claim_id)
    try:
        return await _start_job(
            operation="audit.run",
            request_id=request_id,
            meta={"project_id": project_id, "claim_id": claim_id},
            coro_factory=lambda: _run_audit(project_id, claim_id),
        )
    except Exception as exc:
        log.exception("[%s] audit.run failed to queue", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="profile.recompute", structured_output=True)
def tool_profile_recompute(
    project_id: str,
    claim_id: str,
    audit_job_id: str,
    research_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] profile.recompute %s %s", request_id, project_id, claim_id)
    try:
        job = runtime.jobs.get(audit_job_id)
        if job.status != "completed" or not job.result:
            raise ValueError(
                f"Profile recompute requires completed audit job, found {job.status}."
            )
        result = runtime.engine_api.recompute_profile(
            project_id=project_id,
            claim_id=claim_id,
            audit_result=job.result,
            research_output=research_output,
        )
        data = result.model_dump(mode="json", exclude_none=True)
        return _tool_ok(
            request_id,
            {
                "profile": data,
                "resource_refs": [f"profile://{project_id}/{claim_id}"],
            },
        )
    except Exception as exc:
        log.exception("[%s] profile.recompute failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="promotion.transition", structured_output=True)
def tool_promotion_transition(
    project_id: str,
    claim_id: str,
    target_gate: str,
    actor: str,
    actor_role: str,
    override: bool = False,
    rationale: str = "",
    notes: str = "",
) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] promotion.transition %s %s", request_id, project_id, claim_id)
    try:
        state = runtime.engine_api.approve_promotion(
            project_id=project_id,
            claim_id=claim_id,
            target_gate=target_gate,
            actor=actor,
            actor_role=actor_role,
            override=override,
            rationale=rationale,
            notes=notes,
        )
        return _tool_ok(
            request_id,
            {
                "promotion_state": state.model_dump(mode="json", exclude_none=True),
                "actor": actor,
                "actor_role": actor_role,
                "rationale": rationale,
                "notes": notes,
                "resource_refs": [f"profile://{project_id}/{claim_id}"],
            },
        )
    except Exception as exc:
        log.exception("[%s] promotion.transition failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="bundle.export", structured_output=True)
def tool_bundle_export(project_id: str) -> dict[str, Any]:
    """Export a legacy project bundle (compatibility-only; prefer trace.export for v2 artifacts)."""
    request_id = _request_id()
    log.info("[%s] bundle.export %s", request_id, project_id)
    try:
        bundle = runtime.engine_api.export_bundle(project_id)
        data = bundle.model_dump(mode="json", exclude_none=True)
        return _tool_ok(
            request_id,
            {
                "bundle": data,
                "resource_refs": [
                    f"bundle://{project_id}",
                    f"project://{project_id}",
                    f"claim-graph://{project_id}",
                ],
            },
        )
    except Exception as exc:
        log.exception("[%s] bundle.export failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="trace.export", structured_output=True)
def tool_trace_export(project_id: str, output_dir: str = "") -> dict[str, Any]:
    """Export trace.json + transition_log.jsonl + sidecar_meta.json to a directory.

    Returns paths to the three files plus a validation result.
    """
    request_id = _request_id()
    log.info("[%s] trace.export %s", request_id, project_id)
    try:
        resolved_output_dir = output_dir or str(
            Path(runtime.data_dir) / "exports" / project_id
        )
        result = runtime.engine_api.export_trace(
            project_id=project_id,
            output_dir=resolved_output_dir,
        )
        data = result.model_dump(mode="json", exclude_none=True)
        return _tool_ok(
            request_id,
            {
                "export": data,
                "resource_refs": [
                    f"trace://{project_id}",
                    f"transition-log://{project_id}",
                    f"sidecar://{project_id}",
                ],
            },
        )
    except Exception as exc:
        log.exception("[%s] trace.export failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="prefix.extract", structured_output=True)
def tool_prefix_extract(
    project_id: str,
    output_path: str = "",
    format: str = "jsonl",
) -> dict[str, Any]:
    """Extract PrefixSlice samples from a project's trace.

    Returns number of slices extracted and output path.
    """
    request_id = _request_id()
    log.info("[%s] prefix.extract %s", request_id, project_id)
    try:
        result = runtime.engine_api.export_prefix_slices(
            project_id=project_id,
            output_path=output_path or None,
            format=format,
        )
        data = result.model_dump(mode="json", exclude_none=True)
        return _tool_ok(
            request_id,
            {
                "prefix_slices": data,
            },
        )
    except Exception as exc:
        log.exception("[%s] prefix.extract failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="job.get", structured_output=True)
def tool_job_get(job_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] job.get %s", request_id, job_id)
    try:
        try:
            job = runtime.jobs.get(job_id).model_dump(mode="json", exclude_none=True)
        except JobNotFoundError:
            job = runtime.proof_control.get_job(job_id)
        return _tool_ok(
            request_id,
            {"job": job},
        )
    except Exception as exc:
        log.exception("[%s] job.get failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="job.cancel", structured_output=True)
def tool_job_cancel(job_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] job.cancel %s", request_id, job_id)
    try:
        job = runtime.proof_control.cancel_job(job_id)
        return _tool_ok(request_id, {"job": job})
    except Exception as exc:
        log.exception("[%s] job.cancel failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="job.kill", structured_output=True)
def tool_job_kill(job_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] job.kill %s", request_id, job_id)
    try:
        job = runtime.proof_control.kill_job(job_id)
        return _tool_ok(request_id, {"job": job})
    except Exception as exc:
        log.exception("[%s] job.kill failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="proof.run.start", structured_output=True)
def tool_proof_run_start(
    session_name: str,
    session_dir: str,
    theory_path: str = "",
    target_theory: str = "",
    target_theorem: str = "",
    label: str = "",
    wall_timeout_seconds: int = 600,
    idle_timeout_seconds: int = 120,
    cancel_grace_seconds: int = 5,
) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] proof.run.start %s", request_id, session_name)
    try:
        job = runtime.proof_control.start_job(
            session_name=session_name,
            session_dir=session_dir,
            run_kind="build",
            theory_path=theory_path or None,
            target_theory=target_theory or None,
            target_theorem=target_theorem or None,
            label=label,
            wall_timeout_seconds=wall_timeout_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            cancel_grace_seconds=cancel_grace_seconds,
        )
        return _tool_ok(request_id, {"job": job})
    except Exception as exc:
        log.exception("[%s] proof.run.start failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="claim.trace_forward", structured_output=True)
async def tool_trace_forward(project_id: str, claim_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] claim.trace_forward %s %s", request_id, project_id, claim_id)
    try:
        return await _start_job(
            operation="claim.trace_forward",
            request_id=request_id,
            meta={"project_id": project_id, "claim_id": claim_id},
            coro_factory=lambda: runtime.engine_api.trace_forward(project_id, claim_id),
        )
    except Exception as exc:
        log.exception("[%s] claim.trace_forward failed to queue", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="claim.trace_backward", structured_output=True)
async def tool_trace_backward(project_id: str, claim_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] claim.trace_backward %s %s", request_id, project_id, claim_id)
    try:
        return await _start_job(
            operation="claim.trace_backward",
            request_id=request_id,
            meta={"project_id": project_id, "claim_id": claim_id},
            coro_factory=lambda: runtime.engine_api.trace_backward(project_id, claim_id),
        )
    except Exception as exc:
        log.exception("[%s] claim.trace_backward failed to queue", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="graph.detect_gaps", structured_output=True)
async def tool_detect_gaps(project_id: str) -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] graph.detect_gaps %s", request_id, project_id)
    try:
        return await _start_job(
            operation="graph.detect_gaps",
            request_id=request_id,
            meta={"project_id": project_id},
            coro_factory=lambda: runtime.engine_api.detect_gaps(project_id),
        )
    except Exception as exc:
        log.exception("[%s] graph.detect_gaps failed to queue", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="claim.assess", structured_output=True)
async def tool_assess(project_id: str, claim_id: str = "") -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] claim.assess %s %s", request_id, project_id, claim_id)
    try:
        return await _start_job(
            operation="claim.assess",
            request_id=request_id,
            meta={"project_id": project_id, "claim_id": claim_id},
            coro_factory=lambda: runtime.engine_api.assess_soundness(
                project_id,
                claim_id or None,
            ),
        )
    except Exception as exc:
        log.exception("[%s] claim.assess failed to queue", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.tool(name="graph.export", structured_output=True)
def tool_graph_export(project_id: str, format: str = "json") -> dict[str, Any]:
    request_id = _request_id()
    log.info("[%s] graph.export %s %s", request_id, project_id, format)
    try:
        payload = runtime.engine_api.export_graph(project_id, format)
        return _tool_ok(
            request_id,
            {"graph_export": payload, "format": format, "project_id": project_id},
        )
    except Exception as exc:
        log.exception("[%s] graph.export failed", request_id)
        return _tool_error(request_id, _map_exception(exc))


@mcp.resource("project://{project_id}")
def resource_project(project_id: str) -> dict[str, Any]:
    project = runtime.engine_api.open_project(project_id)
    return ProjectResourceEnvelope(
        uri=f"project://{project_id}",
        project_id=project_id,
        payload=project.model_dump(mode="json", exclude_none=True),
        meta={"read_only": True},
    ).model_dump(mode="json", exclude_none=True)


@mcp.resource("claim-graph://{project_id}")
def resource_claim_graph_latest(project_id: str) -> dict[str, Any]:
    return _claim_graph_resource_envelope(project_id)


@mcp.resource("claim-graph://{project_id}/{revision_id}")
def resource_claim_graph_revision(project_id: str, revision_id: str) -> dict[str, Any]:
    return _claim_graph_resource_envelope(project_id, revision_id)


@mcp.resource("profile://{project_id}/{claim_id}")
def resource_profile_latest(project_id: str, claim_id: str) -> dict[str, Any]:
    return _profile_resource_envelope(project_id, claim_id)


@mcp.resource("profile://{project_id}/{claim_id}/{revision_id}")
def resource_profile_revision(
    project_id: str,
    claim_id: str,
    revision_id: str,
) -> dict[str, Any]:
    return _profile_resource_envelope(project_id, claim_id, revision_id)


@mcp.resource("audit-report://{project_id}/{claim_id}")
def resource_audit_report(project_id: str, claim_id: str) -> dict[str, Any]:
    return _audit_report_resource_envelope(project_id, claim_id)


@mcp.resource("bundle://{project_id}")
def resource_bundle(project_id: str) -> dict[str, Any]:
    bundle = runtime.engine_api.export_bundle(project_id)
    return {
        "uri": f"bundle://{project_id}",
        "project_id": project_id,
        "payload": bundle.model_dump(mode="json", exclude_none=True),
        "meta": {"read_only": True},
    }


@mcp.resource("trace://{project_id}")
def resource_trace(project_id: str) -> dict[str, Any]:
    """Return the PipelineTraceV1 JSON (model-safe, no source_domain)."""
    trace_data = runtime.engine_api.get_trace_data(project_id)
    return {
        "uri": f"trace://{project_id}",
        "project_id": project_id,
        "payload": trace_data,
        "meta": {"read_only": True, "model_safe": True},
    }


@mcp.resource("transition-log://{project_id}")
def resource_transition_log(project_id: str) -> dict[str, Any]:
    """Return the transition_log.jsonl content."""
    log_content = runtime.engine_api.get_transition_log(project_id)
    return {
        "uri": f"transition-log://{project_id}",
        "project_id": project_id,
        "payload": {"content": log_content, "format": "jsonl"},
        "meta": {"read_only": True},
    }


@mcp.resource("sidecar://{project_id}")
def resource_sidecar(project_id: str) -> dict[str, Any]:
    """Return sidecar_meta.json (operator-only, NOT for model consumption).

    WARNING: This resource contains operator-only metadata.
    It MUST NOT be fed into model contexts or used as training data.
    """
    sidecar_data = runtime.engine_api.get_sidecar_meta(project_id)
    return {
        "uri": f"sidecar://{project_id}",
        "project_id": project_id,
        "payload": sidecar_data,
        "meta": {
            "read_only": True,
            "operator_only": True,
            "warning": "OPERATOR-ONLY. Do NOT feed this to model contexts.",
        },
    }


async def _run_document_ingest(project_id: str, text: str) -> dict[str, Any]:
    result = await runtime.engine_api.ingest_document(project_id, text)
    return result.model_dump(mode="json", exclude_none=True)


async def _run_claim_structure(project_id: str, user_input: str) -> dict[str, Any]:
    result = await runtime.engine_api.run_claim_structuring(project_id, user_input)
    return result.model_dump(mode="json", exclude_none=True)


async def _run_formalize_dual(project_id: str, claim_id: str) -> dict[str, Any]:
    result = await runtime.engine_api.run_dual_formalization(project_id, claim_id)
    return result.model_dump(mode="json", exclude_none=True)


async def _run_audit(project_id: str, claim_id: str) -> dict[str, Any]:
    result = await runtime.engine_api.run_audit(project_id, claim_id)
    return result.model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# Compatibility wrappers for existing local callers.
# These are not the canonical MCP tool surface.
# ---------------------------------------------------------------------------


def create_project(name: str, domain: str, description: str = "") -> str:
    project = runtime.engine_api.create_project(name=name, domain=domain, description=description)
    return json.dumps(
        {
            "project_id": project.project_id,
            "name": project.name,
            "domain": project.domain,
            "message": "Project created. Use document.ingest or claim.structure next.",
        },
        indent=2,
    )


def list_projects() -> str:
    projects = runtime.engine_api.list_projects()
    return json.dumps(
        [project.model_dump(mode="json", exclude_none=True) for project in projects],
        indent=2,
    )


async def ingest(project_id: str, text: str) -> str:
    result = await runtime.engine_api.ingest_document(project_id, text)
    summary = runtime.engine_api.get_summary(project_id)
    summary.update(result.model_dump(mode="json", exclude_none=True))
    summary["project_id"] = project_id
    return json.dumps(summary, indent=2, default=str)


def get_graph(project_id: str, depth_filter: int = -1) -> str:
    return json.dumps(runtime.engine_api.get_graph(project_id, depth_filter), indent=2, default=str)


def get_summary(project_id: str) -> str:
    return json.dumps(runtime.engine_api.get_summary(project_id), indent=2, default=str)


def export_graph(project_id: str, format: str = "json") -> str:
    return runtime.engine_api.export_graph(project_id, format)


def add_claim(
    project_id: str,
    title: str,
    statement: str,
    role: str,
    source_location: str = "",
    scope: str = "",
    depth: int = 0,
) -> str:
    return json.dumps(
        runtime.engine_api.claim_trace_service.add_claim(
            project_id=project_id,
            title=title,
            statement=statement,
            role=role,
            source_location=source_location,
            scope=scope,
            depth=depth,
        ),
        indent=2,
        default=str,
    )


def get_claim(project_id: str, claim_id: str) -> str:
    return json.dumps(
        runtime.engine_api.claim_trace_service.get_claim(project_id, claim_id),
        indent=2,
        default=str,
    )


def get_axioms(project_id: str) -> str:
    return json.dumps(
        runtime.engine_api.claim_trace_service.get_axioms(project_id),
        indent=2,
        default=str,
    )


def get_conclusions(project_id: str) -> str:
    return json.dumps(
        runtime.engine_api.claim_trace_service.get_conclusions(project_id),
        indent=2,
        default=str,
    )


async def trace_forward_from(project_id: str, claim_id: str) -> str:
    return json.dumps(
        await runtime.engine_api.trace_forward(project_id, claim_id),
        indent=2,
        default=str,
    )


async def trace_backward_from(project_id: str, claim_id: str) -> str:
    return json.dumps(
        await runtime.engine_api.trace_backward(project_id, claim_id),
        indent=2,
        default=str,
    )


async def detect_gaps(project_id: str) -> str:
    return json.dumps(
        await runtime.engine_api.detect_gaps(project_id),
        indent=2,
        default=str,
    )


async def assess(project_id: str, claim_id: str = "") -> str:
    return json.dumps(
        await runtime.engine_api.assess_soundness(project_id, claim_id or None),
        indent=2,
        default=str,
    )


def link_claims(
    project_id: str,
    source_id: str,
    target_id: str,
    relation_type: str,
    strength: str = "unknown",
    rationale: str = "",
) -> str:
    return json.dumps(
        runtime.engine_api.claim_trace_service.link_claims(
            project_id=project_id,
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            strength=strength,
            rationale=rationale,
        ),
        indent=2,
        default=str,
    )


def challenge_claim(project_id: str, claim_id: str, reason: str) -> str:
    return json.dumps(
        runtime.engine_api.claim_trace_service.challenge_claim(
            project_id=project_id,
            claim_id=claim_id,
            reason=reason,
        ),
        indent=2,
        default=str,
    )


def main() -> None:
    mcp.run()


__all__ = [
    "add_claim",
    "assess",
    "challenge_claim",
    "create_project",
    "detect_gaps",
    "export_graph",
    "get_axioms",
    "get_claim",
    "get_conclusions",
    "get_graph",
    "get_summary",
    "ingest",
    "link_claims",
    "list_projects",
    "main",
    "mcp",
    "reset_runtime",
    "runtime",
    "trace_backward_from",
    "trace_forward_from",
]
