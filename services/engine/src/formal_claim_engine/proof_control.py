"""Generic proof-run control plane over the FWP client seam."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .config import PipelineConfig
from .proof_protocol import (
    ACTIVE_PROOF_STATUSES,
    TERMINAL_PROOF_STATUSES,
    _collect_artifact_payloads,
    _normalize_run_status,
    _parse_iso8601,
    _termination_reason,
    create_fwp_client,
    create_fwp_workspace_inputs,
)


def _theory_paths_for_session(session_dir: str) -> list[str]:
    root = Path(session_dir).resolve()
    paths = []
    for suffix in ("*.lean", "*.thy", "*.v"):
        paths.extend(path.resolve() for path in sorted(root.rglob(suffix)))
    return [str(path) for path in paths]


@dataclass
class _ProofJobRecord:
    job_id: str
    session_name: str
    session_dir: str
    run_kind: str
    created_at: str
    target_theory: str | None = None
    target_theorem: str | None = None
    theory_path: str | None = None
    label: str = ""
    workspace_ref: str | None = None
    meta: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.meta = dict(self.meta or {})


class ProofControlPlane:
    """Workspace/artifact/run control over the FWP client."""

    def __init__(
        self,
        *,
        config: PipelineConfig | None = None,
        data_dir: str | None = None,
    ) -> None:
        base_config = config or PipelineConfig()
        if data_dir is not None:
            base_config = replace(base_config, data_dir=data_dir)
        self.config = base_config
        self.client, self.symbols = create_fwp_client(self.config.proof_protocol)
        self.job_dir = Path(self.config.data_dir) / "proof_jobs"
        self.job_dir.mkdir(parents=True, exist_ok=True)

    def start_job(
        self,
        *,
        session_name: str,
        session_dir: str,
        run_kind: str = "build",
        theory_path: str | None = None,
        target_theory: str | None = None,
        target_theorem: str | None = None,
        label: str = "",
        wall_timeout_seconds: int | None = None,
        idle_timeout_seconds: int | None = None,
        cancel_grace_seconds: int | None = None,
    ) -> dict[str, Any]:
        resolved_session_dir = str(Path(session_dir).resolve())
        target_theory = target_theory or Path(theory_path or "").stem or session_name
        target_theorem = target_theorem or "demo"
        budget = self.config.proof_protocol.budget
        proof_source = ""
        if theory_path:
            proof_source = Path(theory_path).resolve().read_text(encoding="utf-8")
        request = self.symbols.ProofBuildRequest(
            request_id=(
                "proof-job."
                + sha256(
                    f"{resolved_session_dir}:{target_theory}:{target_theorem}:{run_kind}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:12]
            ),
            project_id=self.config.project_id,
            subject_id=f"proof-job.{Path(resolved_session_dir).name}",
            subject_revision_id=f"claim-graph://{self.config.project_id}/adhoc",
            artifact_ref=f"workspace://{resolved_session_dir}",
            proof_source=proof_source,
            theorem_statement=target_theorem,
            target_backend=self.config.proof_protocol.target_backend_id,
            workspace_inputs=create_fwp_workspace_inputs(
                self.symbols,
                session_dir=resolved_session_dir,
                session_name=session_name,
                target_theory=target_theory,
                target_theorem=target_theorem,
            ),
            resource_policy={
                "wall_ms": max(
                    1,
                    int((wall_timeout_seconds or budget.wall_timeout_seconds) * 1000),
                ),
                "idle_ms": max(
                    1,
                    int((idle_timeout_seconds or budget.idle_timeout_seconds) * 1000),
                ),
                "cancel_grace_ms": max(
                    1,
                    int((cancel_grace_seconds or budget.cancel_grace_seconds) * 1000),
                ),
                "max_rss_mb": max(1, int(budget.max_rss_mb)),
                "max_output_bytes": max(1, int(budget.max_output_bytes)),
                "max_diag_count": max(1, int(budget.max_diag_count)),
                "max_children": max(0, int(budget.max_children)),
                "max_restarts": max(0, int(budget.max_restarts)),
            },
            lineage={
                "requestOrigin": "formal-claim-proof-control",
                "sessionDir": resolved_session_dir,
                "label": label,
            },
            run_kind=run_kind,
        )
        status = self.client.submit_formalization_check(request)
        record = _ProofJobRecord(
            job_id=status.job_id,
            session_name=session_name,
            session_dir=resolved_session_dir,
            run_kind=run_kind,
            created_at=status.started_at or "",
            target_theory=target_theory,
            target_theorem=target_theorem,
            theory_path=str(Path(theory_path).resolve()) if theory_path else None,
            label=label,
            workspace_ref=status.workspace_ref,
            meta={
                "started_from": "formal-claim",
                "label": label,
                "last_status": self._status_to_meta(status),
            },
        )
        self._save_record(record)
        return self._render_job(status, record)

    def get_job(self, job_id: str) -> dict[str, Any]:
        record = self._load_record(job_id)
        self._seed_job_context(record)
        pending_kill_after = record.meta.get("pending_kill_after")
        if isinstance(pending_kill_after, (int, float)):
            if time.time() >= float(pending_kill_after):
                status = self.client.kill_job(job_id)
                record.meta.pop("pending_kill_after", None)
                record.meta["last_status"] = self._status_to_meta(status)
                self._save_record(record)
                return self._render_job(status, record, control_action="kill")
            status = self._status_from_meta(record)
        else:
            status = self.client.get_job(job_id)
            record.meta["last_status"] = self._status_to_meta(status)
            self._save_record(record)
        if not record.workspace_ref and status.workspace_ref:
            record.workspace_ref = status.workspace_ref
            self._save_record(record)
        return self._render_job(status, record)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        record = self._load_record(job_id)
        self._seed_job_context(record)
        status = self.client.cancel_job(job_id)
        signal_kinds = list(status.diagnostic_summary.get("signalKinds") or [])
        if "abort.escalation_pending" in signal_kinds:
            budget = dict(status.resource_usage.get("budget") or {})
            grace_seconds = max(
                0.0,
                float(
                    budget.get(
                        "cancel_grace_ms",
                        self.config.proof_protocol.budget.cancel_grace_seconds * 1000,
                    )
                )
                / 1000.0,
            )
            record.meta["pending_kill_after"] = time.time() + grace_seconds
            record.meta["last_status"] = self._status_to_meta(status)
            self._save_record(record)
        else:
            record.meta.pop("pending_kill_after", None)
            record.meta["last_status"] = self._status_to_meta(status)
            self._save_record(record)
        return self._render_job(status, record, control_action="cancel")

    def kill_job(self, job_id: str) -> dict[str, Any]:
        record = self._load_record(job_id)
        self._seed_job_context(record)
        status = self.client.kill_job(job_id)
        record.meta.pop("pending_kill_after", None)
        record.meta["last_status"] = self._status_to_meta(status)
        self._save_record(record)
        return self._render_job(status, record, control_action="kill")

    def _record_path(self, job_id: str) -> Path:
        return self.job_dir / f"{job_id}.json"

    def _save_record(self, record: _ProofJobRecord) -> None:
        self._record_path(record.job_id).write_text(
            json.dumps(asdict(record), indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    def _load_record(self, job_id: str) -> _ProofJobRecord:
        path = self._record_path(job_id)
        if not path.exists():
            raise FileNotFoundError(f"Proof job '{job_id}' was not found.")
        return _ProofJobRecord(**json.loads(path.read_text(encoding="utf-8")))

    def _seed_job_context(self, record: _ProofJobRecord) -> None:
        if not record.workspace_ref:
            return
        self.client.job_context[record.job_id] = {
            "workspaceRef": record.workspace_ref,
            "lineage": {
                "requestOrigin": "formal-claim-proof-control",
                "sessionDir": record.session_dir,
            },
            "artifactRef": f"workspace://{record.session_dir}",
        }

    def _status_to_meta(self, status: Any) -> dict[str, Any]:
        return {
            "job_id": status.job_id,
            "status": status.status,
            "started_at": status.started_at,
            "completed_at": status.completed_at,
            "workspace_ref": status.workspace_ref,
            "diagnostic_summary": dict(status.diagnostic_summary or {}),
            "artifact_refs": list(status.artifact_refs or []),
            "lineage": dict(status.lineage or {}),
            "resource_usage": dict(status.resource_usage or {}),
            "backend_extensions": dict(status.backend_extensions or {}),
        }

    def _status_from_meta(self, record: _ProofJobRecord) -> Any:
        payload = dict(record.meta.get("last_status") or {})
        if not payload:
            return self.client.get_job(record.job_id)
        return SimpleNamespace(
            job_id=payload.get("job_id", record.job_id),
            status=payload.get("status", "running"),
            started_at=payload.get("started_at"),
            completed_at=payload.get("completed_at"),
            workspace_ref=payload.get("workspace_ref", record.workspace_ref),
            diagnostic_summary=dict(payload.get("diagnostic_summary") or {}),
            artifact_refs=list(payload.get("artifact_refs") or []),
            lineage=dict(payload.get("lineage") or {}),
            resource_usage=dict(payload.get("resource_usage") or {}),
            backend_extensions=dict(payload.get("backend_extensions") or {}),
        )

    def _render_job(
        self,
        status: Any,
        record: _ProofJobRecord,
        *,
        control_action: str | None = None,
    ) -> dict[str, Any]:
        workspace_ref = status.workspace_ref or record.workspace_ref or ""
        if workspace_ref and record.workspace_ref != workspace_ref:
            record.workspace_ref = workspace_ref
            self._save_record(record)
        signal_kinds = list(status.diagnostic_summary.get("signalKinds") or [])
        normalized_status = _normalize_run_status(status.status, signal_kinds=signal_kinds)
        stdout_tail = ""
        artifact_paths = {
            str(item.get("artifactId") or ""): str(item.get("uri") or "")
            for item in list(status.artifact_refs or [])
            if item.get("artifactId")
        }
        if workspace_ref and artifact_paths:
            stdout_tail, extra_artifacts = _collect_artifact_payloads(
                self.client,
                workspace_ref,
                max_bytes=self.config.proof_protocol.budget.max_output_bytes,
            )
            artifact_paths.update(extra_artifacts)
        started_at = _parse_iso8601(status.started_at)
        completed_at = _parse_iso8601(status.completed_at)
        runtime_seconds = 0.0
        if started_at is not None and completed_at is not None:
            runtime_seconds = max(0.0, completed_at - started_at)
        fingerprint = sha256(
            json.dumps(
                {
                    "workspace_ref": workspace_ref,
                    "session_name": record.session_name,
                    "target_theory": record.target_theory,
                    "target_theorem": record.target_theorem,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        payload = {
            "job_id": status.job_id,
            "run_kind": record.run_kind,
            "status": normalized_status,
            "session_name": record.session_name,
            "session_dir": record.session_dir,
            "created_at": record.created_at,
            "started_at": status.started_at,
            "completed_at": status.completed_at,
            "theory_path": record.theory_path,
            "target_theory": record.target_theory,
            "target_theorem": record.target_theorem,
            "command": [
                "fwp-client",
                self.config.proof_protocol.transport,
                self.config.proof_protocol.target_backend_id,
                record.target_theory or "",
                record.target_theorem or "",
            ],
            "stdout_path": None,
            "stderr_path": None,
            "stdout_tail": stdout_tail,
            "stderr_tail": "",
            "session_fingerprint": fingerprint,
            "artifact_paths": artifact_paths,
            "theory_paths": _theory_paths_for_session(record.session_dir),
            "exit_code": 0 if normalized_status == "completed" else None,
            "runtime_seconds": runtime_seconds,
            "idle_seconds": 0.0,
            "control_action": control_action,
            "termination_reason": _termination_reason(status.status, signal_kinds),
            "failure_message": None
            if normalized_status in ACTIVE_PROOF_STATUSES | {"completed"}
            else (_termination_reason(status.status, signal_kinds) or status.status),
            "result": {
                "workspace_ref": workspace_ref,
                "artifact_refs": list(status.artifact_refs or []),
                "diagnostic_summary": dict(status.diagnostic_summary or {}),
                "lineage": dict(status.lineage or {}),
                "resource_usage": dict(status.resource_usage or {}),
                "backend_extensions": dict(status.backend_extensions or {}),
            },
            "meta": dict(record.meta or {}),
        }
        return payload


__all__ = ["ProofControlPlane"]
