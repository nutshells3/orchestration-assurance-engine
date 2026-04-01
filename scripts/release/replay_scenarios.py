"""Replay deterministic end-to-end scenarios through the human CLI surface."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def resolve_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "scripts" / "dev" / "run_uv.py").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from scenario replay script.")


ROOT = resolve_root()
DEFAULT_SCENARIOS = ["theorem-audit", "paper-claim", "legal-claim"]
SCENARIO_FIXTURE_ENV = "FORMAL_CLAIM_SCENARIO_FIXTURE"


def scenario_dir(scenario_id: str) -> Path:
    return ROOT / "examples" / scenario_id


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def cli_json(
    *,
    data_dir: Path,
    fixture_path: Path,
    output_path: Path,
    args: list[str],
) -> dict[str, Any]:
    env = dict(os.environ)
    env[SCENARIO_FIXTURE_ENV] = str(fixture_path)
    command = [
        sys.executable,
        str(ROOT / "scripts" / "dev" / "run_uv.py"),
        "run",
        "--directory",
        "apps/cli",
        "formal-claim",
        "--format",
        "json",
        "--data-dir",
        str(data_dir),
        *args,
    ]
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"CLI step failed for {' '.join(args)}\nSTDERR:\n{completed.stderr}\nSTDOUT:\n{completed.stdout}"
        )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object from CLI, found {payload!r}")
    write_json(output_path, payload)
    return payload


def validate_summary(summary: dict[str, Any], expected: dict[str, Any]) -> None:
    if summary["scenario_id"] != expected["scenario_id"]:
        raise AssertionError((summary["scenario_id"], expected["scenario_id"]))
    if summary["domain"] != expected["domain"]:
        raise AssertionError((summary["domain"], expected["domain"]))
    if summary["claim_id"] != expected["target_claim_id"]:
        raise AssertionError((summary["claim_id"], expected["target_claim_id"]))
    if summary["source_document_kind"] != expected["source_document_kind"]:
        raise AssertionError(
            (summary["source_document_kind"], expected["source_document_kind"])
        )
    if summary["claim_count"] < int(expected["minimum_claim_count"]):
        raise AssertionError((summary["claim_count"], expected["minimum_claim_count"]))
    if summary["reference_count"] < int(expected["minimum_reference_count"]):
        raise AssertionError((summary["reference_count"], expected["minimum_reference_count"]))
    if summary["assurance_link_count"] < int(expected["minimum_assurance_link_count"]):
        raise AssertionError(
            (summary["assurance_link_count"], expected["minimum_assurance_link_count"])
        )
    if summary["evaluation_evidence_count"] < int(expected.get("minimum_evaluation_evidence_count", 0)):
        raise AssertionError(
            (
                summary["evaluation_evidence_count"],
                expected.get("minimum_evaluation_evidence_count", 0),
            )
        )
    if summary["claim_evidence_link_count"] < int(expected.get("minimum_claim_evidence_link_count", 0)):
        raise AssertionError(
            (
                summary["claim_evidence_link_count"],
                expected.get("minimum_claim_evidence_link_count", 0),
            )
        )
    if summary["reference_evidence_link_count"] < int(
        expected.get("minimum_reference_evidence_link_count", 0)
    ):
        raise AssertionError(
            (
                summary["reference_evidence_link_count"],
                expected.get("minimum_reference_evidence_link_count", 0),
            )
        )
    if summary["final_promotion_gate"] != expected["final_promotion_gate"]:
        raise AssertionError(
            (summary["final_promotion_gate"], expected["final_promotion_gate"])
        )


def summary_path_for(
    scenario_id: str,
    *,
    output_root: Path | None = None,
) -> Path:
    return (output_root or (ROOT / ".tmp" / "scenario-runs")) / scenario_id / "summary.json"


def load_existing_summary(
    scenario_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, Any] | None:
    summary_path = summary_path_for(scenario_id, output_root=output_root)
    if not summary_path.exists():
        return None
    return load_json(summary_path)


def replay_scenario(
    scenario_id: str,
    *,
    output_root: Path | None = None,
) -> dict[str, Any]:
    fixture_path = scenario_dir(scenario_id) / "scenario.json"
    source_path = scenario_dir(scenario_id) / "source.md"
    expected_path = scenario_dir(scenario_id) / "expected-summary.json"
    fixture = load_json(fixture_path)
    expected = load_json(expected_path)
    run_root = (output_root or (ROOT / ".tmp" / "scenario-runs")) / scenario_id
    data_dir = run_root / "data"
    output_files: dict[str, str] = {}
    current_step = "setup"

    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    def run_step(step_name: str, *args: str) -> dict[str, Any]:
        nonlocal current_step
        current_step = step_name
        output_path = run_root / f"{step_name}.json"
        payload = cli_json(
            data_dir=data_dir,
            fixture_path=fixture_path,
            output_path=output_path,
            args=list(args),
        )
        output_files[step_name] = str(output_path)
        return payload

    try:
        created = run_step(
            "project-init",
            "project",
            "init",
            "--name",
            str(fixture["name"]),
            "--domain",
            str(fixture["domain"]),
            "--description",
            str(fixture["description"]),
        )
        project_id = str(created["project"]["project_id"])

        ingested = run_step(
            "document-ingest",
            "document",
            "ingest",
            "--project-id",
            project_id,
            "--file",
            str(source_path),
        )
        document_id = str(ingested["document_ingest"]["source_document"]["document_id"])

        source_list = run_step(
            "document-source-list",
            "document",
            "source",
            "list",
            "--project-id",
            project_id,
        )
        source_show = run_step(
            "document-source-show",
            "document",
            "source",
            "show",
            "--project-id",
            project_id,
            "--document-id",
            document_id,
        )

        structured = run_step(
            "claim-structure",
            "claim",
            "structure",
            "--project-id",
            project_id,
            "--text",
            str(fixture["structure_input"]),
        )
        claim_id = str(fixture["target_claim_id"])

        formalized = run_step(
            "formalize-dual",
            "formalize",
            "dual",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
        )

        audited = run_step(
            "audit-run",
            "audit",
            "run",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
        )

        research_output_path = run_root / "research-output.json"
        write_json(research_output_path, dict(fixture.get("research_output") or {}))
        output_files["research-output"] = str(research_output_path)

        recomputed = run_step(
            "profile-recompute",
            "profile",
            "recompute",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
            "--audit-result",
            str(run_root / "audit-run.json"),
            "--research-output",
            str(research_output_path),
        )

        promotion_states: list[dict[str, Any]] = []
        for index, transition in enumerate(list(fixture.get("promotion_transitions") or []), start=1):
            step_name = f"promotion-{index:02d}-{transition['target_gate']}"
            args = [
                "promotion",
                "transition",
                "--project-id",
                project_id,
                "--claim-id",
                claim_id,
                "--target-gate",
                str(transition["target_gate"]),
                "--actor",
                str(transition["actor"]),
                "--actor-role",
                str(transition["actor_role"]),
                "--rationale",
                str(transition.get("rationale") or ""),
                "--notes",
                str(transition.get("notes") or ""),
            ]
            if bool(transition.get("override")):
                args.append("--override")
            promotion_states.append(run_step(step_name, *args))

        reference_list = run_step(
            "reference-list",
            "reference",
            "list",
            "--project-id",
            project_id,
        )
        external_references = list(reference_list["external_references"])
        if not external_references:
            raise AssertionError(f"{scenario_id} produced no external references.")
        claim_links = run_step(
            "reference-links-claim",
            "reference",
            "links",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
        )
        assurance_links = run_step(
            "reference-links-assurance",
            "reference",
            "links",
            "--project-id",
            project_id,
        )
        evidence_list = run_step(
            "evidence-list",
            "evidence",
            "list",
            "--project-id",
            project_id,
        )
        evaluation_evidence = list(evidence_list["evaluation_evidence"])
        evidence_id = str(evaluation_evidence[0]["evidence_id"]) if evaluation_evidence else ""
        reference_id = str(external_references[0]["reference_id"])
        if evaluation_evidence:
            reference_id = str(
                (evaluation_evidence[0].get("linked_reference_ids") or [reference_id])[0]
            )
        reference_show = run_step(
            "reference-show",
            "reference",
            "show",
            "--project-id",
            project_id,
            "--reference-id",
            reference_id,
        )
        backlinks = run_step(
            "reference-backlinks",
            "reference",
            "backlinks",
            "--project-id",
            project_id,
            "--reference-id",
            reference_id,
        )
        evidence_show = (
            run_step(
                "evidence-show",
                "evidence",
                "show",
                "--project-id",
                project_id,
                "--evidence-id",
                evidence_id,
            )
            if evidence_id
            else {"evaluation_evidence": None}
        )
        claim_evidence_links = run_step(
            "evidence-links-claim",
            "evidence",
            "links",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
        )
        reference_evidence_links = run_step(
            "evidence-links-reference",
            "evidence",
            "links",
            "--project-id",
            project_id,
            "--reference-id",
            reference_id,
        )

        graph = run_step(
            "artifact-claim-graph",
            "artifact",
            "show",
            "claim-graph",
            "--project-id",
            project_id,
        )
        graph_signals = run_step(
            "artifact-claim-graph-signals",
            "artifact",
            "show",
            "claim-graph-signals",
            "--project-id",
            project_id,
        )
        graph_revisions = run_step(
            "artifact-claim-graph-revisions",
            "artifact",
            "show",
            "claim-graph-revisions",
            "--project-id",
            project_id,
        )
        revision_id = str(graph_revisions["claim_graph_revisions"][-1]["revision_id"])
        graph_revision = run_step(
            "artifact-claim-graph-revision",
            "artifact",
            "show",
            "claim-graph-revision",
            "--project-id",
            project_id,
            "--revision-id",
            revision_id,
        )
        profile = run_step(
            "artifact-profile",
            "artifact",
            "show",
            "profile",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
        )
        audit_report = run_step(
            "artifact-audit-report",
            "artifact",
            "show",
            "audit-report",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
        )
        promotion_state = run_step(
            "artifact-promotion-state",
            "artifact",
            "show",
            "promotion-state",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
        )
        review_events = run_step(
            "artifact-review-events",
            "artifact",
            "show",
            "review-events",
            "--project-id",
            project_id,
            "--claim-id",
            claim_id,
        )
        bundle = run_step(
            "artifact-bundle",
            "artifact",
            "show",
            "bundle",
            "--project-id",
            project_id,
        )
        exported = run_step(
            "export-bundle",
            "export",
            "bundle",
            "--project-id",
            project_id,
        )

        trace_output_dir = str(run_root / "trace-export")
        exported_trace = run_step(
            "export-trace",
            "export",
            "trace",
            "--project-id",
            project_id,
            "--output-dir",
            trace_output_dir,
        )

        exported_prefix = run_step(
            "export-prefix",
            "export",
            "prefix",
            "--project-id",
            project_id,
            "--output-path",
            str(run_root / "prefix_slices.jsonl"),
        )

        summary = {
            "scenario_id": scenario_id,
            "scenario_name": str(fixture["name"]),
            "domain": str(fixture["domain"]),
            "fixture_path": str(fixture_path),
            "expected_path": str(expected_path),
            "source_path": str(source_path),
            "run_root": str(run_root),
            "data_dir": str(data_dir),
            "project_id": project_id,
            "document_id": document_id,
            "claim_id": claim_id,
            "reference_id": reference_id,
            "source_document_kind": str(
                ingested["document_ingest"]["source_document"]["source_kind"]
            ),
            "claim_count": len(graph["claim_graph"]["claims"]),
            "reference_count": len(external_references),
            "assurance_link_count": len(assurance_links["assurance_links"]),
            "claim_reference_link_count": len(claim_links["claim_reference_links"]),
            "evaluation_evidence_count": len(evaluation_evidence),
            "claim_evidence_link_count": len(
                claim_evidence_links["claim_evidence_links"]
            ),
            "reference_evidence_link_count": len(
                reference_evidence_links["reference_evidence_links"]
            ),
            "graph_revision_id": revision_id,
            "profile_gate": str(profile["profile"]["gate"]),
            "final_promotion_gate": str(
                promotion_state["promotion_state"]["current_gate"]
            ),
            "review_event_count": len(review_events["review_events"]),
            "cli_steps": list(output_files.keys()),
            "output_files": output_files,
        }
        validate_summary(summary, expected)
        write_json(run_root / "summary.json", summary)

        if source_list["source_documents"][0]["document_id"] != document_id:
            raise AssertionError(source_list)
        if source_show["source_mapping_bundle"]["artifact"]["source_document"]["document_id"] != document_id:
            raise AssertionError(source_show)
        if structured["claim_structuring"]["workflow"]["state"] != "admitted":
            raise AssertionError(structured)
        if formalized["dual_formalization"]["workflow"]["state"] != "completed":
            raise AssertionError(formalized)
        if audited["audit"]["audit_workflow"]["state"] != "completed":
            raise AssertionError(audited)
        if recomputed["profile_recompute"]["profile"]["claim_id"] != claim_id:
            raise AssertionError(recomputed)
        if graph_revision["claim_graph_revision"]["revision"]["revision_id"] != revision_id:
            raise AssertionError(graph_revision)
        if reference_show["external_reference"]["reference_id"] != reference_id:
            raise AssertionError(reference_show)
        if not backlinks["reference_backlinks"]:
            raise AssertionError(backlinks)
        if evidence_id:
            if evidence_show["evaluation_evidence"]["evidence_id"] != evidence_id:
                raise AssertionError(evidence_show)
            if not claim_evidence_links["claim_evidence_links"]:
                raise AssertionError(claim_evidence_links)
            if not reference_evidence_links["reference_evidence_links"]:
                raise AssertionError(reference_evidence_links)
        if bundle["bundle"]["promotion_states"][claim_id]["current_gate"] != summary["final_promotion_gate"]:
            raise AssertionError(bundle)
        if exported["bundle"]["promotion_states"][claim_id]["current_gate"] != summary["final_promotion_gate"]:
            raise AssertionError(exported)
        if claim_id not in graph_signals["claim_graph_signals"]:
            raise AssertionError(graph_signals)

        # Canonical trace export validation
        trace_export = exported_trace["trace_export"]
        if not trace_export.get("trace_path"):
            raise AssertionError("export trace produced no trace_path")
        if not trace_export.get("transition_log_path"):
            raise AssertionError("export trace produced no transition_log_path")
        if not trace_export.get("sidecar_meta_path"):
            raise AssertionError("export trace produced no sidecar_meta_path")
        if trace_export.get("export_version") != "v2":
            raise AssertionError(f"export trace version is not v2: {trace_export.get('export_version')}")
        if not Path(trace_export["trace_path"]).exists():
            raise AssertionError(f"trace.json not found at {trace_export['trace_path']}")
        if not Path(trace_export["transition_log_path"]).exists():
            raise AssertionError(f"transition_log.jsonl not found at {trace_export['transition_log_path']}")
        if not Path(trace_export["sidecar_meta_path"]).exists():
            raise AssertionError(f"sidecar_meta.json not found at {trace_export['sidecar_meta_path']}")

        # Canonical prefix export validation
        prefix_export = exported_prefix["prefix_export"]
        if not prefix_export.get("output_path"):
            raise AssertionError("export prefix produced no output_path")
        if not Path(prefix_export["output_path"]).exists():
            raise AssertionError(f"prefix_slices.jsonl not found at {prefix_export['output_path']}")
        if prefix_export.get("slice_count", 0) < 1:
            raise AssertionError(f"export prefix produced {prefix_export.get('slice_count', 0)} slices, expected >= 1")

        return summary
    except Exception as exc:
        write_json(
            run_root / "failure.json",
            {
                "scenario_id": scenario_id,
                "step": current_step,
                "error": str(exc),
                "run_root": str(run_root),
            },
        )
        raise


def replay_scenarios(
    scenario_ids: list[str] | None = None,
    *,
    output_root: Path | None = None,
    reuse_existing: bool = False,
) -> list[dict[str, Any]]:
    results = []
    for scenario_id in scenario_ids or DEFAULT_SCENARIOS:
        if reuse_existing:
            existing = load_existing_summary(scenario_id, output_root=output_root)
            if existing is not None:
                results.append(existing)
                continue
        results.append(replay_scenario(scenario_id, output_root=output_root))
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay deterministic end-to-end scenarios.")
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="Replay one named scenario. Can be repeated.",
    )
    parser.add_argument(
        "--output-root",
        default=str(ROOT / ".tmp" / "scenario-runs"),
        help="Directory where replay outputs and summaries are stored.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = replay_scenarios(args.scenarios, output_root=Path(args.output_root))
    print(json.dumps({"scenarios": results}, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
