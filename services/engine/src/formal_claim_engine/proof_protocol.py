"""Backend-neutral proof seam for engine-facing proof execution."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from .config import PipelineConfig, ProofProtocolConfig, REPO_ROOT

TERMINAL_PROOF_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "killed",
    "timed_out",
}
ACTIVE_PROOF_STATUSES = {
    "queued",
    "running",
    "cancel_requested",
}


@runtime_checkable
class ProofBuilder(Protocol):
    """Filesystem fixture build surface for tests and scenario replay."""

    def write_theory(self, session_dir: str, theory_name: str, content: str) -> Path:
        ...

    def write_root(
        self,
        session_dir: str,
        session_name: str,
        theories: list[str],
    ) -> Path:
        ...

    def build(self, session_name: str, session_dir: str) -> Any:
        ...


@runtime_checkable
class ProofAuditExecutor(Protocol):
    """Filesystem fixture audit surface for tests and scenario replay."""

    def run_audit(self, request_path: Path) -> dict[str, Any]:
        ...


@runtime_checkable
class ProofProtocolClient(Protocol):
    """Engine-facing proof execution contract."""

    backend_id: str

    def prepare_theory_session(
        self,
        *,
        session_dir: str,
        session_name: str,
        theory_name: str,
        theory_body: str,
        theorem_statement: str | None = None,
        subject_id: str | None = None,
    ) -> None:
        ...

    def build_session(
        self,
        *,
        session_name: str,
        session_dir: str,
        target_theory: str,
        target_theorem: str,
        subject_id: str | None = None,
    ) -> Any:
        ...

    def run_audit(self, request_path: Path) -> dict[str, Any]:
        ...


@dataclass
class BuildSessionResult:
    success: bool
    stdout: str
    stderr: str
    return_code: int
    sorry_count: int = 0
    oops_count: int = 0
    sorry_locations: list[str] = None  # type: ignore[assignment]
    theorems: list[str] = None  # type: ignore[assignment]
    definitions: list[str] = None  # type: ignore[assignment]
    locales: list[str] = None  # type: ignore[assignment]
    session_fingerprint: str | None = None
    timeout_classification: str = "completed"
    duration_seconds: float = 0.0
    command: list[str] = None  # type: ignore[assignment]
    workspace_dir: str | None = None
    session_dir: str | None = None
    root_path: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    artifact_paths: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.sorry_locations = list(self.sorry_locations or [])
        self.theorems = list(self.theorems or [])
        self.definitions = list(self.definitions or [])
        self.locales = list(self.locales or [])
        self.command = list(self.command or [])
        self.artifact_paths = dict(self.artifact_paths or {})


@dataclass
class _PreparedTheorySession:
    subject_id: str | None
    session_name: str
    theory_name: str
    theory_body: str
    theorem_statement: str | None = None


@dataclass(frozen=True)
class _FwpSymbols:
    ProofProtocolClient: type
    ProofProtocolClientError: type
    ProofWorkspaceInputs: type
    WorkspaceDocumentInput: type
    ProofBuildRequest: type
    ProofAuditRequest: type
    LocalHubTransport: type
    HttpHubTransport: type
    build_reference_hub: Callable[[], Any] | None


def _language_id_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".thy":
        return "isabelle"
    if suffix == ".lean":
        return "lean"
    if suffix == ".v":
        return "coq"
    return "plain_text"


def _workspace_documents_for(session_dir: str | Path) -> list[Path]:
    root = Path(session_dir)
    documents: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".thy", ".lean", ".v"}:
            documents.append(path)
    return documents


def _read_theory_body(
    session_dir: str | Path,
    target_theory: str,
    prepared_session: _PreparedTheorySession | None,
) -> str:
    if prepared_session is not None:
        return prepared_session.theory_body
    root = Path(session_dir)
    for suffix in (".lean", ".thy", ".v"):
        candidate = root / f"{target_theory}{suffix}"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    for candidate in _workspace_documents_for(root):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"Proof source for '{target_theory}' was not prepared in memory and no source file exists in {root}."
    )


def _resource_policy_payload(config: ProofProtocolConfig) -> dict[str, int]:
    budget = config.budget
    return {
        "wall_ms": max(1, int(budget.wall_timeout_seconds * 1000)),
        "idle_ms": max(1, int(budget.idle_timeout_seconds * 1000)),
        "cancel_grace_ms": max(1, int(budget.cancel_grace_seconds * 1000)),
        "max_rss_mb": max(1, int(budget.max_rss_mb)),
        "max_output_bytes": max(1, int(budget.max_output_bytes)),
        "max_diag_count": max(1, int(budget.max_diag_count)),
        "max_children": max(0, int(budget.max_children)),
        "max_restarts": max(0, int(budget.max_restarts)),
    }


def _parse_iso8601(value: str | None) -> float | None:
    if not value:
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        return time.mktime(time.strptime(candidate[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _normalize_run_status(raw_status: str, *, signal_kinds: list[str]) -> str:
    if raw_status == "completed":
        return "completed"
    if raw_status == "running":
        if "abort.escalation_pending" in signal_kinds:
            return "cancel_requested"
        return "running"
    if raw_status == "aborted.user_requested":
        return "cancelled"
    if raw_status == "killed":
        return "killed"
    if raw_status.startswith("timeout."):
        return "timed_out"
    return "failed"


def _termination_reason(raw_status: str, signal_kinds: list[str]) -> str | None:
    if raw_status == "running" and "abort.escalation_pending" in signal_kinds:
        return None
    if raw_status == "aborted.user_requested":
        return "cancelled"
    if raw_status == "killed":
        return "kill_requested"
    if raw_status == "timeout.wall":
        return "wall_timeout"
    if raw_status == "timeout.idle":
        return "idle_timeout"
    if signal_kinds:
        return signal_kinds[0]
    return raw_status if raw_status != "completed" else None


def _normalize_probe_kind(kind: str | None) -> str:
    normalized = str(kind or "").strip()
    return {
        "nitpick": "counterexample",
        "sledgehammer": "proofSearch",
    }.get(normalized, normalized)


def _normalize_counterexample_outcome(outcome: str) -> str:
    return {
        "found": "countermodel_found",
        "none": "no_countermodel_found",
    }.get(outcome, outcome)


def _normalize_probe_entry(
    payload: dict[str, Any],
    *,
    fallback_kind: str | None,
    session_name: str,
    target_theorem: str,
) -> dict[str, Any] | None:
    probe_kind = _normalize_probe_kind(payload.get("kind") or fallback_kind)
    if not probe_kind:
        return None
    outcome = str(payload.get("outcome") or payload.get("status") or "untested")
    if probe_kind == "counterexample":
        outcome = _normalize_counterexample_outcome(outcome)
    normalized: dict[str, Any] = {
        "kind": probe_kind,
        "session": str(payload.get("session") or f"{session_name}_{probe_kind.lower()}"),
        "target_theorem": str(payload.get("target_theorem") or target_theorem),
        "outcome": outcome,
        "summary": str(payload.get("summary") or ""),
    }
    if probe_kind == "proofSearch":
        hints = [
            str(item)
            for item in list(payload.get("hints") or payload.get("steps") or [])
            if str(item)
        ]
        if hints:
            normalized["hints"] = hints
    return normalized


def _default_probe_entry(
    *,
    kind: str,
    session_name: str,
    target_theorem: str,
) -> dict[str, Any]:
    if kind == "counterexample":
        return {
            "kind": "counterexample",
            "session": f"{session_name}_counterexample",
            "target_theorem": target_theorem,
            "outcome": "untested",
            "summary": "No normalized counterexample probe result was returned.",
        }
    if kind == "proofSearch":
        return {
            "kind": "proofSearch",
            "session": f"{session_name}_proofsearch",
            "target_theorem": target_theorem,
            "outcome": "untested",
            "summary": "No normalized proof-search result was returned.",
            "hints": [],
        }
    raise ValueError(f"Unsupported default probe kind {kind!r}.")


def _ensure_probe_results(
    probe_results: list[dict[str, Any]],
    *,
    session_name: str,
    target_theorem: str,
) -> list[dict[str, Any]]:
    by_kind: dict[str, dict[str, Any]] = {}
    for payload in probe_results:
        normalized = _normalize_probe_entry(
            dict(payload),
            fallback_kind=str(payload.get("kind") or ""),
            session_name=session_name,
            target_theorem=target_theorem,
        )
        if normalized is None:
            continue
        by_kind[normalized["kind"]] = normalized
    for kind in ("counterexample", "proofSearch"):
        by_kind.setdefault(
            kind,
            _default_probe_entry(
                kind=kind,
                session_name=session_name,
                target_theorem=target_theorem,
            ),
        )
    ordered: list[dict[str, Any]] = []
    for kind in ("counterexample", "proofSearch"):
        ordered.append(by_kind.pop(kind))
    for kind in sorted(by_kind):
        ordered.append(by_kind[kind])
    return ordered


def _normalize_robustness_harness(
    payload: dict[str, Any] | None,
    *,
    session_name: str,
    target_theorem: str,
) -> dict[str, Any]:
    data = dict(payload or {})
    return {
        "session": str(data.get("session") or session_name),
        "target_theorem": str(data.get("target_theorem") or target_theorem),
        "premise_sensitivity": str(data.get("premise_sensitivity") or "untested"),
        "conclusion_perturbation": str(
            data.get("conclusion_perturbation") or "untested"
        ),
        "notes": [str(item) for item in list(data.get("notes") or []) if str(item)],
    }


def _normalize_runner_audit_payload(
    payload: dict[str, Any],
    *,
    session_name: str,
    target_theorem: str,
    proof_backend: str,
    proof_transport: str,
) -> dict[str, Any]:
    normalized = dict(payload)
    raw_probe_results = normalized.get("probe_results")
    probe_candidates: list[dict[str, Any]] = []
    if isinstance(raw_probe_results, list):
        probe_candidates.extend(
            [dict(item) for item in raw_probe_results if isinstance(item, dict)]
        )
    elif isinstance(raw_probe_results, dict):
        if "kind" in raw_probe_results:
            probe_candidates.append(dict(raw_probe_results))
        else:
            probe_candidates.extend(
                [dict(item) for item in raw_probe_results.values() if isinstance(item, dict)]
            )

    legacy_backend_details: dict[str, Any] = {}
    for legacy_key in ("nitpick", "sledgehammer"):
        entry = normalized.pop(legacy_key, None)
        if isinstance(entry, dict):
            legacy_backend_details[legacy_key] = dict(entry)
            legacy_probe = _normalize_probe_entry(
                dict(entry),
                fallback_kind=legacy_key,
                session_name=session_name,
                target_theorem=target_theorem,
            )
            if legacy_probe is not None:
                probe_candidates.append(legacy_probe)

    normalized["probe_results"] = _ensure_probe_results(
        probe_candidates,
        session_name=session_name,
        target_theorem=target_theorem,
    )
    normalized["robustness_harness"] = _normalize_robustness_harness(
        normalized.get("robustness_harness"),
        session_name=session_name,
        target_theorem=target_theorem,
    )

    backend_extensions = dict(normalized.get("backend_extensions") or {})
    if legacy_backend_details:
        backend_extensions.setdefault("legacy_local", {}).update(legacy_backend_details)
    normalized["backend_extensions"] = backend_extensions
    backend_value = str(
        normalized.get("backend")
        or normalized.get("target_backend")
        or normalized.pop("proof_backend", None)
        or proof_backend
    )
    transport_value = str(
        normalized.get("transport")
        or normalized.pop("proof_transport", None)
        or normalized.pop("transport_kind", None)
        or proof_transport
    )
    normalized["backend"] = backend_value
    normalized["target_backend"] = backend_value
    normalized["transport"] = transport_value
    normalized["success"] = bool(normalized.get("success", True))
    return normalized


def _symbols_from_theory(
    theory_body: str,
) -> tuple[list[str], list[str], list[str], list[str], int, int]:
    theorem_pattern = re.compile(
        r"(?m)^\\s*(?:lemma|theorem|corollary|proposition)\\s+([A-Za-z0-9_']+)\\b"
    )
    definition_pattern = re.compile(r"(?m)^\\s*definition\\s+([A-Za-z0-9_']+)\\b")
    locale_pattern = re.compile(r"(?m)^\\s*locale\\s+([A-Za-z0-9_']+)\\b")
    sorry_locations: list[str] = []
    oops_count = 0
    sorry_count = 0
    for line_number, line in enumerate(theory_body.splitlines(), start=1):
        if re.search(r"\\bsorry\\b", line):
            sorry_count += 1
            sorry_locations.append(f"line {line_number}")
        if re.search(r"\\boops\\b", line):
            oops_count += 1
    return (
        theorem_pattern.findall(theory_body),
        definition_pattern.findall(theory_body),
        locale_pattern.findall(theory_body),
        sorry_locations,
        sorry_count,
        oops_count,
    )


def _collect_artifact_payloads(
    client: Any,
    workspace_ref: str,
    *,
    max_bytes: int,
) -> tuple[str, dict[str, str]]:
    artifact_index = client.list_artifacts(workspace_ref)
    artifact_paths: dict[str, str] = {}
    payloads: list[str] = []
    for artifact in artifact_index.artifacts:
        artifact_id = str(artifact.get("artifactId") or "")
        if not artifact_id:
            continue
        artifact_paths[artifact_id] = str(artifact.get("uri") or "")
        try:
            payload = client.read_artifact(workspace_ref, artifact_id, max_bytes=max_bytes)
        except Exception:
            continue
        if payload.content:
            payloads.append(payload.content)
    return "\n\n".join(payloads), artifact_paths


def _resolve_fwp_repo_root(config: ProofProtocolConfig) -> Path:
    candidates = [
        config.fwp_repo_root,
        os.environ.get("FORMAL_CLAIM_FWP_REPO"),
        os.environ.get("FWP_REPO_ROOT"),
        str(REPO_ROOT.parent / "fwp"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if (path / "packages" / "fwp-client" / "src").exists():
            return path
    raise RuntimeError(
        "Could not locate the FWP repository. Set FORMAL_CLAIM_FWP_REPO or "
        "configure proof_protocol.fwp_repo_root."
    )


@lru_cache(maxsize=8)
def _load_fwp_symbols(repo_root_text: str, include_local_hub: bool) -> _FwpSymbols:
    repo_root = Path(repo_root_text)
    patterns = ["packages/*/src"]
    if include_local_hub:
        patterns.extend(["services/*/src", "integrations/*/src"])
    for pattern in patterns:
        for path in sorted(repo_root.glob(pattern)):
            text = str(path)
            if text not in sys.path:
                sys.path.insert(0, text)
    from fwp_client import (  # type: ignore
        HttpHubTransport,
        LocalHubTransport,
        ProofAuditRequest,
        ProofBuildRequest,
        ProofProtocolClient as FwpClient,
        ProofProtocolClientError,
        ProofWorkspaceInputs,
        WorkspaceDocumentInput,
    )

    build_reference_hub = None
    if include_local_hub:
        from formal_hub import build_reference_hub as _build_reference_hub  # type: ignore

        build_reference_hub = _build_reference_hub
    return _FwpSymbols(
        ProofProtocolClient=FwpClient,
        ProofProtocolClientError=ProofProtocolClientError,
        ProofWorkspaceInputs=ProofWorkspaceInputs,
        WorkspaceDocumentInput=WorkspaceDocumentInput,
        ProofBuildRequest=ProofBuildRequest,
        ProofAuditRequest=ProofAuditRequest,
        LocalHubTransport=LocalHubTransport,
        HttpHubTransport=HttpHubTransport,
        build_reference_hub=build_reference_hub,
    )


def create_fwp_client(config: ProofProtocolConfig) -> tuple[Any, _FwpSymbols]:
    repo_root = _resolve_fwp_repo_root(config)
    include_local_hub = config.transport == "local_hub"
    symbols = _load_fwp_symbols(str(repo_root), include_local_hub)
    if config.transport == "local_hub":
        if symbols.build_reference_hub is None:
            raise RuntimeError("FWP local-hub transport is unavailable.")
        transport = symbols.LocalHubTransport(symbols.build_reference_hub())
    elif config.transport == "http":
        if not config.endpoint:
            raise ValueError("proof_protocol.endpoint is required when transport='http'.")
        auth_token = os.environ.get(config.auth_token_env) if config.auth_token_env else None
        transport = symbols.HttpHubTransport(
            config.endpoint,
            auth_token=auth_token,
            origin=config.origin,
            timeout_seconds=config.timeout_seconds,
        )
    else:
        raise ValueError(
            f"Unsupported proof transport {config.transport!r}. "
            "Expected 'local_hub' or 'http'."
        )
    return (
        symbols.ProofProtocolClient(
            transport,
            client_name="formal-claim",
            client_version="0.1.0",
        ),
        symbols,
    )


def create_fwp_workspace_inputs(
    symbols: _FwpSymbols,
    *,
    session_dir: str,
    session_name: str,
    target_theory: str,
    target_theorem: str,
    prepared_session: _PreparedTheorySession | None = None,
) -> Any:
    root_path = Path(session_dir).resolve()
    return symbols.ProofWorkspaceInputs(
        root_uri=root_path.as_uri(),
        documents=[],
        options={
            "sessionName": session_name,
            "targetTheory": target_theory,
            "targetTheorem": target_theorem,
        },
    )


class FwpProofAdapter:
    """FWP-backed proof adapter for formalization checks and theorem-local audits."""

    backend_id = "fwp"

    def __init__(
        self,
        config: PipelineConfig,
        *,
        client_factory: Callable[[ProofProtocolConfig], tuple[Any, _FwpSymbols]] | None = None,
    ) -> None:
        self.config = config
        self.client_factory = client_factory or create_fwp_client
        self._prepared_sessions: dict[str, _PreparedTheorySession] = {}

    def _subject_id_for(
        self,
        *,
        session_dir: str,
        prepared_session: _PreparedTheorySession | None,
        subject_id: str | None,
    ) -> str:
        if subject_id:
            return str(subject_id)
        if prepared_session is not None and prepared_session.subject_id:
            return str(prepared_session.subject_id)
        return "subject.unknown"

    def prepare_theory_session(
        self,
        *,
        session_dir: str,
        session_name: str,
        theory_name: str,
        theory_body: str,
        theorem_statement: str | None = None,
        subject_id: str | None = None,
    ) -> None:
        resolved = str(Path(session_dir).resolve())
        self._prepared_sessions[resolved] = _PreparedTheorySession(
            subject_id=subject_id,
            session_name=session_name,
            theory_name=theory_name,
            theory_body=theory_body,
            theorem_statement=theorem_statement,
        )
        # Write theory to disk so the real backend (lake build) can find it
        from .config import proof_source_extension
        ext = proof_source_extension(self.config.proof_protocol.target_backend_id)
        out_dir = Path(session_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        theory_file = out_dir / f"{theory_name}{ext}"
        theory_file.write_text(theory_body, encoding="utf-8")

    def build_session(
        self,
        *,
        session_name: str,
        session_dir: str,
        target_theory: str,
        target_theorem: str,
        subject_id: str | None = None,
    ) -> BuildSessionResult:
        resolved_session_dir = str(Path(session_dir).resolve())
        prepared_session = self._prepared_sessions.get(resolved_session_dir)
        client, symbols = self.client_factory(self.config.proof_protocol)
        claim_id = self._subject_id_for(
            session_dir=session_dir,
            prepared_session=prepared_session,
            subject_id=subject_id,
        )
        request = symbols.ProofBuildRequest(
            request_id=(
                "build."
                + sha256(
                    f"{session_dir}:{target_theory}:{target_theorem}".encode("utf-8")
                ).hexdigest()[:12]
            ),
            project_id=self.config.project_id,
            subject_id=claim_id,
            subject_revision_id=f"claim-graph://{self.config.project_id}/latest",
            artifact_ref=(
                f"formal-artifact://{self.config.project_id}/{claim_id}/{target_theory}"
            ),
            proof_source=_read_theory_body(session_dir, target_theory, prepared_session),
            theorem_statement=str(
                (prepared_session.theorem_statement if prepared_session else None)
                or target_theorem
            ),
            target_backend=self.config.proof_protocol.target_backend_id,
            workspace_inputs=create_fwp_workspace_inputs(
                symbols,
                session_dir=session_dir,
                session_name=session_name,
                target_theory=target_theory,
                target_theorem=target_theorem,
                prepared_session=prepared_session,
            ),
            resource_policy=_resource_policy_payload(self.config.proof_protocol),
            lineage={
                "requestOrigin": "formal-claim",
                "sessionDir": str(Path(session_dir).resolve()),
                "theoryName": target_theory,
                "targetTheorem": target_theorem,
            },
        )
        job = client.submit_formalization_check(request)
        deadline = (
            time.time() + self.config.proof_protocol.budget.wall_timeout_seconds + 30
        )
        while job.status == "running" and time.time() < deadline:
            time.sleep(self.config.proof_protocol.poll_interval_seconds)
            job = client.get_job(job.job_id)
        signal_kinds = list(job.diagnostic_summary.get("signalKinds") or [])
        artifact_stdout, artifact_paths = _collect_artifact_payloads(
            client,
            job.workspace_ref,
            max_bytes=self.config.proof_protocol.budget.max_output_bytes,
        )
        theory_body = _read_theory_body(session_dir, target_theory, prepared_session)
        theorems, definitions, locales, sorry_locations, sorry_count, oops_count = (
            _symbols_from_theory(theory_body)
        )
        fingerprint = sha256(
            json.dumps(
                {
                    "workspace_ref": job.workspace_ref,
                    "session_name": session_name,
                    "target_theory": target_theory,
                    "target_theorem": target_theorem,
                    "artifact_refs": job.artifact_refs,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        started_at = _parse_iso8601(job.started_at)
        completed_at = _parse_iso8601(job.completed_at)
        duration_seconds = 0.0
        if started_at is not None and completed_at is not None:
            duration_seconds = max(0.0, completed_at - started_at)
        normalized_status = _normalize_run_status(job.status, signal_kinds=signal_kinds)
        return BuildSessionResult(
            success=normalized_status == "completed",
            stdout=artifact_stdout
            or json.dumps(job.diagnostic_summary, ensure_ascii=True),
            stderr="",
            return_code=0 if normalized_status == "completed" else 1,
            sorry_count=sorry_count,
            oops_count=oops_count,
            sorry_locations=sorry_locations,
            theorems=theorems or [target_theorem],
            definitions=definitions,
            locales=locales,
            session_fingerprint=fingerprint,
            timeout_classification=_termination_reason(job.status, signal_kinds)
            or "completed",
            duration_seconds=duration_seconds,
            command=[
                "fwp-client",
                self.config.proof_protocol.transport,
                self.config.proof_protocol.target_backend_id,
                target_theory,
                target_theorem,
            ],
            workspace_dir=job.workspace_ref,
            session_dir=resolved_session_dir,
            root_path=None,
            artifact_paths=artifact_paths,
        )

    def run_audit(self, request_path: Path) -> dict[str, Any]:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        session_name = str(request.get("session_name") or request_path.stem)
        session_dir = str(request.get("session_dir") or request_path.parent)
        target_theory = str(request.get("target_theory") or session_name)
        target_theorem = str(request.get("target_theorem") or "demo")
        prepared_session = self._prepared_sessions.get(str(Path(session_dir).resolve()))
        proof_source = str(
            request.get("proof_source")
            or _read_theory_body(session_dir, target_theory, prepared_session)
        )
        theorem_statement = str(
            request.get("theorem_statement")
            or (prepared_session.theorem_statement if prepared_session else None)
            or target_theorem
        )
        client, symbols = self.client_factory(self.config.proof_protocol)
        claim_id = self._subject_id_for(
            session_dir=session_dir,
            prepared_session=prepared_session,
            subject_id=str(request.get("claim_id") or request.get("subject_id") or ""),
        )
        resource_policy = dict(
            request.get("resource_policy") or _resource_policy_payload(self.config.proof_protocol)
        )
        audit_request = symbols.ProofAuditRequest(
            request_id=(
                "audit."
                + sha256(
                    f"{request_path}:{target_theory}:{target_theorem}".encode("utf-8")
                ).hexdigest()[:12]
            ),
            project_id=self.config.project_id,
            subject_id=claim_id,
            subject_revision_id=f"claim-graph://{self.config.project_id}/latest",
            artifact_ref=(
                f"formal-artifact://{self.config.project_id}/{claim_id}/{target_theory}"
            ),
            proof_source=proof_source,
            theorem_statement=theorem_statement,
            target_backend=str(
                request.get("target_backend")
                or request.get("proof_backend")
                or self.config.proof_protocol.target_backend_id
            ),
            workspace_inputs=create_fwp_workspace_inputs(
                symbols,
                session_dir=session_dir,
                session_name=session_name,
                target_theory=target_theory,
                target_theorem=target_theorem,
                prepared_session=prepared_session,
            ),
            resource_policy=resource_policy,
            lineage={
                "requestOrigin": "formal-claim",
                "requestPath": str(request_path.resolve()),
                "sessionDir": str(Path(session_dir).resolve()),
            },
            export_requirements=list(
                request.get("export_requirements") or ["contractPack"]
            ),
            trust_frontier_requirements=list(
                request.get("trust_frontier_requirements") or ["trustFrontier"]
            ),
            probe_requirements=list(
                request.get("probe_requirements")
                or ["dependencySlice", "counterexample", "proofSearch"]
            ),
            robustness_harness_requirements=list(
                request.get("robustness_harness_requirements")
                or ["premiseDeletion", "conclusionPerturbation"]
            ),
            backend_extension_selection=dict(
                request.get("backend_extension_selection") or {}
            ),
        )
        audit = client.submit_audit_probe(audit_request)
        artifact_index = client.list_artifacts(audit.workspace_ref)
        artifact_paths = {
            str(item.get("artifactId") or ""): str(item.get("uri") or "")
            for item in artifact_index.artifacts
            if item.get("artifactId")
        }
        signal_by_kind: dict[str, list[dict[str, Any]]] = {}
        for signal in list(audit.signals or []):
            signal_by_kind.setdefault(str(signal.get("kind") or "unknown"), []).append(
                dict(signal)
            )

        trust_surface = {
            "session": session_name,
            "target_theorem": target_theorem,
            "direct_theorem_dependencies": [],
            "transitive_theorem_dependencies": [],
            "dependency_edges": [],
            "imported_theories": [],
            "imported_theory_hotspots": [],
            "oracle_ids": [],
            "global_axiom_ids": [],
            "reviewed_global_axiom_ids": [],
            "reviewed_exception_ids": [],
            "locale_assumptions": [],
            "premise_assumptions": [],
            "notes": [],
        }

        trust_signals = signal_by_kind.get("trustFrontier", [])
        if trust_signals:
            trust_payload = dict(trust_signals[-1])
            surface = dict(
                trust_payload.get("surface")
                or trust_payload.get("normalizedResult")
                or {}
            )
            for key, value in surface.items():
                trust_surface[key] = value
            status = str(trust_payload.get("status") or "reported")
            trust_surface["notes"] = list(
                dict.fromkeys(
                    list(trust_surface.get("notes") or [])
                    + [f"FWP trustFrontier status: {status}"]
                )
            )

        dependency_signals = signal_by_kind.get("dependencySlice", [])
        if dependency_signals:
            dependency_payload = dict(dependency_signals[-1])
            dependencies = list(
                dependency_payload.get("dependencies")
                or (dependency_payload.get("normalizedResult") or {}).get("dependencies")
                or []
            )
            if dependencies:
                trust_surface["transitive_theorem_dependencies"] = dependencies
                trust_surface["imported_theories"] = dependencies

        probe_results: list[dict[str, Any]] = []
        robustness_harness = _normalize_robustness_harness(
            None,
            session_name=session_name,
            target_theorem=target_theorem,
        )

        for signal in list(audit.signals or []):
            payload = dict(signal)
            signal_kind = str(payload.get("kind") or "")
            if signal_kind in {"counterexample", "nitpick", "proofSearch", "sledgehammer"}:
                normalized = _normalize_probe_entry(
                    payload,
                    fallback_kind=signal_kind,
                    session_name=session_name,
                    target_theorem=target_theorem,
                )
                if normalized is not None:
                    probe_results.append(normalized)
                continue

            if signal_kind != "probeSummary":
                continue

            probe_kind = str(
                payload.get("probeKind")
                or payload.get("probe_kind")
                or payload.get("probe")
                or payload.get("name")
                or ""
            )
            normalized_kind = _normalize_probe_kind(probe_kind)
            if normalized_kind in {"counterexample", "proofSearch"}:
                normalized = _normalize_probe_entry(
                    payload,
                    fallback_kind=normalized_kind,
                    session_name=session_name,
                    target_theorem=target_theorem,
                )
                if normalized is not None:
                    probe_results.append(normalized)
                continue

            outcome = str(payload.get("outcome") or payload.get("status") or "completed")
            summary = str(payload.get("summary") or "")
            if normalized_kind == "premiseDeletion":
                robustness_harness["premise_sensitivity"] = outcome
            elif normalized_kind == "conclusionPerturbation":
                robustness_harness["conclusion_perturbation"] = outcome
            if normalized_kind in {"premiseDeletion", "conclusionPerturbation", "robustness"}:
                robustness_harness["notes"] = list(
                    dict.fromkeys(
                        list(robustness_harness["notes"])
                        + ([summary] if summary else [])
                    )
                )

        backend_extensions = dict(audit.backend_extensions or {})
        backend_extensions.setdefault(
            "fwp",
            {
                "workspace_ref": audit.workspace_ref,
                "contract_pack_ref": audit.contract_pack_ref,
            },
        )
        payload = {
            "success": True,
            "session_name": session_name,
            "session_dir": session_dir,
            "target_theory": target_theory,
            "target_theorem": target_theorem,
            "workspace_ref": audit.workspace_ref,
            "contract_pack_ref": audit.contract_pack_ref,
            "artifact_paths": artifact_paths,
            "signals": list(audit.signals or []),
            "target_backend": str(
                backend_extensions.get("backend") or audit_request.target_backend
            ),
            "transport": self.config.proof_protocol.transport,
            "backend_extensions": backend_extensions,
            "trust": {
                "success": True,
                "session": session_name,
                "target_theorem": target_theorem,
                "surface": trust_surface,
                "notes": list(trust_surface.get("notes") or []),
            },
            "probe_results": probe_results,
            "robustness_harness": robustness_harness,
        }
        return _normalize_runner_audit_payload(
            payload,
            session_name=session_name,
            target_theorem=target_theorem,
            proof_backend=str(audit_request.target_backend),
            proof_transport=self.config.proof_protocol.transport,
        )


class FilesystemProofAdapter:
    """Generic local fixture adapter used only for tests and scenario replay."""

    backend_id = "filesystem_adapter"

    def __init__(
        self,
        config: PipelineConfig,
        *,
        builder: ProofBuilder,
        audit_client: ProofAuditExecutor,
    ) -> None:
        self.config = config
        self.builder = builder
        self.audit_client = audit_client

    def prepare_theory_session(
        self,
        *,
        session_dir: str,
        session_name: str,
        theory_name: str,
        theory_body: str,
        theorem_statement: str | None = None,
        subject_id: str | None = None,
    ) -> None:
        del theorem_statement
        del subject_id
        self.builder.write_theory(session_dir, theory_name, theory_body)
        self.builder.write_root(session_dir, session_name, [theory_name])

    def build_session(
        self,
        *,
        session_name: str,
        session_dir: str,
        target_theory: str,
        target_theorem: str,
        subject_id: str | None = None,
    ) -> Any:
        del subject_id
        del target_theory, target_theorem
        return self.builder.build(session_name, session_dir)

    def run_audit(self, request_path: Path) -> dict[str, Any]:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        session_name = str(request.get("session_name") or request_path.stem)
        target_theorem = str(request.get("target_theorem") or "demo")
        payload = dict(self.audit_client.run_audit(request_path))
        return _normalize_runner_audit_payload(
            payload,
            session_name=session_name,
            target_theorem=target_theorem,
            proof_backend=str(
                request.get("target_backend")
                or request.get("proof_backend")
                or self.config.proof_protocol.target_backend_id
            ),
            proof_transport="filesystem_adapter",
        )


def build_proof_protocol_client(config: PipelineConfig) -> ProofProtocolClient:
    """Return the currently configured proof backend adapter."""

    backend = config.proof_protocol.backend
    if backend != "fwp":
        raise ValueError(
            f"Unsupported proof backend {backend!r}. formal-claim now speaks only "
            "through the FWP proof seam."
        )
    return FwpProofAdapter(config)
