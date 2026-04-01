"""Smoke tests for the canonical graph query package."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "packages" / "graph-model" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from graph query test.")


REPO_ROOT = resolve_repo_root()
GRAPH_MODEL_SRC = REPO_ROOT / "packages" / "graph-model" / "src"

if str(GRAPH_MODEL_SRC) not in sys.path:
    sys.path.insert(0, str(GRAPH_MODEL_SRC))

from formal_claim_graph import (  # noqa: E402
    AssuranceGraph,
    AssuranceGraphQueries,
    ClaimGraph,
    ClaimGraphQueries,
    summarize_theorem_trust,
    diff_assurance_graphs,
    diff_claim_graphs,
)


def main() -> None:
    claim_graph = ClaimGraph.model_validate(
        json.loads(
            (REPO_ROOT / "examples" / "theorem-audit" / "claim-graph.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assurance_graph = AssuranceGraph.model_validate(
        json.loads(
            (REPO_ROOT / "examples" / "theorem-audit" / "assurance-graph.json").read_text(
                encoding="utf-8"
            )
        )
    )

    claim_queries = ClaimGraphQueries(claim_graph)
    assert claim_queries.roots() == ["claim.dispatch.driver_assignment_converges"]
    assert set(claim_queries.leaves()) == {
        "claim.dispatch.dispatch_model_defined",
        "claim.dispatch.finite_decrease_measure",
    }
    assert set(
        claim_queries.dependency_closure(
            ["claim.dispatch.driver_assignment_converges"]
        )
    ) == {
        "claim.dispatch.driver_assignment_converges",
        "claim.dispatch.matching_step_wellformed",
        "claim.dispatch.finite_decrease_measure",
        "claim.dispatch.dispatch_model_defined",
    }

    impact = claim_queries.impact_analysis(["claim.dispatch.dispatch_model_defined"])
    assert impact.directly_affected_claim_ids == [
        "claim.dispatch.matching_step_wellformed"
    ]
    assert impact.transitively_affected_claim_ids == [
        "claim.dispatch.matching_step_wellformed",
        "claim.dispatch.driver_assignment_converges",
    ]
    assert "claim.dispatch.driver_assignment_converges" in claim_queries.hotspot_claim_ids(
        limit=2
    )

    projection = claim_queries.project(
        [
            "claim.dispatch.driver_assignment_converges",
            "claim.dispatch.matching_step_wellformed",
        ]
    )
    assert len(projection.data["claims"]) == 2
    assert len(projection.data["relations"]) == 1

    claim_graph_changed = deepcopy(claim_graph.model_dump(mode="json", exclude_none=True))
    claim_graph_changed["claims"][0]["title"] = "Dispatch model is explicitly defined"
    claim_graph_changed["relations"].append(
        {
            "relation_id": "rel.dispatch.model_motivates_measure",
            "from_claim_id": "claim.dispatch.finite_decrease_measure",
            "to_claim_id": "claim.dispatch.dispatch_model_defined",
            "relation_type": "motivates",
            "status": "active",
        }
    )
    claim_diff = diff_claim_graphs(claim_graph, ClaimGraph.model_validate(claim_graph_changed))
    assert claim_diff.changed_claim_ids == ["claim.dispatch.dispatch_model_defined"]
    assert claim_diff.added_relation_ids == ["rel.dispatch.model_motivates_measure"]

    assurance_queries = AssuranceGraphQueries(assurance_graph)
    assert assurance_queries.formal_artifacts_for_claim(
        "claim.dispatch.driver_assignment_converges"
    ) == ["node.formal.dispatch.driver_assignment_converges"]
    assert assurance_queries.evidence_for_claim(
        "claim.dispatch.driver_assignment_converges"
    ) == ["node.evidence.simulation.convergence_10000"]
    assert assurance_queries.review_nodes_for_target(
        "node.formal.dispatch.driver_assignment_converges"
    ) == ["node.review.backtranslation.driver_assignment_converges"]
    assert "node.formal.dispatch.driver_assignment_converges" in assurance_queries.hotspot_node_ids(
        limit=3
    )

    assurance_graph_changed = deepcopy(
        assurance_graph.model_dump(mode="json", exclude_none=True)
    )
    assurance_graph_changed["edges"].append(
        {
            "edge_id": "edge.runtime_guard.tests.locale",
            "source_id": "node.contract.runtime.convergence_timeout_guard",
            "target_id": "node.formal.dispatch.dispatch_context",
            "relation_type": "tests",
            "status": "active",
        }
    )
    assurance_diff = diff_assurance_graphs(
        assurance_graph,
        AssuranceGraph.model_validate(assurance_graph_changed),
    )
    assert assurance_diff.added_edge_ids == ["edge.runtime_guard.tests.locale"]

    trust_summary = summarize_theorem_trust(
        {
            "surface": {
                "target_theorem": "driver_assignment_converges",
                "global_axiom_ids": ["axiom.choice"],
                "reviewed_global_axiom_ids": ["axiom.choice"],
                "oracle_ids": ["oracle.fastforce"],
                "locale_assumptions": ["dispatch_context.finite_drivers"],
                "premise_assumptions": ["premise.pending_jobs"],
                "reviewed_exception_ids": ["review.exception.dispatch_context"],
                "transitive_theorem_dependencies": [
                    "dispatch_context.intro",
                    "finite_measure_decreases",
                ],
                "imported_theory_hotspots": [
                    "node.formal.dispatch.dispatch_context"
                ],
                "notes": ["theorem-local trust surface present"],
            }
        }
    )
    assert trust_summary.oracle_dependency_count == 1
    assert trust_summary.reviewed_exception_count == 1
    assert trust_summary.hotspot_artifact_ids == [
        "node.formal.dispatch.dispatch_context"
    ]


if __name__ == "__main__":
    main()
