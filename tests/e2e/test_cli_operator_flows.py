"""Operator-flow smoke for the human-facing formal-claim CLI."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "apps" / "cli" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from CLI e2e test.")


REPO_ROOT = resolve_repo_root()
CLI_SRC = REPO_ROOT / "apps" / "cli" / "src"
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

for src in (REPO_ROOT, CLI_SRC, ENGINE_SRC):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

from formal_claim_cli import cli as cli_module  # noqa: E402
from formal_claim_engine import FormalClaimEngineAPI, PipelineConfig  # noqa: E402
from tests.integration.test_document_ingest_adapter import StubLLM as IngestStubLLM  # noqa: E402
from tests.integration.test_engine_api import DummyLLM, build_orchestrator  # noqa: E402


class FakeStdin:
    def __init__(self, payload: bytes):
        self.buffer = io.BytesIO(payload)

    def read(self, *args, **kwargs):
        return self.buffer.getvalue().decode("utf-8", errors="replace")


def run_cli(*args: str, stdin_bytes: bytes | None = None) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    original_stdin = sys.stdin
    if stdin_bytes is not None:
        sys.stdin = FakeStdin(stdin_bytes)
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli_module.main(list(args))
    finally:
        sys.stdin = original_stdin
    return code, stdout.getvalue(), stderr.getvalue()


def parse_stdout_json(stdout: str) -> dict[str, object]:
    payload = json.loads(stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"Expected JSON object payload, found {payload!r}")
    return payload


def theorem_audit_factory(data_dir: str | None) -> FormalClaimEngineAPI:
    return FormalClaimEngineAPI(
        config=PipelineConfig(data_dir=data_dir or "./pipeline_data"),
        llm=DummyLLM(),
        orchestrator_factory=build_orchestrator,
    )


def document_ingest_factory(data_dir: str | None) -> FormalClaimEngineAPI:
    payload = {
        "claims": [
            {
                "id": "premise_a",
                "title": "Article 5 applies",
                "statement": "Article 5 governs the dispute under the stated jurisdictional facts.",
                "role": "statute",
                "source_location": "doc:complaint#p1",
                "source_text": "Article 5 governs the dispute.",
                "scope": "jurisdictional dispute",
                "depth": 0,
            },
            {
                "id": "claim_b",
                "title": "Termination was unauthorized",
                "statement": "Termination lacked a valid Article 5 basis.",
                "role": "holding",
                "source_location": "doc:complaint#p2",
                "source_text": "Termination lacked a valid basis.",
                "scope": "jurisdictional dispute",
                "depth": 1,
            },
        ],
        "relations": [
            {
                "source_id": "premise_a",
                "target_id": "claim_b",
                "relation_type": "supports",
                "strength": "authoritative",
                "rationale": "Article 5 is the cited basis for the holding.",
            }
        ],
    }
    return FormalClaimEngineAPI(
        config=PipelineConfig(data_dir=data_dir or "./pipeline_data"),
        llm=IngestStubLLM(payload),
    )


def paper_evidence_factory(data_dir: str | None) -> FormalClaimEngineAPI:
    payload = {
        "claims": [
            {
                "id": "matched_workload",
                "title": "Baseline and guarded workloads match",
                "statement": "The baseline and guardrailed review pipelines use the same ticket stream.",
                "role": "premise",
                "source_text": "The baseline and guardrailed review pipelines use the same ticket stream.",
                "scope": "review latency study",
                "depth": 0,
            },
            {
                "id": "latency_improves",
                "title": "Median latency improves",
                "statement": "Under the guarded policy, median review latency falls from 18 minutes to 11 minutes.",
                "role": "observation",
                "source_text": "Under the guarded policy, median review latency falls from 18 minutes to 11 minutes.",
                "scope": "review latency study",
                "depth": 1,
            },
            {
                "id": "policy_unchanged",
                "title": "Escalation policy remains unchanged",
                "statement": "The escalation policy remains unchanged while the guardrail is active.",
                "role": "premise",
                "source_text": "The escalation policy remains unchanged while the guardrail is active.",
                "scope": "review latency study",
                "depth": 1,
            },
        ],
        "relations": [
            {
                "source_id": "latency_improves",
                "target_id": "matched_workload",
                "relation_type": "supports",
                "strength": "inductive",
                "rationale": "The improvement claim depends on matched workloads.",
            },
            {
                "source_id": "latency_improves",
                "target_id": "policy_unchanged",
                "relation_type": "supports",
                "strength": "inductive",
                "rationale": "The improvement claim remains scoped to unchanged escalation policy.",
            },
        ],
    }
    return FormalClaimEngineAPI(
        config=PipelineConfig(data_dir=data_dir or "./pipeline_data"),
        llm=IngestStubLLM(payload),
    )


def main() -> None:
    original_factory = cli_module.ENGINE_API_FACTORY
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cli_module.ENGINE_API_FACTORY = theorem_audit_factory

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "project",
                "init",
                "--name",
                "dispatch-proof",
                "--domain",
                "formal_proof",
                "--description",
                "cli theorem audit smoke",
            )
            assert code == 0, stderr
            created = parse_stdout_json(stdout)
            project_id = str(created["project"]["project_id"])

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "project",
                "open",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            assert "project_id:" in stdout, stdout

            structure_path = Path(tmp) / "structure.json"
            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "claim",
                "structure",
                "--project-id",
                project_id,
                "--text",
                "Prove that the dispatch algorithm converges.",
                "--output",
                str(structure_path),
            )
            assert code == 0, stderr
            structured = parse_stdout_json(stdout)
            assert structured["claim_structuring"]["workflow"]["state"] == "admitted"

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "claim-graph-revisions",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            revisions = parse_stdout_json(stdout)
            assert revisions["claim_graph_revisions"], revisions
            revision_id = str(revisions["claim_graph_revisions"][-1]["revision_id"])

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "claim-graph-signals",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            graph_signals = parse_stdout_json(stdout)
            assert "claim.dispatch.driver_assignment_converges" in graph_signals["claim_graph_signals"]

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "claim-graph-revision",
                "--project-id",
                project_id,
                "--revision-id",
                revision_id,
            )
            assert code == 0, stderr
            revision = parse_stdout_json(stdout)
            assert (
                revision["claim_graph_revision"]["revision"]["revision_id"] == revision_id
            )
            assert revision["claim_graph_revision"]["artifact"]["project_id"] == project_id

            audit_path = Path(tmp) / "audit.json"
            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "audit",
                "run",
                "--project-id",
                project_id,
                "--claim-id",
                "claim.dispatch.driver_assignment_converges",
                "--output",
                str(audit_path),
            )
            assert code == 0, stderr
            audited = parse_stdout_json(stdout)
            assert audited["audit"]["audit_workflow"]["state"] == "completed"
            assert audited["audit"]["profile"]["proofClaim"]["scoreMethod"] == "qbaf_df_quad"

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "claim",
                "analyze",
                "--project-id",
                project_id,
                "--claim-id",
                "claim.dispatch.driver_assignment_converges",
            )
            assert code == 0, stderr
            analyzed = parse_stdout_json(stdout)
            assert analyzed["claim_analysis"]["analysis_mode"] == "audit_workflow"
            assert (
                analyzed["claim_analysis"]["profile"]["proofClaim"]["scoreMethod"]
                == "qbaf_df_quad"
            )

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "profile",
                "--project-id",
                project_id,
                "--claim-id",
                "claim.dispatch.driver_assignment_converges",
            )
            assert code == 0, stderr
            profile_payload = parse_stdout_json(stdout)
            assert profile_payload["profile"]["claim_id"] == "claim.dispatch.driver_assignment_converges"

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "audit-report",
                "--project-id",
                project_id,
                "--claim-id",
                "claim.dispatch.driver_assignment_converges",
            )
            assert code == 0, stderr
            audit_report = parse_stdout_json(stdout)
            assert audit_report["audit_report"]["event_type"] == "audit_workflow"

            research_path = Path(tmp) / "research.json"
            research_path.write_text(
                json.dumps(
                    {
                        "overall_assessment": "Simulation supports the claim.",
                        "recommended_support_status": "simulation_supported",
                        "evidence_items": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "profile",
                "recompute",
                "--project-id",
                project_id,
                "--claim-id",
                "claim.dispatch.driver_assignment_converges",
                "--audit-result",
                str(audit_path),
                "--research-output",
                str(research_path),
            )
            assert code == 0, stderr
            recomputed = parse_stdout_json(stdout)
            assert (
                recomputed["profile_recompute"]["profile"]["claim_id"]
                == "claim.dispatch.driver_assignment_converges"
            )

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "promotion",
                "transition",
                "--project-id",
                project_id,
                "--claim-id",
                "claim.dispatch.driver_assignment_converges",
                "--target-gate",
                "blocked",
                "--actor",
                "human.reviewer",
                "--actor-role",
                "reviewer",
                "--notes",
                "CLI review hold.",
            )
            assert code == 0, stderr
            promoted = parse_stdout_json(stdout)
            assert promoted["promotion_state"]["current_gate"] == "blocked"

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "promotion-state",
                "--project-id",
                project_id,
                "--claim-id",
                "claim.dispatch.driver_assignment_converges",
            )
            assert code == 0, stderr
            promotion_state = parse_stdout_json(stdout)
            assert promotion_state["promotion_state"]["current_gate"] == "blocked"

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "review-events",
                "--project-id",
                project_id,
                "--claim-id",
                "claim.dispatch.driver_assignment_converges",
            )
            assert code == 0, stderr
            review_events = parse_stdout_json(stdout)
            assert len(review_events["review_events"]) >= 2, review_events

            bundle_path = Path(tmp) / "bundle.json"
            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "export",
                "bundle",
                "--project-id",
                project_id,
                "--output",
                str(bundle_path),
            )
            assert code == 0, stderr
            bundle = parse_stdout_json(stdout)
            assert bundle["bundle"]["promotion_states"][
                "claim.dispatch.driver_assignment_converges"
            ]["current_gate"] == "blocked"

        with tempfile.TemporaryDirectory() as tmp:
            cli_module.ENGINE_API_FACTORY = document_ingest_factory

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "project",
                "init",
                "--name",
                "legal-doc",
                "--domain",
                "legal",
                "--description",
                "cli ingest smoke",
            )
            assert code == 0, stderr
            created = parse_stdout_json(stdout)
            project_id = str(created["project"]["project_id"])

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "document",
                "ingest",
                "--project-id",
                project_id,
                "--text",
                "Article 5 governs the dispute. Therefore termination lacked a valid basis.",
            )
            assert code == 0, stderr
            ingested = parse_stdout_json(stdout)
            assert ingested["document_ingest"]["claims_added"] == 2
            assert ingested["document_ingest"]["source_document"]["source_kind"] == "inline_text"

            document_path = Path(tmp) / "complaint.md"
            document_path.write_text(
                "Section 1. Article 5 governs the dispute.\n"
                "Section 2. Article 5 governs the dispute.\n"
                "Termination lacked a valid basis.\n",
                encoding="utf-8",
            )
            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "document",
                "ingest",
                "--project-id",
                project_id,
                "--file",
                str(document_path),
            )
            assert code == 0, stderr
            file_ingested = parse_stdout_json(stdout)
            assert file_ingested["document_ingest"]["source_document"]["source_kind"] == "local_file"
            document_id = str(file_ingested["document_ingest"]["source_document"]["document_id"])
            first_imported_claim_id = str(file_ingested["document_ingest"]["claim_ids"][0])

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "document",
                "upload",
                "--project-id",
                project_id,
                "--name",
                "complaint-upload.txt",
                "--media-type",
                "text/plain",
                stdin_bytes=(
                    "Article 5 governs the dispute.\n"
                    "Termination lacked a valid basis.\n"
                ).encode("utf-8"),
            )
            assert code == 0, stderr
            uploaded = parse_stdout_json(stdout)
            assert uploaded["document_ingest"]["source_document"]["source_kind"] == "uploaded_file", uploaded

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "document",
                "source",
                "list",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            source_documents = parse_stdout_json(stdout)
            assert any(
                item["document_id"] == document_id for item in source_documents["source_documents"]
            ), source_documents
            assert any(
                item["source_kind"] == "uploaded_file" for item in source_documents["source_documents"]
            ), source_documents

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "document",
                "source",
                "show",
                "--project-id",
                project_id,
                "--document-id",
                document_id,
            )
            assert code == 0, stderr
            source_bundle = parse_stdout_json(stdout)
            assert source_bundle["source_mapping_bundle"]["artifact"]["source_document"]["document_id"] == document_id

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "reference",
                "list",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            reference_list = parse_stdout_json(stdout)
            assert reference_list["external_references"], reference_list
            reference_id = str(reference_list["external_references"][0]["reference_id"])

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "reference",
                "show",
                "--project-id",
                project_id,
                "--reference-id",
                reference_id,
            )
            assert code == 0, stderr
            reference_show = parse_stdout_json(stdout)
            assert reference_show["external_reference"]["reference_id"] == reference_id, reference_show

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "reference",
                "links",
                "--project-id",
                project_id,
                "--claim-id",
                first_imported_claim_id,
            )
            assert code == 0, stderr
            reference_links = parse_stdout_json(stdout)
            assert reference_links["claim_reference_links"], reference_links

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "reference",
                "backlinks",
                "--project-id",
                project_id,
                "--reference-id",
                reference_id,
            )
            assert code == 0, stderr
            reference_backlinks = parse_stdout_json(stdout)
            assert any(
                item["subject_kind"] == "claim"
                for item in reference_backlinks["reference_backlinks"]
            ), reference_backlinks

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "artifact",
                "show",
                "summary",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            assert "total_claims: 6" in stdout, stdout

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "claim-graph",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            graph = parse_stdout_json(stdout)
            assert graph["claim_graph"]["total_claims"] == 6, graph

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "artifact",
                "show",
                "claim-graph-revisions",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            revisions = parse_stdout_json(stdout)
            assert len(revisions["claim_graph_revisions"]) >= 1, revisions

        with tempfile.TemporaryDirectory() as tmp:
            cli_module.ENGINE_API_FACTORY = paper_evidence_factory

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "project",
                "init",
                "--name",
                "paper-evidence",
                "--domain",
                "academic",
                "--description",
                "cli evidence smoke",
            )
            assert code == 0, stderr
            created = parse_stdout_json(stdout)
            project_id = str(created["project"]["project_id"])

            document_path = Path(tmp) / "paper.md"
            document_path.write_text(
                "The baseline and guardrailed review pipelines use the same ticket stream.\n"
                "Under the guarded policy, median review latency falls from 18 minutes to 11 minutes.\n"
                "The escalation policy remains unchanged while the guardrail is active.\n",
                encoding="utf-8",
            )
            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "document",
                "ingest",
                "--project-id",
                project_id,
                "--file",
                str(document_path),
            )
            assert code == 0, stderr
            ingested = parse_stdout_json(stdout)
            assert ingested["document_ingest"]["evaluation_evidence_added"] == 1, ingested
            claim_id = str(ingested["document_ingest"]["claim_ids"][1])

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "evidence",
                "list",
                "--project-id",
                project_id,
            )
            assert code == 0, stderr
            evidence_list = parse_stdout_json(stdout)
            assert len(evidence_list["evaluation_evidence"]) == 1, evidence_list
            evidence_id = str(evidence_list["evaluation_evidence"][0]["evidence_id"])

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "evidence",
                "show",
                "--project-id",
                project_id,
                "--evidence-id",
                evidence_id,
            )
            assert code == 0, stderr
            evidence_show = parse_stdout_json(stdout)
            assert evidence_show["evaluation_evidence"]["metric_name"] == "median review latency", evidence_show
            reference_id = str(
                evidence_show["evaluation_evidence"]["linked_reference_ids"][0]
            )

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "evidence",
                "links",
                "--project-id",
                project_id,
                "--claim-id",
                claim_id,
            )
            assert code == 0, stderr
            claim_evidence_links = parse_stdout_json(stdout)
            assert claim_evidence_links["claim_evidence_links"], claim_evidence_links

            code, stdout, stderr = run_cli(
                "--data-dir",
                tmp,
                "--format",
                "json",
                "evidence",
                "links",
                "--project-id",
                project_id,
                "--reference-id",
                reference_id,
            )
            assert code == 0, stderr
            reference_evidence_links = parse_stdout_json(stdout)
            assert reference_evidence_links["reference_evidence_links"], reference_evidence_links
    finally:
        cli_module.ENGINE_API_FACTORY = original_factory


if __name__ == "__main__":
    main()
