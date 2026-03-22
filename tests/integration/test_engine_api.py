"""Integration smoke for the typed engine API boundary."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from engine API test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine import (  # noqa: E402
    ArtifactStore,
    FormalClaimEngineAPI,
    PipelineConfig,
    PipelineOrchestrator,
    ReviewActorRole,
)
from formal_claim_engine.external_reference_registry import (  # noqa: E402
    reference_registry_artifact_id,
)
from formal_claim_engine.llm_client import LLMClient, LLMResponse  # noqa: E402
from formal_claim_engine.proof_protocol import FilesystemProofAdapter  # noqa: E402
from formal_claim_engine.store import canonical_artifact_id  # noqa: E402


class DummyLLM(LLMClient):
    async def complete(self, *args, **kwargs):  # pragma: no cover - defensive guard
        raise AssertionError("Engine API smoke should not hit a real LLM.")


class IngestStubLLM(LLMClient):
    def __init__(self, payload: dict):
        super().__init__()
        self.payload = payload

    async def complete(self, *args, **kwargs):
        return LLMResponse(text=json.dumps(self.payload), raw=None, usage=None)


class StubAgent:
    def __init__(self, outputs: list[dict]):
        self.outputs = list(outputs)

    async def run(self, context: dict) -> dict:
        if not self.outputs:
            raise AssertionError("StubAgent received more calls than expected.")
        output = self.outputs.pop(0)
        return {
            "role": "stub",
            "output": output,
            "raw_text": json.dumps(output, default=str),
            "usage": None,
        }


class FakeIsabelle:
    def write_theory(self, session_dir: str, theory_name: str, content: str):
        path = Path(session_dir)
        path.mkdir(parents=True, exist_ok=True)
        theory_path = path / f"{theory_name}.thy"
        theory_path.write_text(content, encoding="utf-8")
        return theory_path

    def write_root(self, session_dir: str, session_name: str, theories: list[str]):
        path = Path(session_dir) / "ROOT"
        path.write_text(
            f'session "{session_name}" = HOL +\n  theories\n    "{theories[0]}"\n',
            encoding="utf-8",
        )
        return path

    def build(self, session_name: str, session_dir: str):
        return type(
            "BuildResult",
            (),
            {
                "success": True,
                "stdout": f"theorem {session_name}_theorem\ndefinition helper\nlocale ctx",
                "stderr": "",
                "sorry_count": 0,
                "oops_count": 0,
                "sorry_locations": [],
                "theorems": [f"{session_name}_theorem"],
                "definitions": ["helper"],
                "locales": ["ctx"],
                "session_fingerprint": f"fp-{session_name}",
            },
        )()


class FakeRunnerCli:
    def run_audit(self, request_path: Path) -> dict:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        return {
            "success": True,
            "session_name": request["session_name"],
            "session_dir": request["session_dir"],
            "target_theorem": request["target_theorem"],
            "trust": {
                "success": True,
                "session": request["session_name"],
                "target_theorem": request["target_theorem"],
                "surface": {
                    "session": request["session_name"],
                    "target_theorem": request["target_theorem"],
                    "direct_theorem_dependencies": ["dispatch_context.intro"],
                    "transitive_theorem_dependencies": [
                        "dispatch_context.intro",
                        "finite_measure_decreases",
                    ],
                    "dependency_edges": [],
                    "imported_theories": ["Dispatch_Model_A", "Main"],
                    "imported_theory_hotspots": [],
                    "oracle_ids": [],
                    "global_axiom_ids": [],
                    "reviewed_global_axiom_ids": [],
                    "reviewed_exception_ids": [],
                    "locale_assumptions": ["dispatch_context.finite_drivers"],
                    "premise_assumptions": [],
                    "notes": ["export trust surface present"],
                },
                "export_output_dir": str(request_path.parent / "exports"),
                "dump_output_dir": str(request_path.parent / "dump"),
                "notes": [],
            },
            "probe_results": [
                {
                    "kind": "counterexample",
                    "session": f"{request['session_name']}_counterexample",
                    "target_theorem": request["target_theorem"],
                    "outcome": "no_countermodel_found",
                    "summary": "Counterexample probe found no countermodel.",
                },
                {
                    "kind": "proofSearch",
                    "session": f"{request['session_name']}_proofsearch",
                    "target_theorem": request["target_theorem"],
                    "outcome": "hints_available",
                    "summary": "Proof-search probe returned candidate hints.",
                    "hints": ["by simp"],
                },
            ],
            "robustness_harness": {
                "session": request["session_name"],
                "target_theorem": request["target_theorem"],
                "premise_sensitivity": "stable",
                "conclusion_perturbation": "stable",
                "notes": [],
            },
        }


def load_valid_claim_graph(project_id: str) -> dict:
    payload = json.loads(
        (REPO_ROOT / "examples" / "theorem-audit" / "claim-graph.json").read_text(
            encoding="utf-8"
        )
    )
    payload["project_id"] = project_id
    payload["graph_id"] = f"cg.{project_id.split('.')[-1]}"
    return payload


def build_formalizer_output(*, claim_id: str, label: str, theorem: str) -> dict:
    return {
        "claim_id": claim_id,
        "formalizer": label,
        "proof_language": "lean",
        "proof_source": (
            f"theory Dispatch_Model_{label} imports Main\n"
            "begin\n"
            f"lemma {theorem}:\n"
            "  assumes KEEP_PREMISE: \"finite drivers\"\n"
            "  shows \"True\"\n"
            "  using KEEP_PREMISE\n"
            "  by simp\n"
            "end\n"
        ),
        "session_name": f"Session_{label}",
        "module_name": f"Dispatch_Model_{label}",
        "primary_target": theorem,
        "theorem_statement": "True",
        "definition_names": ["helper"],
        "context_name": "dispatch_context" if label == "A" else None,
        "assumptions_used": [
            {"carrier": "locale", "statement": "finite drivers"}
        ],
        "back_translation": "Stable assignment follows from a finite-driver invariant.",
        "divergence_notes": "",
        "open_obligation_locations": [],
        "confidence": 0.7,
    }


def build_verifier_output(claim_id: str, label: str, theorem: str, *, proof_status: str) -> dict:
    return {
        "claim_id": claim_id,
        "formalizer": label,
        "proof_language": "lean",
        "build_success": True,
        "build_log_summary": "build passed",
        "errors": [],
        "warnings": [],
        "targets_found": [theorem],
        "definitions_found": ["helper"],
        "contexts_found": ["ctx"],
        "open_obligation_count": 0,
        "open_obligation_locations": [],
        "dependency_count": 1,
        "session_fingerprint": f"fp-{label}",
        "proof_status": proof_status,
        "formal_artifact": {
            "node_id": f"node.formal.dispatch.driver_assignment_converges.{label}",
            "node_type": "formal_artifact",
            "system": "lean",
            "artifact_kind": "theorem",
            "identifier": theorem,
            "session": f"Session_{label}",
            "module": f"Dispatch_Model_{label}",
            "status": "active",
            "proof_status": proof_status,
        },
    }


def build_orchestrator(config: PipelineConfig, llm: LLMClient, store: ArtifactStore) -> PipelineOrchestrator:
    orchestrator = PipelineOrchestrator(config, llm=llm, store=store)
    orchestrator.proof_client = FilesystemProofAdapter(
        config,
        builder=FakeIsabelle(),
        audit_client=FakeRunnerCli(),
    )
    orchestrator.planner = StubAgent(
        [
            {
                "action": "admit_claims",
                "rationale": "Planner admitted the canonical theorem-audit ClaimGraph.",
                "warnings": [],
                "claim_graph_update": load_valid_claim_graph(config.project_id),
            }
        ]
    )
    orchestrator.claim_graph_agent = StubAgent([])
    orchestrator.formalizer_a = StubAgent(
        [
            build_formalizer_output(
                claim_id="claim.dispatch.driver_assignment_converges",
                label="A",
                theorem="driver_assignment_converges",
            )
        ]
    )
    orchestrator.formalizer_b = StubAgent(
        [
            build_formalizer_output(
                claim_id="claim.dispatch.driver_assignment_converges",
                label="B",
                theorem="driver_assignment_converges_alt",
            )
        ]
    )
    orchestrator.verifier = StubAgent(
        [
            build_verifier_output(
                "claim.dispatch.driver_assignment_converges",
                "A",
                "driver_assignment_converges",
                proof_status="proof_complete",
            ),
            build_verifier_output(
                "claim.dispatch.driver_assignment_converges",
                "B",
                "driver_assignment_converges_alt",
                proof_status="built",
            ),
        ]
    )
    return orchestrator


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        api = FormalClaimEngineAPI(
            config=PipelineConfig(data_dir=tmp),
            llm=DummyLLM(),
            orchestrator_factory=build_orchestrator,
        )
        project = api.create_project("dispatch-proof", "formal_proof", "engine api smoke")
        assert project.project_id.startswith("proj."), project

        opened = api.open_project(project.project_id)
        assert opened.claim_count == 0, opened

        structuring = asyncio.run(
            api.run_claim_structuring(
                project.project_id,
                "Prove that the dispatch algorithm converges.",
            )
        )
        assert structuring.workflow["state"] == "admitted", structuring
        assert structuring.project.claim_count >= 1, structuring.project
        revisions = api.list_graph_revisions(project.project_id)
        assert revisions, revisions
        revision_payload = api.load_graph_revision(
            project.project_id,
            revisions[-1]["revision_id"],
        )
        assert revision_payload["artifact"]["project_id"] == project.project_id
        graph_signals = api.get_graph_signal_overlays(project.project_id)
        assert "claim.dispatch.driver_assignment_converges" in graph_signals, graph_signals

        audit = asyncio.run(
            api.run_audit(
                project.project_id,
                "claim.dispatch.driver_assignment_converges",
            )
        )
        assert audit.audit_workflow["state"] == "completed", audit
        assert audit.profile["claim_id"] == "claim.dispatch.driver_assignment_converges"
        assert audit.promotion_state["current_gate"] == "draft", audit.promotion_state
        profile_payload = api.get_assurance_profile(project.project_id, audit.claim_id)
        assert profile_payload["claim_id"] == audit.claim_id, profile_payload
        assert profile_payload["proofClaim"]["scoreMethod"] == "qbaf_df_quad", profile_payload
        assert profile_payload["proofClaim"]["scoreBreakdownRef"].endswith(
            "#proofClaimBreakdown"
        ), profile_payload
        audit_report = api.get_latest_audit_report(project.project_id, audit.claim_id)
        assert audit_report["event_type"] == "audit_workflow", audit_report
        review_events = api.list_review_events(project.project_id, audit.claim_id)
        assert review_events, review_events
        promotion_state = api.get_promotion_state(project.project_id, audit.claim_id)
        assert promotion_state["current_gate"] == "draft", promotion_state

        analyzed = asyncio.run(
            api.analyze_claim(
                project.project_id,
                "claim.dispatch.driver_assignment_converges",
            )
        )
        assert analyzed.analysis_mode == "audit_workflow", analyzed
        assert analyzed.profile["proofClaim"]["scoreMethod"] == "qbaf_df_quad", analyzed

        recomputed = api.recompute_profile(
            project.project_id,
            audit.claim_id,
            audit,
            research_output={
                "overall_assessment": "Simulation supports the claim under the declared assumptions.",
                "recommended_support_status": "simulation_supported",
                "evidence_items": [],
            },
        )
        assert recomputed.profile["claim_id"] == audit.claim_id, recomputed

        blocked = api.approve_promotion(
            project.project_id,
            audit.claim_id,
            target_gate="blocked",
            actor="human.reviewer",
            actor_role=ReviewActorRole.reviewer,
            notes="Quarantine due to unresolved blockers.",
        )
        assert blocked.current_gate.value == "blocked", blocked

        bundle = api.export_bundle(project.project_id)
        assert bundle.project.project_id == project.project_id, bundle
        assert bundle.claim_graph["project_id"] == project.project_id, bundle.claim_graph
        assert bundle.assurance_profiles, bundle
        assert audit.claim_id in bundle.review_events, bundle.review_events
        assert bundle.promotion_states[audit.claim_id]["current_gate"] == "blocked"

    with tempfile.TemporaryDirectory() as tmp:
        api = FormalClaimEngineAPI(
            config=PipelineConfig(data_dir=tmp),
            llm=IngestStubLLM(
                {
                    "claims": [
                        {
                            "id": "premise_a",
                            "title": "Article 5 applies",
                            "statement": "Article 5 governs the dispute under the stated jurisdictional facts.",
                            "role": "statute",
                            "source_text": "Article 5 governs the dispute.",
                            "scope": "jurisdictional dispute",
                            "depth": 0,
                        },
                        {
                            "id": "claim_b",
                            "title": "Termination was unauthorized",
                            "statement": "Termination lacked a valid Article 5 basis.",
                            "role": "holding",
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
            ),
        )
        project = api.create_project("legal-import", "legal", "engine api document import")
        document_path = Path(tmp) / "complaint.md"
        document_path.write_text(
            "Section 1. Article 5 governs the dispute.\n"
            "Section 2. Article 5 governs the dispute.\n"
            "Termination lacked a valid basis.\n",
            encoding="utf-8",
        )
        imported = asyncio.run(
            api.import_local_document(project.project_id, str(document_path))
        )
        assert imported.source_document["source_kind"] == "local_file", imported
        assert imported.evaluation_evidence_added == 0, imported
        uploaded = asyncio.run(
            api.upload_document_bytes(
                project.project_id,
                file_name="complaint-upload.txt",
                raw_bytes=(
                    "Section 1. Article 5 governs the dispute.\n"
                    "Termination lacked a valid basis.\n"
                ).encode("utf-8"),
                media_type="text/plain",
            )
        )
        assert uploaded.source_document["source_kind"] == "uploaded_file", uploaded
        source_documents = api.list_source_documents(project.project_id)
        assert len(source_documents) == 2, source_documents
        bundle = api.get_source_mapping_bundle(
            project.project_id,
            imported.source_document["document_id"],
        )
        assert bundle["artifact"]["source_document"]["document_id"] == imported.source_document["document_id"], bundle
        assert bundle["artifact"]["mapping_report"]["ambiguous_anchor_count"] == 1, bundle
        api.claim_trace_service.repository.artifact_store.append_review_event(
            target_claim_id=imported.claim_ids[0],
            artifact_kind="review_note",
            artifact_id="reference.registry.review",
            event_type="manual_review",
            actor="operator.reference-review",
            actor_role="reviewer",
            notes="Operator confirmed the imported anchor metadata.",
            metadata={"summary": "reference review"},
        )
        references = api.list_external_references(project.project_id)
        assert references, references
        first_reference_id = str(references[0]["reference_id"])
        reference = api.get_external_reference(project.project_id, first_reference_id)
        assert reference["reference_id"] == first_reference_id, reference
        claim_links = api.get_claim_reference_links(
            project.project_id,
            imported.claim_ids[0],
        )
        assert claim_links, claim_links
        backlinks = api.get_reference_backlinks(project.project_id, first_reference_id)
        assert any(item["subject_kind"] == "claim" for item in backlinks), backlinks
        assurance_links = api.list_assurance_links(
            project.project_id,
            imported.claim_ids[0],
        )
        assert any(item["subject_kind"] == "review_event" for item in assurance_links), assurance_links
        registry_latest = api.claim_trace_service.repository.artifact_store.get_latest_artifact(
            "external_reference_registries",
            reference_registry_artifact_id(project.project_id),
        )
        assert registry_latest is not None, registry_latest

    with tempfile.TemporaryDirectory() as tmp:
        api = FormalClaimEngineAPI(
            config=PipelineConfig(data_dir=tmp),
            llm=IngestStubLLM(
                {
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
            ),
        )
        project = api.create_project("paper-import", "academic", "engine api evaluation evidence")
        document_path = Path(tmp) / "paper.md"
        document_path.write_text(
            "The baseline and guardrailed review pipelines use the same ticket stream.\n"
            "Under the guarded policy, median review latency falls from 18 minutes to 11 minutes.\n"
            "The escalation policy remains unchanged while the guardrail is active.\n",
            encoding="utf-8",
        )
        imported = asyncio.run(
            api.import_local_document(project.project_id, str(document_path))
        )
        assert imported.evaluation_evidence_added == 1, imported
        evidence_items = api.list_evaluation_evidence(project.project_id)
        assert len(evidence_items) == 1, evidence_items
        evidence = evidence_items[0]
        assert evidence["metric_name"] == "median review latency", evidence
        assert evidence["baseline_value"] == 18.0, evidence
        assert evidence["reported_value"] == 11.0, evidence
        loaded_evidence = api.get_evaluation_evidence(
            project.project_id,
            evidence["evidence_id"],
        )
        assert loaded_evidence["evidence_id"] == evidence["evidence_id"], loaded_evidence
        claim_links = api.get_claim_evidence_links(project.project_id, imported.claim_ids[1])
        assert claim_links, claim_links
        reference_id = str((evidence.get("linked_reference_ids") or [None])[0])
        assert reference_id, evidence
        reference_links = api.get_reference_evidence_links(
            project.project_id,
            reference_id,
        )
        assert reference_links, reference_links
        backlinks = api.get_reference_backlinks(project.project_id, reference_id)
        assert any(item["subject_kind"] == "evaluation_evidence" for item in backlinks), backlinks
        bundle = api.export_bundle(project.project_id)
        assert len(bundle.evaluation_evidence) == 1, bundle


if __name__ == "__main__":
    main()
