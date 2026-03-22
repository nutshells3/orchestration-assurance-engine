"""Integration smoke for dual formalization workflow and divergence capture."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from integration test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.config import PipelineConfig  # noqa: E402
from formal_claim_engine.dual_formalization_workflow import DualFormalizationStage  # noqa: E402
from formal_claim_engine.llm_client import LLMClient  # noqa: E402
from formal_claim_engine.models import ClaimGraph  # noqa: E402
from formal_claim_engine.orchestrator import PipelineOrchestrator  # noqa: E402
from formal_claim_engine.proof_protocol import FilesystemProofAdapter  # noqa: E402
from formal_claim_engine.store import canonical_artifact_id  # noqa: E402


class DummyLLM(LLMClient):
    async def complete(self, *args, **kwargs):  # pragma: no cover - defensive guard
        raise AssertionError("Workflow smoke should not reach the real LLM client.")


class StubAgent:
    def __init__(self, outputs: list[dict], *, role: str):
        self.outputs = list(outputs)
        self.role = role
        self.contexts: list[dict] = []

    async def run(self, context: dict) -> dict:
        self.contexts.append(context)
        if not self.outputs:
            raise AssertionError("StubAgent received more calls than expected.")
        output = self.outputs.pop(0)
        return {
            "role": self.role,
            "output": output,
            "raw_text": json.dumps(output, default=str),
            "usage": None,
            "lineage": {
                "prompt_identifier": f"test.{self.role}",
                "prompt_version": "1.0.0",
                "prompt_sha256": "1" * 64,
                "response_schema_id": f"schema.{self.role}",
                "response_schema_version": "1.0.0",
                "response_schema_sha256": "2" * 64,
                "provider_adapter_id": "test.adapter",
                "provider_adapter_version": "1.0.0",
                "provider": "test",
                "model": "stub",
            },
        }


class FailingAgent:
    def __init__(self, error: str):
        self.error = error
        self.contexts: list[dict] = []

    async def run(self, context: dict) -> dict:
        self.contexts.append(context)
        raise RuntimeError(self.error)


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
        stdout = f"theorem {session_name}_theorem\ndefinition helper\nlocale ctx"
        return SimpleNamespace(
            success=True,
            stdout=stdout,
            stderr="",
            sorry_count=0,
            oops_count=0,
            sorry_locations=[],
            theorems=[f"{session_name}_theorem"],
            definitions=["helper"],
            locales=["ctx"],
            session_fingerprint=f"fp-{session_name}",
        )


class FakeRunnerCli:
    def __init__(self):
        self.request_paths: list[Path] = []

    def run_audit(self, request_path: Path) -> dict:
        self.request_paths.append(request_path)
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
                        "wf_measure",
                    ],
                    "dependency_edges": [],
                    "imported_theories": ["Dispatch_Model_A", "Main"],
                    "imported_theory_hotspots": [],
                    "oracle_ids": [],
                    "global_axiom_ids": [],
                    "reviewed_global_axiom_ids": [],
                    "reviewed_exception_ids": [],
                    "locale_assumptions": ["dispatch_context.finite_drivers"],
                    "premise_assumptions": ["premise.pending_jobs"],
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
        }


def load_claim_graph(project_id: str) -> ClaimGraph:
    payload = json.loads(
        (REPO_ROOT / "examples" / "theorem-audit" / "claim-graph.json").read_text(
            encoding="utf-8"
        )
    )
    payload["project_id"] = project_id
    payload["graph_id"] = f"cg.{project_id.split('.')[-1]}"
    return ClaimGraph.model_validate(payload)


def build_formalizer_output(
    *,
    claim_id: str,
    label: str,
    theorem: str,
    back_translation: str,
    assumptions: list[dict[str, str]],
    divergence_notes: str,
) -> dict:
    return {
        "claim_id": claim_id,
        "formalizer": label,
        "proof_language": "lean",
        "proof_source": (
            f"theory {theorem} imports Main\nbegin\n\n"
            f"theorem {theorem}: True by simp\n\nend\n"
        ),
        "session_name": f"Session_{label}",
        "module_name": f"Theory_{label}",
        "primary_target": theorem,
        "theorem_statement": "True",
        "definition_names": ["helper"],
        "context_name": None,
        "assumptions_used": assumptions,
        "back_translation": back_translation,
        "divergence_notes": divergence_notes,
        "open_obligation_locations": [],
        "confidence": 0.6,
    }


def build_verifier_output(claim_id: str, label: str, theorem: str) -> dict:
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
        "proof_status": "built",
        "formal_artifact": {
            "node_id": f"artifact.{claim_id}.{label}",
            "node_type": "formal_artifact",
            "system": "lean4",
            "artifact_kind": "theorem",
            "identifier": theorem,
            "session": f"Session_{label}",
            "module": f"Theory_{label}",
            "status": "active",
            "proof_status": "built",
        },
    }


def canonical_id(value: object) -> str:
    return canonical_artifact_id(value)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = PipelineConfig(
            project_id="project.dual_formalization",
            data_dir=tmp,
        )
        graph = load_claim_graph(config.project_id)
        claim = next(
            item
            for item in graph.claims
            if canonical_id(item.claim_id) == "claim.dispatch.driver_assignment_converges"
        )

        orchestrator = PipelineOrchestrator(config, llm=DummyLLM())
        orchestrator.formalizer_a = StubAgent(
            [
                build_formalizer_output(
                    claim_id=canonical_id(claim.claim_id),
                    label="A",
                    theorem="dispatch_converges_A",
                    back_translation="Every dispatch execution reaches a stable state.",
                    assumptions=[
                        {"carrier": "premise", "statement": "finite dispatch state"},
                        {"carrier": "premise", "statement": "strict decrease measure"},
                    ],
                    divergence_notes="A used premise carriers.",
                )
            ],
            role="formalizer_a",
        )
        orchestrator.formalizer_b = FailingAgent("secondary formalizer crashed")

        workflow = asyncio.run(orchestrator.run_dual_formalization_workflow(graph, claim))
        assert workflow.state == DualFormalizationStage.completed
        assert workflow.divergence is not None
        assert workflow.divergence.classification == "single_success"
        assert workflow.successful_formalizers == ["A"], workflow
        assert workflow.failed_formalizers == ["B"], workflow
        review_events = orchestrator.store.query_review_events(canonical_id(claim.claim_id))
        attempt_events = [
            event for event in review_events
            if event["event_type"] == "formalization_attempt"
        ]
        assert len(attempt_events) == 2, review_events
        assert any(
            event["metadata"]["formalizer_label"] == "A"
            and event["metadata"]["claim_graph_id"] == canonical_id(graph.graph_id)
            and event["metadata"]["prompt_lineage"]["prompt_identifier"]
            == "test.formalizer_a"
            for event in attempt_events
        )

    with tempfile.TemporaryDirectory() as tmp:
        config = PipelineConfig(
            project_id="project.dual_formalization_phase2",
            data_dir=tmp,
        )
        graph = load_claim_graph(config.project_id)
        claim = next(
            item
            for item in graph.claims
            if canonical_id(item.claim_id) == "claim.dispatch.driver_assignment_converges"
        )

        orchestrator = PipelineOrchestrator(config, llm=DummyLLM())
        orchestrator.proof_client = FilesystemProofAdapter(
            config,
            builder=FakeIsabelle(),
            audit_client=FakeRunnerCli(),
        )
        orchestrator.formalizer_a = StubAgent(
            [
                build_formalizer_output(
                    claim_id=canonical_id(claim.claim_id),
                    label="A",
                    theorem="dispatch_converges_locale",
                    back_translation="Stable assignment follows from a locale-scoped measure proof.",
                    assumptions=[
                        {"carrier": "locale", "statement": "finite dispatch state"},
                    ],
                    divergence_notes="A packaged the measure inside a locale.",
                )
            ],
            role="formalizer_a",
        )
        orchestrator.formalizer_b = StubAgent(
            [
                build_formalizer_output(
                    claim_id=canonical_id(claim.claim_id),
                    label="B",
                    theorem="dispatch_converges_premise",
                    back_translation="Stable assignment follows from explicit premises on the ranking function.",
                    assumptions=[
                        {"carrier": "premise", "statement": "finite dispatch state"},
                        {"carrier": "premise", "statement": "strict decrease measure"},
                    ],
                    divergence_notes="B kept the measure as an explicit premise.",
                )
            ],
            role="formalizer_b",
        )
        orchestrator.verifier = StubAgent(
            [
                build_verifier_output(
                    canonical_id(claim.claim_id),
                    "A",
                    "dispatch_converges_locale",
                ),
                build_verifier_output(
                    canonical_id(claim.claim_id),
                    "B",
                    "dispatch_converges_premise",
                ),
            ],
            role="proof_verifier",
        )
        orchestrator.auditor = StubAgent(
            [
                {
                    "claim_id": canonical_id(claim.claim_id),
                    "audit_kind": "comparison",
                    "trust_frontier": {
                        "global_axiom_dependency_count": 0,
                        "locale_assumption_count": 1,
                        "premise_assumption_count": 1,
                        "oracle_dependency_count": 0,
                        "unreviewed_import_count": 0,
                        "transitive_dependency_count": 1,
                        "reviewed_global_axiom_ids": [],
                        "oracle_ids": [],
                        "hotspot_artifact_ids": [],
                        "notes": ["Divergence between locale and premise carriers was preserved."],
                    },
                    "conservativity": {
                        "definitional_only": True,
                        "reviewed_global_axioms_required": False,
                        "compile_away_known": False,
                        "nondefinitional_hotspots": [],
                        "trusted_mechanisms": ["locale assumptions"],
                        "flagged_mechanisms": [],
                    },
                    "model_health": {
                        "locale_satisfiability": "pass",
                        "countermodel_probe": "untested",
                        "vacuity_check": "untested",
                        "premise_sensitivity": "untested",
                        "conclusion_perturbation": "untested",
                        "notes": [],
                    },
                    "intent_alignment": {
                        "independent_formalization_count": 2,
                        "agreement_score": 0.55,
                        "backtranslation_review": "needs_revision",
                        "paraphrase_robustness_score": 0.5,
                        "semantics_guard_violations": [],
                        "reviewer_notes": [
                            "Divergence metadata should be reviewed before promotion."
                        ],
                    },
                    "blocking_issues": [],
                    "warnings": ["Independent formalizers diverged on assumption carriers."],
                    "recommendation": "needs_revision",
                }
            ],
            role="auditor",
        )

        result = asyncio.run(orchestrator.phase2_formalize_and_audit(graph, claim))
        workflow = result["dual_formalization_workflow"]
        assert workflow["state"] == DualFormalizationStage.completed.value, workflow
        assert workflow["divergence"]["classification"] == "diverged", workflow
        assert result["formalizer_a"]["primary_target"] == "dispatch_converges_locale"
        assert result["formalizer_b"]["primary_target"] == "dispatch_converges_premise"
        assert result["verifier_results"]["A"]["build_success"] is True
        assert result["verifier_results"]["B"]["build_success"] is True
        assert result["profile"]["formal_status"] in {
            "build_passed_no_proof",
            "proof_complete",
            "skeleton_only",
        }, result["profile"]


if __name__ == "__main__":
    main()
