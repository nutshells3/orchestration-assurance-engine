"""Thin human-facing CLI over the canonical FormalClaimEngineAPI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence


def resolve_engine_src() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "services" / "engine" / "src"
        if candidate.exists():
            return candidate
    raise RuntimeError("Could not locate services/engine/src from apps/cli.")


ENGINE_SRC = resolve_engine_src()
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.engine_api import FormalClaimEngineAPI  # noqa: E402
from formal_claim_engine.fixture_runtime import build_engine_api  # noqa: E402
from formal_claim_engine.proof_control import ProofControlPlane  # noqa: E402

EngineApiFactory = Callable[[str | None], FormalClaimEngineAPI]


def default_engine_api_factory(data_dir: str | None) -> FormalClaimEngineAPI:
    return build_engine_api(data_dir=data_dir)


ENGINE_API_FACTORY: EngineApiFactory = default_engine_api_factory


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _render_pretty(value: Any, *, indent: int = 0) -> str:
    prefix = " " * indent
    if _scalar(value):
        if value is None:
            return f"{prefix}null"
        if isinstance(value, bool):
            return f"{prefix}{str(value).lower()}"
        return f"{prefix}{value}"
    if isinstance(value, list):
        if not value:
            return f"{prefix}[]"
        lines: list[str] = []
        for item in value:
            if _scalar(item):
                rendered = _render_pretty(item, indent=0)
                lines.append(f"{prefix}- {rendered.strip()}")
                continue
            lines.append(f"{prefix}-")
            lines.append(_render_pretty(item, indent=indent + 2))
        return "\n".join(lines)
    if isinstance(value, dict):
        if not value:
            return f"{prefix}{{}}"
        lines = []
        for key, item in value.items():
            if _scalar(item):
                rendered = _render_pretty(item, indent=0)
                lines.append(f"{prefix}{key}: {rendered.strip()}")
                continue
            lines.append(f"{prefix}{key}:")
            lines.append(_render_pretty(item, indent=indent + 2))
        return "\n".join(lines)
    return f"{prefix}{value}"


def _emit_payload(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    if getattr(args, "output", None):
        Path(args.output).write_text(
            json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    if args.output_format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    else:
        print(_render_pretty(payload))
    return 0


def _emit_error(args: argparse.Namespace, message: str, *, details: dict[str, Any] | None = None) -> int:
    payload = {"ok": False, "error": {"message": message}}
    if details:
        payload["error"]["details"] = details
    if getattr(args, "output_format", "pretty") == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=True), file=sys.stderr)
    else:
        print(_render_pretty(payload), file=sys.stderr)
    return 1


def _api(args: argparse.Namespace) -> FormalClaimEngineAPI:
    return ENGINE_API_FACTORY(getattr(args, "data_dir", None))


def _read_text_argument(args: argparse.Namespace) -> str:
    text = getattr(args, "text", None)
    file_path = getattr(args, "file", None)
    if text:
        return text
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    raise ValueError("Provide either --text or --file.")


def _read_stdin_bytes() -> bytes:
    payload = sys.stdin.buffer.read()
    if not payload:
        raise ValueError("Provide document bytes on stdin.")
    return payload


def _load_json_file(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    raise ValueError(f"Expected JSON object in {path}.")


def _proof_control(args: argparse.Namespace) -> ProofControlPlane:
    data_dir = getattr(args, "data_dir", None) or "./tracer_data"
    return ProofControlPlane(data_dir=data_dir)


def cmd_project_init(args: argparse.Namespace) -> dict[str, Any]:
    project = _api(args).create_project(args.name, args.domain, args.description)
    return {"project": project.model_dump(mode="json", exclude_none=True)}


def cmd_project_open(args: argparse.Namespace) -> dict[str, Any]:
    project = _api(args).open_project(args.project_id)
    return {"project": project.model_dump(mode="json", exclude_none=True)}


def cmd_project_list(args: argparse.Namespace) -> dict[str, Any]:
    projects = _api(args).list_projects()
    return {
        "projects": [
            project.model_dump(mode="json", exclude_none=True) for project in projects
        ]
    }


async def cmd_document_ingest(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "file", None):
        result = await _api(args).import_local_document(args.project_id, args.file)
    else:
        result = await _api(args).ingest_document(args.project_id, _read_text_argument(args))
    return {"document_ingest": result.model_dump(mode="json", exclude_none=True)}


async def cmd_document_upload(args: argparse.Namespace) -> dict[str, Any]:
    result = await _api(args).upload_document_bytes(
        args.project_id,
        file_name=args.name,
        raw_bytes=_read_stdin_bytes(),
        media_type=args.media_type,
    )
    return {"document_ingest": result.model_dump(mode="json", exclude_none=True)}


def cmd_document_source_list(args: argparse.Namespace) -> dict[str, Any]:
    return {"source_documents": _api(args).list_source_documents(args.project_id)}


def cmd_document_source_show(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "source_mapping_bundle": _api(args).get_source_mapping_bundle(
            args.project_id,
            args.document_id,
            revision_id=args.revision_id,
        )
    }


def cmd_reference_list(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "external_references": _api(args).list_external_references(
            args.project_id,
            revision_id=args.revision_id,
        )
    }


def cmd_reference_show(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "external_reference": _api(args).get_external_reference(
            args.project_id,
            args.reference_id,
        )
    }


def cmd_reference_links(args: argparse.Namespace) -> dict[str, Any]:
    if args.claim_id:
        return {
            "claim_reference_links": _api(args).get_claim_reference_links(
                args.project_id,
                args.claim_id,
            )
        }
    return {
        "assurance_links": _api(args).list_assurance_links(
            args.project_id,
        )
    }


def cmd_reference_backlinks(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "reference_backlinks": _api(args).get_reference_backlinks(
            args.project_id,
            args.reference_id,
        )
    }


def cmd_evidence_list(args: argparse.Namespace) -> dict[str, Any]:
    return {"evaluation_evidence": _api(args).list_evaluation_evidence(args.project_id)}


def cmd_evidence_show(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "evaluation_evidence": _api(args).get_evaluation_evidence(
            args.project_id,
            args.evidence_id,
        )
    }


def cmd_evidence_links(args: argparse.Namespace) -> dict[str, Any]:
    if args.claim_id:
        return {
            "claim_evidence_links": _api(args).get_claim_evidence_links(
                args.project_id,
                args.claim_id,
            )
        }
    return {
        "reference_evidence_links": _api(args).get_reference_evidence_links(
            args.project_id,
            args.reference_id,
        )
    }


async def cmd_claim_structure(args: argparse.Namespace) -> dict[str, Any]:
    result = await _api(args).run_claim_structuring(args.project_id, _read_text_argument(args))
    return {"claim_structuring": result.model_dump(mode="json", exclude_none=True)}


async def cmd_claim_analyze(args: argparse.Namespace) -> dict[str, Any]:
    result = await _api(args).analyze_claim(args.project_id, args.claim_id)
    return {"claim_analysis": result.model_dump(mode="json", exclude_none=True)}


async def cmd_formalize_dual(args: argparse.Namespace) -> dict[str, Any]:
    result = await _api(args).run_dual_formalization(args.project_id, args.claim_id)
    return {"dual_formalization": result.model_dump(mode="json", exclude_none=True)}


async def cmd_audit_run(args: argparse.Namespace) -> dict[str, Any]:
    result = await _api(args).run_audit(args.project_id, args.claim_id)
    return {"audit": result.model_dump(mode="json", exclude_none=True)}


def cmd_profile_recompute(args: argparse.Namespace) -> dict[str, Any]:
    audit_payload = _load_json_file(args.audit_result)
    audit_result = dict(audit_payload.get("audit") or audit_payload)
    research_output = None
    if args.research_output:
        research_payload = _load_json_file(args.research_output)
        research_output = dict(research_payload.get("research_output") or research_payload)
    result = _api(args).recompute_profile(
        args.project_id,
        args.claim_id,
        audit_result,
        research_output=research_output,
    )
    return {"profile_recompute": result.model_dump(mode="json", exclude_none=True)}


def cmd_promotion_transition(args: argparse.Namespace) -> dict[str, Any]:
    state = _api(args).approve_promotion(
        args.project_id,
        args.claim_id,
        target_gate=args.target_gate,
        actor=args.actor,
        actor_role=args.actor_role,
        override=args.override,
        rationale=args.rationale,
        notes=args.notes,
    )
    return {"promotion_state": state.model_dump(mode="json", exclude_none=True)}


def cmd_artifact_show(args: argparse.Namespace) -> dict[str, Any]:
    api = _api(args)
    if args.kind == "project":
        project = api.open_project(args.project_id)
        return {"project": project.model_dump(mode="json", exclude_none=True)}
    if args.kind == "summary":
        return {"summary": api.get_summary(args.project_id)}
    if args.kind == "claim-graph":
        return {"claim_graph": api.get_graph(args.project_id)}
    if args.kind == "claim-graph-signals":
        return {"claim_graph_signals": api.get_graph_signal_overlays(args.project_id)}
    if args.kind == "claim-graph-revisions":
        return {"claim_graph_revisions": api.list_graph_revisions(args.project_id)}
    if args.kind == "claim-graph-revision":
        if not args.revision_id:
            raise ValueError("--revision-id is required for claim-graph-revision show.")
        return {
            "claim_graph_revision": api.load_graph_revision(
                args.project_id,
                args.revision_id,
            )
        }
    if args.kind == "bundle":
        bundle = api.export_bundle(args.project_id)
        return {"bundle": bundle.model_dump(mode="json", exclude_none=True)}
    if args.kind == "profile":
        if not args.claim_id:
            raise ValueError("--claim-id is required for profile show.")
        return {"profile": api.get_assurance_profile(args.project_id, args.claim_id)}
    if args.kind == "audit-report":
        if not args.claim_id:
            raise ValueError("--claim-id is required for audit-report show.")
        return {"audit_report": api.get_latest_audit_report(args.project_id, args.claim_id)}
    if args.kind == "promotion-state":
        if not args.claim_id:
            raise ValueError("--claim-id is required for promotion-state show.")
        return {"promotion_state": api.get_promotion_state(args.project_id, args.claim_id)}
    if args.kind == "review-events":
        if not args.claim_id:
            raise ValueError("--claim-id is required for review-events show.")
        return {"review_events": api.list_review_events(args.project_id, args.claim_id)}
    raise ValueError(f"Unsupported artifact kind: {args.kind}")


def cmd_export_bundle(args: argparse.Namespace) -> dict[str, Any]:
    bundle = _api(args).export_bundle(args.project_id)
    return {"bundle": bundle.model_dump(mode="json", exclude_none=True)}


def cmd_export_trace(args: argparse.Namespace) -> dict[str, Any]:
    """Export trace.json + transition_log.jsonl + sidecar_meta.json."""
    output_dir = args.output_dir
    if not output_dir:
        data_dir = getattr(args, "data_dir", None) or "./pipeline_data"
        output_dir = str(Path(data_dir) / "exports" / args.project_id)
    result = _api(args).export_trace(args.project_id, output_dir)
    return {"trace_export": result.model_dump(mode="json", exclude_none=True)}


def cmd_export_prefix(args: argparse.Namespace) -> dict[str, Any]:
    """Extract PrefixSlice samples from a project trace."""
    result = _api(args).export_prefix_slices(
        args.project_id,
        output_path=args.output_path,
        format=args.slice_format,
    )
    return {"prefix_export": result.model_dump(mode="json", exclude_none=True)}


def cmd_proof_job_start(args: argparse.Namespace) -> dict[str, Any]:
    job = _proof_control(args).start_job(
        session_name=args.session_name,
        session_dir=args.session_dir,
        run_kind=args.run_kind,
        theory_path=args.theory_path,
        target_theory=args.target_theory,
        target_theorem=args.target_theorem,
        label=args.label,
        wall_timeout_seconds=args.wall_timeout,
        idle_timeout_seconds=args.idle_timeout,
        cancel_grace_seconds=args.cancel_grace,
    )
    return {"proof_job": job}


def cmd_proof_job_get(args: argparse.Namespace) -> dict[str, Any]:
    return {"proof_job": _proof_control(args).get_job(args.job_id)}


def cmd_proof_job_cancel(args: argparse.Namespace) -> dict[str, Any]:
    return {"proof_job": _proof_control(args).cancel_job(args.job_id)}


def cmd_proof_job_kill(args: argparse.Namespace) -> dict[str, Any]:
    return {"proof_job": _proof_control(args).kill_job(args.job_id)}


def cmd_proof_theory_list(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.session_dir).resolve()
    theory_files: list[str] = []
    for suffix in ("*.lean", "*.thy", "*.v"):
        theory_files.extend(str(path.resolve()) for path in sorted(root.rglob(suffix)))
    return {"theory_files": theory_files}


def cmd_proof_theory_read(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "theory_file": {
            "path": str(Path(args.path).resolve()),
            "content": Path(args.path).resolve().read_text(encoding="utf-8"),
        }
    }


def cmd_proof_theory_write(args: argparse.Namespace) -> dict[str, Any]:
    content = _read_text_argument(args)
    target = Path(args.path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    result = {
        "path": str(target),
        "content": content,
        "size_bytes": target.stat().st_size,
        "updated_at": target.stat().st_mtime,
    }
    return {"theory_file": result}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="formal-claim",
        description="Human-facing CLI over the canonical Formal Claim engine boundary.",
    )
    parser.add_argument("--data-dir", help="Override the engine data directory.")
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=["pretty", "json"],
        default="pretty",
        help="Output mode for stdout.",
    )

    root = parser.add_subparsers(dest="command")

    project = root.add_parser("project", help="Create, open, or list projects.")
    project_sub = project.add_subparsers(dest="project_command")

    project_init = project_sub.add_parser("init", help="Create a project.")
    project_init.add_argument("--name", required=True)
    project_init.add_argument("--domain", required=True)
    project_init.add_argument("--description", default="")
    project_init.add_argument("--output")
    project_init.set_defaults(func=cmd_project_init)

    project_open = project_sub.add_parser("open", help="Open an existing project.")
    project_open.add_argument("--project-id", required=True)
    project_open.add_argument("--output")
    project_open.set_defaults(func=cmd_project_open)

    project_list = project_sub.add_parser("list", help="List known projects.")
    project_list.add_argument("--output")
    project_list.set_defaults(func=cmd_project_list)

    document = root.add_parser("document", help="Ingest source documents.")
    document_sub = document.add_subparsers(dest="document_command")
    document_ingest = document_sub.add_parser("ingest", help="Extract evidence and candidates.")
    document_ingest.add_argument("--project-id", required=True)
    document_input = document_ingest.add_mutually_exclusive_group(required=True)
    document_input.add_argument("--text")
    document_input.add_argument("--file")
    document_ingest.add_argument("--output")
    document_ingest.set_defaults(func=cmd_document_ingest)

    document_upload = document_sub.add_parser(
        "upload",
        help="Read uploaded document bytes from stdin and extract candidates.",
    )
    document_upload.add_argument("--project-id", required=True)
    document_upload.add_argument("--name", required=True)
    document_upload.add_argument("--media-type")
    document_upload.add_argument("--output")
    document_upload.set_defaults(func=cmd_document_upload)

    document_source = document_sub.add_parser(
        "source",
        help="Inspect normalized source-document mappings.",
    )
    document_source_sub = document_source.add_subparsers(dest="document_source_command")

    document_source_list = document_source_sub.add_parser(
        "list",
        help="List source documents known to a project.",
    )
    document_source_list.add_argument("--project-id", required=True)
    document_source_list.add_argument("--output")
    document_source_list.set_defaults(func=cmd_document_source_list)

    document_source_show = document_source_sub.add_parser(
        "show",
        help="Show a stored source-mapping bundle.",
    )
    document_source_show.add_argument("--project-id", required=True)
    document_source_show.add_argument("--document-id", required=True)
    document_source_show.add_argument("--revision-id")
    document_source_show.add_argument("--output")
    document_source_show.set_defaults(func=cmd_document_source_show)

    reference = root.add_parser("reference", help="Browse engine-owned external references.")
    reference_sub = reference.add_subparsers(dest="reference_command")

    reference_list = reference_sub.add_parser("list", help="List external references for a project.")
    reference_list.add_argument("--project-id", required=True)
    reference_list.add_argument("--revision-id")
    reference_list.add_argument("--output")
    reference_list.set_defaults(func=cmd_reference_list)

    reference_show = reference_sub.add_parser("show", help="Show one external reference.")
    reference_show.add_argument("--project-id", required=True)
    reference_show.add_argument("--reference-id", required=True)
    reference_show.add_argument("--output")
    reference_show.set_defaults(func=cmd_reference_show)

    reference_links = reference_sub.add_parser(
        "links",
        help="List reference links for one claim or all assurance artifacts.",
    )
    reference_links.add_argument("--project-id", required=True)
    reference_links.add_argument("--claim-id")
    reference_links.add_argument("--output")
    reference_links.set_defaults(func=cmd_reference_links)

    reference_backlinks = reference_sub.add_parser(
        "backlinks",
        help="Show all claim/profile/audit/review backlinks for one reference.",
    )
    reference_backlinks.add_argument("--project-id", required=True)
    reference_backlinks.add_argument("--reference-id", required=True)
    reference_backlinks.add_argument("--output")
    reference_backlinks.set_defaults(func=cmd_reference_backlinks)

    evidence = root.add_parser("evidence", help="Browse engine-owned evaluation evidence.")
    evidence_sub = evidence.add_subparsers(dest="evidence_command")

    evidence_list = evidence_sub.add_parser("list", help="List evaluation evidence for a project.")
    evidence_list.add_argument("--project-id", required=True)
    evidence_list.add_argument("--output")
    evidence_list.set_defaults(func=cmd_evidence_list)

    evidence_show = evidence_sub.add_parser("show", help="Show one evaluation evidence item.")
    evidence_show.add_argument("--project-id", required=True)
    evidence_show.add_argument("--evidence-id", required=True)
    evidence_show.add_argument("--output")
    evidence_show.set_defaults(func=cmd_evidence_show)

    evidence_links = evidence_sub.add_parser(
        "links",
        help="Show evaluation evidence links for one claim or one reference.",
    )
    evidence_links.add_argument("--project-id", required=True)
    evidence_subject = evidence_links.add_mutually_exclusive_group(required=True)
    evidence_subject.add_argument("--claim-id")
    evidence_subject.add_argument("--reference-id")
    evidence_links.add_argument("--output")
    evidence_links.set_defaults(func=cmd_evidence_links)

    claim = root.add_parser("claim", help="Operate on claim workflows.")
    claim_sub = claim.add_subparsers(dest="claim_command")
    claim_structure = claim_sub.add_parser("structure", help="Run claim structuring.")
    claim_structure.add_argument("--project-id", required=True)
    claim_input = claim_structure.add_mutually_exclusive_group(required=True)
    claim_input.add_argument("--text")
    claim_input.add_argument("--file")
    claim_structure.add_argument("--output")
    claim_structure.set_defaults(func=cmd_claim_structure)

    claim_analyze = claim_sub.add_parser(
        "analyze",
        help="Run one-click claim analysis. Formal claims try audit first; others fall back to best-effort profiling.",
    )
    claim_analyze.add_argument("--project-id", required=True)
    claim_analyze.add_argument("--claim-id", required=True)
    claim_analyze.add_argument("--output")
    claim_analyze.set_defaults(func=cmd_claim_analyze)

    formalize = root.add_parser("formalize", help="Run explicit formalization workflows.")
    formalize_sub = formalize.add_subparsers(dest="formalize_command")
    formalize_dual = formalize_sub.add_parser(
        "dual",
        help="Run the dual-formalization workflow for one claim.",
    )
    formalize_dual.add_argument("--project-id", required=True)
    formalize_dual.add_argument("--claim-id", required=True)
    formalize_dual.add_argument("--output")
    formalize_dual.set_defaults(func=cmd_formalize_dual)

    audit = root.add_parser("audit", help="Run FWP-backed audit workflows.")
    audit_sub = audit.add_subparsers(dest="audit_command")
    audit_run = audit_sub.add_parser("run", help="Run audit for one claim.")
    audit_run.add_argument("--project-id", required=True)
    audit_run.add_argument("--claim-id", required=True)
    audit_run.add_argument("--output")
    audit_run.set_defaults(func=cmd_audit_run)

    profile = root.add_parser("profile", help="Recompute assurance profiles.")
    profile_sub = profile.add_subparsers(dest="profile_command")
    profile_recompute = profile_sub.add_parser("recompute", help="Recompute a profile.")
    profile_recompute.add_argument("--project-id", required=True)
    profile_recompute.add_argument("--claim-id", required=True)
    profile_recompute.add_argument("--audit-result", required=True)
    profile_recompute.add_argument("--research-output")
    profile_recompute.add_argument("--output")
    profile_recompute.set_defaults(func=cmd_profile_recompute)

    promotion = root.add_parser("promotion", help="Advance promotion checkpoints.")
    promotion_sub = promotion.add_subparsers(dest="promotion_command")
    promotion_transition = promotion_sub.add_parser("transition", help="Advance one claim gate.")
    promotion_transition.add_argument("--project-id", required=True)
    promotion_transition.add_argument("--claim-id", required=True)
    promotion_transition.add_argument("--target-gate", required=True)
    promotion_transition.add_argument("--actor", required=True)
    promotion_transition.add_argument("--actor-role", required=True)
    promotion_transition.add_argument("--override", action="store_true")
    promotion_transition.add_argument("--rationale", default="")
    promotion_transition.add_argument("--notes", default="")
    promotion_transition.add_argument("--output")
    promotion_transition.set_defaults(func=cmd_promotion_transition)

    artifact = root.add_parser("artifact", help="Inspect stored artifacts.")
    artifact_sub = artifact.add_subparsers(dest="artifact_command")
    artifact_show = artifact_sub.add_parser("show", help="Show one artifact or summary.")
    artifact_show.add_argument(
        "kind",
        choices=[
            "project",
            "summary",
            "claim-graph",
            "claim-graph-signals",
            "claim-graph-revisions",
            "claim-graph-revision",
            "profile",
            "audit-report",
            "promotion-state",
            "review-events",
            "bundle",
        ],
    )
    artifact_show.add_argument("--project-id", required=True)
    artifact_show.add_argument("--claim-id")
    artifact_show.add_argument("--revision-id")
    artifact_show.add_argument("--output")
    artifact_show.set_defaults(func=cmd_artifact_show)

    export = root.add_parser("export", help="Export canonical artifacts.")
    export_sub = export.add_subparsers(dest="export_command")
    export_bundle = export_sub.add_parser(
        "bundle",
        help="Export one project bundle (compatibility-only; prefer 'trace').",
    )
    export_bundle.add_argument("--project-id", required=True)
    export_bundle.add_argument("--output")
    export_bundle.set_defaults(func=cmd_export_bundle)

    export_trace = export_sub.add_parser(
        "trace",
        help="Export trace.json + transition_log.jsonl + sidecar_meta.json.",
    )
    export_trace.add_argument("--project-id", required=True)
    export_trace.add_argument(
        "--output-dir",
        help="Output directory (defaults to <data_dir>/exports/<project_id>).",
    )
    export_trace.add_argument("--output")
    export_trace.set_defaults(func=cmd_export_trace)

    export_prefix = export_sub.add_parser(
        "prefix",
        help="Extract PrefixSlice training samples from a project trace.",
    )
    export_prefix.add_argument("--project-id", required=True)
    export_prefix.add_argument(
        "--output-path",
        help="Output file path.",
    )
    export_prefix.add_argument(
        "--format",
        dest="slice_format",
        choices=["jsonl", "json"],
        default="jsonl",
    )
    export_prefix.add_argument("--output")
    export_prefix.set_defaults(func=cmd_export_prefix)

    proof = root.add_parser("proof", help="Run governed proof jobs and inspect generated proof source files.")
    proof_sub = proof.add_subparsers(dest="proof_command")

    proof_job = proof_sub.add_parser("job", help="Manage governed proof jobs.")
    proof_job_sub = proof_job.add_subparsers(dest="proof_job_command")

    proof_job_start = proof_job_sub.add_parser("start", help="Start a bounded proof build job.")
    proof_job_start.add_argument("--session-name", required=True)
    proof_job_start.add_argument("--session-dir", required=True)
    proof_job_start.add_argument("--run-kind", default="build", choices=["build"])
    proof_job_start.add_argument("--label", default="")
    proof_job_start.add_argument("--theory-path")
    proof_job_start.add_argument("--target-theory")
    proof_job_start.add_argument("--target-theorem")
    proof_job_start.add_argument("--wall-timeout", type=int, default=600)
    proof_job_start.add_argument("--idle-timeout", type=int, default=120)
    proof_job_start.add_argument("--cancel-grace", type=int, default=5)
    proof_job_start.add_argument("--output")
    proof_job_start.set_defaults(func=cmd_proof_job_start)

    proof_job_get = proof_job_sub.add_parser("get", help="Poll one governed proof job.")
    proof_job_get.add_argument("--job-id", required=True)
    proof_job_get.add_argument("--output")
    proof_job_get.set_defaults(func=cmd_proof_job_get)

    proof_job_cancel = proof_job_sub.add_parser(
        "cancel",
        help="Request graceful cancellation for a governed proof job.",
    )
    proof_job_cancel.add_argument("--job-id", required=True)
    proof_job_cancel.add_argument("--output")
    proof_job_cancel.set_defaults(func=cmd_proof_job_cancel)

    proof_job_kill = proof_job_sub.add_parser(
        "kill",
        help="Force kill a governed proof job.",
    )
    proof_job_kill.add_argument("--job-id", required=True)
    proof_job_kill.add_argument("--output")
    proof_job_kill.set_defaults(func=cmd_proof_job_kill)

    proof_theory = proof_sub.add_parser("theory", help="Inspect or edit generated proof source files.")
    proof_theory_sub = proof_theory.add_subparsers(dest="proof_theory_command")

    proof_theory_list = proof_theory_sub.add_parser(
        "list",
        help="List generated proof source files under one session directory.",
    )
    proof_theory_list.add_argument("--session-dir", required=True)
    proof_theory_list.add_argument("--output")
    proof_theory_list.set_defaults(func=cmd_proof_theory_list)

    proof_theory_read = proof_theory_sub.add_parser(
        "read",
        help="Read one generated proof source file.",
    )
    proof_theory_read.add_argument("--path", required=True)
    proof_theory_read.add_argument("--output")
    proof_theory_read.set_defaults(func=cmd_proof_theory_read)

    proof_theory_write = proof_theory_sub.add_parser(
        "write",
        help="Write one generated proof source file.",
    )
    proof_theory_write.add_argument("--path", required=True)
    proof_theory_input = proof_theory_write.add_mutually_exclusive_group(required=True)
    proof_theory_input.add_argument("--text")
    proof_theory_input.add_argument("--file")
    proof_theory_write.add_argument("--output")
    proof_theory_write.set_defaults(func=cmd_proof_theory_write)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    try:
        result = args.func(args)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return _emit_payload(args, result)
    except Exception as exc:  # pragma: no cover - exercised via operator tests
        return _emit_error(args, str(exc), details={"type": exc.__class__.__name__})


__all__ = ["ENGINE_API_FACTORY", "build_parser", "default_engine_api_factory", "main"]
