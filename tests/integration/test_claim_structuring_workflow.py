"""Integration smoke for explicit claim-structuring workflow states."""

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
    raise RuntimeError("Could not locate monorepo root from integration test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.config import PipelineConfig  # noqa: E402
from formal_claim_engine.llm_client import LLMClient  # noqa: E402
from formal_claim_engine.orchestrator import PipelineOrchestrator  # noqa: E402
from formal_claim_engine.claim_structuring_workflow import (  # noqa: E402
    ClaimStructuringStage,
)
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


def load_valid_claim_graph(project_id: str) -> dict:
    payload = json.loads(
        (REPO_ROOT / "examples" / "theorem-audit" / "claim-graph.json").read_text(
            encoding="utf-8"
        )
    )
    payload["project_id"] = project_id
    payload["graph_id"] = f"cg.{project_id.split('.')[-1]}"
    return payload


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = PipelineConfig(
            project_id="project.workflow_smoke",
            data_dir=tmp,
            max_retries_per_phase=2,
        )
        orchestrator = PipelineOrchestrator(config, llm=DummyLLM())
        orchestrator.planner = StubAgent(
            [
                {
                    "action": "admit_claims",
                    "claim_graph_update": None,
                    "promotion_decisions": None,
                    "work_requests": None,
                    "rationale": "Admit a new claim graph draft.",
                    "warnings": [],
                }
            ],
            role="planner",
        )
        orchestrator.claim_graph_agent = StubAgent(
            [
                {"graph_id": "cg.invalid"},
                load_valid_claim_graph(config.project_id),
            ],
            role="claim_graph_agent",
        )

        claim_graph = asyncio.run(
            orchestrator.phase1_claim_structuring("Structure a convergence theorem.")
        )
        workflow = orchestrator.last_claim_structuring_workflow
        assert workflow is not None
        assert workflow.state == ClaimStructuringStage.admitted
        assert workflow.planner_prompt_lineage is not None
        assert canonical_artifact_id(claim_graph.graph_id) == workflow.admitted_graph_id
        assert len(workflow.attempts) == 2, workflow.attempts
        assert workflow.attempts[0].validation_errors, workflow.attempts[0]
        assert workflow.attempts[0].prompt_lineage is not None
        assert not workflow.attempts[1].validation_errors, workflow.attempts[1]
        assert (
            "schema validation" in orchestrator.claim_graph_agent.contexts[1]["planner_guidance"]
        )
        review_events = orchestrator.store.query_review_events(config.project_id)
        assert any(
            event["event_type"] == "claim_structuring_attempt"
            and event["metadata"]["prompt_lineage"]["prompt_identifier"]
            == "test.claim_graph_agent"
            for event in review_events
        ), review_events

        rejection = PipelineOrchestrator(config, llm=DummyLLM())
        rejection.planner = StubAgent(
            [
                {
                    "action": "status_summary",
                    "claim_graph_update": None,
                    "promotion_decisions": None,
                    "work_requests": None,
                    "rationale": "No admissible structuring action yet.",
                    "warnings": ["Need clarification first."],
                }
            ],
            role="planner",
        )
        rejection.claim_graph_agent = StubAgent([], role="claim_graph_agent")

        try:
            asyncio.run(rejection.phase1_claim_structuring("Do nothing yet."))
        except RuntimeError as exc:
            assert "does not admit claim-structuring progression" in str(exc)
        else:  # pragma: no cover - failure path
            raise AssertionError("Planner rejection should fail the workflow.")

        rejected_workflow = rejection.last_claim_structuring_workflow
        assert rejected_workflow is not None
        assert rejected_workflow.state == ClaimStructuringStage.failed
        assert rejected_workflow.failure_reason is not None

        normalized = PipelineOrchestrator(config, llm=DummyLLM())
        normalized.planner = StubAgent(
            [
                {
                    "action": "admit_claims",
                    "claim_graph_update": None,
                    "promotion_decisions": None,
                    "work_requests": None,
                    "rationale": "Admit a normalized claim graph draft.",
                    "warnings": [],
                }
            ],
            role="planner",
        )
        normalized.claim_graph_agent = StubAgent(
            [
                {
                    "claims": [
                        {
                            "title": "Main holding",
                            "statement": "The appellate court should reverse the judgment.",
                            "claim_class": "holding",
                            "claim_kind": "theorem",
                            "status": "Proposed",
                            "formalization_required": True,
                            "downstream_kind": "Research_Only",
                            "source_location": "document:input#p1",
                            "source_text": "The appellate court should reverse the judgment.",
                            "span_start": 11,
                            "span_end": 63,
                        }
                    ],
                    "relations": [],
                }
            ],
            role="claim_graph_agent",
        )
        normalized_graph = asyncio.run(
            normalized.phase1_claim_structuring("Normalize a malformed claim graph.")
        )
        normalized_claim = normalized_graph.claims[0]
        assert canonical_artifact_id(normalized_graph.graph_id).startswith("cg."), normalized_graph
        assert normalized_graph.root_claim_ids, normalized_graph
        assert normalized_claim.claim_class.value == "core_claim", normalized_claim
        assert normalized_claim.claim_kind.value == "theorem_candidate", normalized_claim
        assert normalized_claim.nl_statement == "The appellate court should reverse the judgment.", normalized_claim
        assert normalized_claim.downstream_kind.value == "research_only", normalized_claim
        assert normalized_claim.provenance.source_anchors[0].source_ref == "document:input#p1", normalized_claim
        assert normalized_claim.provenance.source_anchors[0].excerpt == "The appellate court should reverse the judgment.", normalized_claim


if __name__ == "__main__":
    main()
