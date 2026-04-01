"""Integration smoke for FWP-backed deterministic audit workflow."""

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
    raise RuntimeError("Could not locate monorepo root from audit workflow test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.config import PipelineConfig  # noqa: E402
from formal_claim_engine.llm_client import LLMClient  # noqa: E402
from formal_claim_engine.models import ClaimGraph  # noqa: E402
from formal_claim_engine.orchestrator import PipelineOrchestrator  # noqa: E402
from formal_claim_engine.proof_protocol import FilesystemProofAdapter  # noqa: E402
from formal_claim_engine.store import canonical_artifact_id  # noqa: E402


class DummyLLM(LLMClient):
    async def complete(self, *args, **kwargs):  # pragma: no cover - defensive guard
        raise AssertionError("Audit workflow smoke should not hit a real LLM.")


class StubAgent:
    def __init__(self, outputs: list[dict]):
        self.outputs = list(outputs)
        self.contexts: list[dict] = []

    async def run(self, context: dict) -> dict:
        self.contexts.append(context)
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
                    "direct_theorem_dependencies": [
                        "dispatch_context.intro",
                        "finite_measure_decreases",
                    ],
                    "transitive_theorem_dependencies": [
                        "dispatch_context.intro",
                        "finite_measure_decreases",
                        "wf_measure",
                    ],
                    "dependency_edges": [],
                    "imported_theories": ["Dispatch_Model_A", "Finite_Set", "Main"],
                    "imported_theory_hotspots": [
                        "node.formal.dispatch.dispatch_context"
                    ],
                    "oracle_ids": ["oracle.fastforce"],
                    "global_axiom_ids": ["axiom.choice"],
                    "reviewed_global_axiom_ids": ["axiom.choice"],
                    "reviewed_exception_ids": [
                        "review.exception.dispatch_context"
                    ],
                    "locale_assumptions": [
                        "dispatch_context.finite_drivers",
                        "dispatch_context.total_assignment",
                    ],
                    "premise_assumptions": ["premise.pending_jobs"],
                    "notes": [
                        "export trust surface present",
                        "Oracle dependencies were detected in the theorem-local closure.",
                    ],
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
                "premise_sensitivity": "fragile",
                "conclusion_perturbation": "stable",
                "notes": [],
            },
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
    code: str,
) -> dict:
    return {
        "claim_id": claim_id,
        "formalizer": label,
        "proof_language": "lean",
        "proof_source": code,
        "session_name": f"Session_{label}",
        "module_name": f"Dispatch_Model_{label}",
        "primary_target": theorem,
        "theorem_statement": "True",
        "definition_names": ["helper"],
        "context_name": "dispatch_context" if label == "A" else None,
        "assumptions_used": [
            {"carrier": "locale", "statement": "finite drivers"}
        ]
        if label == "A"
        else [
            {"carrier": "premise", "statement": "finite drivers"},
            {"carrier": "premise", "statement": "strict decrease measure"},
        ],
        "back_translation": (
            "Stable assignment follows from a locale-scoped measure proof."
            if label == "A"
            else "Stable assignment follows from explicit premises on the ranking function."
        ),
        "divergence_notes": (
            "A packages the measure assumption inside a locale."
            if label == "A"
            else "B leaves the measure as an explicit premise."
        ),
        "open_obligation_locations": [],
        "confidence": 0.6,
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


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = PipelineConfig(
            project_id="project.audit_workflow",
            data_dir=tmp,
        )
        graph = load_claim_graph(config.project_id)
        claim = next(
            item
            for item in graph.claims
            if canonical_artifact_id(item.claim_id)
            == "claim.dispatch.driver_assignment_converges"
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
                    claim_id=canonical_artifact_id(claim.claim_id),
                    label="A",
                    theorem="driver_assignment_converges",
                    code=(
                        "theory Dispatch_Model_A imports Main\n"
                        "begin\n"
                        "lemma driver_assignment_converges:\n"
                        "  assumes KEEP_PREMISE: \"finite drivers\"\n"
                        "  shows \"True\"\n"
                        "  using KEEP_PREMISE\n"
                        "  by simp\n"
                        "end\n"
                    ),
                )
            ]
        )
        orchestrator.formalizer_b = StubAgent(
            [
                build_formalizer_output(
                    claim_id=canonical_artifact_id(claim.claim_id),
                    label="B",
                    theorem="driver_assignment_converges_alt",
                    code=(
                        "theory Dispatch_Model_B imports Main\n"
                        "begin\n"
                        "lemma driver_assignment_converges_alt:\n"
                        "  assumes KEEP_PREMISE: \"finite drivers\"\n"
                        "  shows \"True\"\n"
                        "  using KEEP_PREMISE\n"
                        "  by simp\n"
                        "end\n"
                    ),
                )
            ]
        )
        orchestrator.verifier = StubAgent(
            [
                build_verifier_output(
                    canonical_artifact_id(claim.claim_id),
                    "A",
                    "driver_assignment_converges",
                    proof_status="proof_complete",
                ),
                build_verifier_output(
                    canonical_artifact_id(claim.claim_id),
                    "B",
                    "driver_assignment_converges_alt",
                    proof_status="built",
                ),
            ]
        )

        result = asyncio.run(orchestrator.phase2_formalize_and_audit(graph, claim))

        assert result["audit_workflow"]["state"] == "completed", result["audit_workflow"]
        assert result["audit_workflow"]["selected_formalizer"] == "A", result["audit_workflow"]
        assert result["proof_audit"]["trust"]["success"] is True, result["proof_audit"]
        assert result["audit"]["trust_frontier"]["oracle_dependency_count"] == 1
        assert result["audit"]["intent_alignment"]["backtranslation_review"] == "needs_revision"
        assert result["profile"]["trust_frontier"]["oracle_dependency_count"] == 1
        assert result["profile"]["model_health"]["countermodel_probe"] == "no_countermodel_found"
        assert result["profile"]["model_health"]["premise_sensitivity"] == "fragile"
        assert result["profile"]["required_actions"], result["profile"]
        request_path = Path(result["audit_workflow"]["proof_request_path"])
        assert request_path.exists(), result["audit_workflow"]


if __name__ == "__main__":
    main()
