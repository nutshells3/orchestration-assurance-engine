"""Smoke tests for deterministic assurance-profile computation."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "packages" / "audit-rules" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from audit-rules test.")


REPO_ROOT = resolve_repo_root()
AUDIT_RULES_SRC = REPO_ROOT / "packages" / "audit-rules" / "src"
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

for path in (AUDIT_RULES_SRC, ENGINE_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from formal_claim_audit_rules import (  # noqa: E402
    AssuranceComputationInput,
    compute_assurance_profile,
    emit_contract_pack,
    validate_promotion_rules,
)
from formal_claim_contracts.claim_graph import ClaimGraph  # noqa: E402


def build_input(claim_graph: ClaimGraph, *, sorry_count: int = 0, countermodel: str = "no_countermodel_found"):
    claim = next(
        claim.model_dump(mode="json", exclude_none=True)
        for claim in claim_graph.claims
        if str(claim.claim_id.root) == "claim.dispatch.driver_assignment_converges"
    )
    verifier_output = {
        "A": {
            "build_success": True,
            "proof_status": "proof_complete",
            "sorry_count": sorry_count,
            "oops_count": 0,
            "open_goal_count": 0,
            "warnings": [],
            "session_fingerprint": "fp-a",
            "theorems_found": ["driver_assignment_converges"],
            "formal_artifact": {
                "node_id": "node.formal.dispatch.driver_assignment_converges",
                "system": "isabelle_hol",
                "session": "Dispatch",
                "theory": "Dispatch_Model_A",
                "identifier": "driver_assignment_converges",
            },
        },
        "B": {
            "build_success": True,
            "proof_status": "built",
            "sorry_count": 0,
            "oops_count": 0,
            "open_goal_count": 0,
            "warnings": [],
            "session_fingerprint": "fp-b",
            "theorems_found": ["driver_assignment_converges_alt"],
            "formal_artifact": {
                "node_id": "node.formal.dispatch.driver_assignment_converges.alt",
                "system": "isabelle_hol",
                "session": "Dispatch",
                "theory": "Dispatch_Model_B",
                "identifier": "driver_assignment_converges_alt",
            },
        },
    }
    audit_output = {
        "trust_frontier": {
            "global_axiom_dependency_count": 0,
            "locale_assumption_count": 3,
            "premise_assumption_count": 0,
            "oracle_dependency_count": 0,
            "unreviewed_import_count": 0,
            "transitive_dependency_count": 14,
            "reviewed_global_axiom_ids": [],
            "oracle_ids": [],
            "hotspot_artifact_ids": ["node.formal.dispatch.dispatch_context"],
            "notes": [],
        },
        "conservativity": {
            "definitional_only": True,
            "reviewed_global_axioms_required": False,
            "compile_away_known": True,
            "nondefinitional_hotspots": [],
            "trusted_mechanisms": ["definition", "locale", "theorem"],
            "flagged_mechanisms": [],
        },
        "model_health": {
            "locale_satisfiability": "pass",
            "countermodel_probe": countermodel,
            "vacuity_check": "pass",
            "premise_sensitivity": "stable",
            "conclusion_perturbation": "stable",
            "notes": [],
        },
        "intent_alignment": {
            "independent_formalization_count": 2,
            "agreement_score": 0.91,
            "backtranslation_review": "pass",
            "paraphrase_robustness_score": 0.84,
            "semantics_guard_violations": [],
            "reviewer_notes": [],
        },
        "blocking_issues": [],
        "warnings": [],
    }
    research_output = {
        "overall_assessment": "Simulation supports the claim under the declared assumptions.",
        "recommended_support_status": "simulation_supported",
        "evidence_items": [
            {
                "node_id": "node.evidence.simulation.convergence_10000",
                "node_type": "evidence",
                "title": "10,000-run convergence simulation",
                "summary": "Supportive simulation.",
                "evidence_kind": "simulation",
                "result_polarity": "supports",
                "artifact_refs": ["results/convergence_10000.json"],
                "confidence": 0.97,
                "status": "active",
            }
        ],
    }
    return AssuranceComputationInput(
        project_id="project.agent_ide",
        claim=claim,
        verifier_output=verifier_output,
        audit_output=audit_output,
        research_output=research_output,
        claim_graph=claim_graph,
        claim_graph_ref="cg.dispatch",
    )


def main() -> None:
    claim_graph = ClaimGraph.model_validate(
        json.loads(
            (REPO_ROOT / "examples" / "theorem-audit" / "claim-graph.json").read_text(
                encoding="utf-8"
            )
        )
    )

    certified = compute_assurance_profile(build_input(claim_graph))
    assert certified.gate.value == "certified"
    assert certified.formal_status.value == "proof_complete"
    assert certified.support_status.value == "simulation_supported"
    assert 0 <= certified.proofClaim.score <= 100
    assert certified.proofClaim.score >= 80
    assert certified.proofClaim.scoreMethod == "qbaf_df_quad"
    assert certified.proofClaim.scoreVersion == "1.0.0"
    assert certified.proofClaim.scoreBreakdownRef.endswith("#proofClaimBreakdown")
    assert (
        certified.proofClaimBreakdown.aggregateFinalScore
        >= certified.proofClaimBreakdown.aggregateBaseScore
    )
    assert {
        dimension.dimension.value
        for dimension in certified.proofClaimBreakdown.dimensions
    } == {
        "trust_base_integrity",
        "intent_alignment",
        "evidence_support",
        "coverage",
        "robustness",
    }
    assert not validate_promotion_rules(
        certified.model_dump(mode="json", exclude_none=True)
    )

    blocked = compute_assurance_profile(
        build_input(claim_graph, sorry_count=1, countermodel="countermodel_found")
    )
    assert blocked.gate.value == "blocked"
    violations = validate_promotion_rules(blocked.model_dump(mode="json", exclude_none=True))
    assert not violations
    assert any("countermodel" in action.lower() for action in blocked.required_actions)

    base_input = build_input(claim_graph)
    runner_driven = compute_assurance_profile(
        AssuranceComputationInput(
            project_id=base_input.project_id,
            claim=base_input.claim,
            verifier_output=base_input.verifier_output,
            audit_output=base_input.audit_output,
            research_output=base_input.research_output,
            coverage_data=base_input.coverage_data,
            claim_graph=base_input.claim_graph,
            claim_graph_ref=base_input.claim_graph_ref,
            runner_trust={
                "surface": {
                    "target_theorem": "driver_assignment_converges",
                    "global_axiom_ids": ["axiom.choice"],
                    "reviewed_global_axiom_ids": ["axiom.choice"],
                    "oracle_ids": [],
                    "locale_assumptions": [
                        "dispatch_context.finite_drivers",
                        "dispatch_context.total_assignment",
                    ],
                    "premise_assumptions": ["premise.pending_jobs"],
                    "reviewed_exception_ids": ["review.exception.dispatch_context"],
                    "transitive_theorem_dependencies": [
                        "dispatch_context.intro",
                        "finite_measure_decreases",
                    ],
                    "imported_theory_hotspots": [
                        "node.formal.dispatch.dispatch_context"
                    ],
                    "notes": ["runner theorem-local trust surface present"],
                }
            },
            probe_results=[
                {
                    "kind": "counterexample",
                    "outcome": "countermodel_found",
                    "summary": "Counterexample probe reported a countermodel.",
                }
            ],
            robustness_harness={
                "premise_sensitivity": "fragile",
                "conclusion_perturbation": "stable",
                "notes": ["Premise deletion replay remained provable."],
            },
        )
    )
    assert runner_driven.gate.value == "blocked"
    assert runner_driven.model_health.countermodel_probe.value == "countermodel_found"
    assert runner_driven.model_health.vacuity_check.value == "fail"
    assert runner_driven.trust_frontier.global_axiom_dependency_count == 1
    assert runner_driven.trust_frontier.model_dump(mode="json")[
        "hotspot_artifact_ids"
    ] == ["node.formal.dispatch.dispatch_context"]
    assert runner_driven.proofClaim.score < certified.proofClaim.score

    lean_like_input = build_input(claim_graph)
    lean_like_input.verifier_output["A"]["formal_artifact"] = {
        "node_id": "node.formal.dispatch.driver_assignment_converges",
        "system": "lean4",
        "module": "Dispatch_Model_A",
        "identifier": "driver_assignment_converges",
    }
    lean_like_profile = compute_assurance_profile(lean_like_input)
    assert lean_like_profile.target_formal_artifact.session == "Dispatch_Model_A"
    assert lean_like_profile.target_formal_artifact.theory == "Dispatch_Model_A"

    contract_pack = emit_contract_pack(
        certified.model_dump(mode="json", exclude_none=True)
    )
    assert contract_pack.pack_id == "contract.claim.dispatch.driver_assignment_converges"
    assert contract_pack.allowed_downstream == [
        "research",
        "dev",
        "monitoring",
        "release",
    ]
    assert contract_pack.blocked_actions == ["ignore_required_actions"]
    assert contract_pack.referenced_artifact_ids == [
        "node.formal.dispatch.dispatch_context",
        "node.formal.dispatch.driver_assignment_converges",
    ]


if __name__ == "__main__":
    main()
