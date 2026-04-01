"""Integration tests for PhaseAssembler and TraceExportBuilder.

Validates that phase2 and phase3 sections are correctly assembled
from ArtifactStore data, including partial-data scenarios.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from phase assembler test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine import (  # noqa: E402
    ArtifactStore,
    AssuranceProfile,
    ClaimGraph,
    Gate,
    PromotionStateMachine,
    ReviewActorRole,
)
from formal_claim_engine.phase_assembler import PhaseAssembler  # noqa: E402
from formal_claim_engine.trace_export import TraceExportBuilder  # noqa: E402
from formal_claim_engine.store import canonical_artifact_id  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = REPO_ROOT / "examples" / "theorem-audit"


def load_claim_graph() -> ClaimGraph:
    return ClaimGraph.model_validate(
        json.loads((FIXTURE_DIR / "claim-graph.json").read_text(encoding="utf-8"))
    )


def load_profile() -> AssuranceProfile:
    return AssuranceProfile.model_validate(
        json.loads(
            (FIXTURE_DIR / "assurance-profile.json").read_text(encoding="utf-8")
        )
    )


def seed_store(store: ArtifactStore) -> tuple[ClaimGraph, AssuranceProfile]:
    """Seed a store with the standard theorem-audit fixtures."""
    cg = load_claim_graph()
    profile = load_profile()
    store.save_claim_graph(cg, actor="test", reason="seed")
    store.save_assurance_profile(profile, actor="test", reason="seed")
    return cg, profile


def seed_dual_formalization_event(
    store: ArtifactStore,
    claim_id: str,
) -> None:
    """Insert a review event containing dual formalization workflow state."""
    store.append_review_event(
        target_claim_id=claim_id,
        artifact_kind="dual_formalization",
        artifact_id=f"workflow.dual_formalization.test",
        event_type="dual_formalization",
        actor="test.formalizer",
        actor_role="system",
        notes="Dual formalization completed.",
        metadata={
            "attempts": [
                {
                    "formalizer_label": "A",
                    "status": "succeeded",
                    "output": {
                        "proof_source": "theory Dispatch_Model_A begin\\nend",
                        "session_name": "Dispatch",
                        "module_name": "Dispatch_Model_A",
                        "primary_target": "driver_assignment_converges",
                        "back_translation": "The assignment always converges.",
                        "theorem_statement": "driver_assignment_converges",
                        "assumptions_used": [
                            {
                                "carrier": "locale",
                                "statement": "finite_driver_pool",
                            },
                        ],
                    },
                    "back_translation": "The assignment always converges.",
                },
                {
                    "formalizer_label": "B",
                    "status": "succeeded",
                    "output": {
                        "proof_source": "theory Dispatch_Model_B begin\\nend",
                        "session_name": "Dispatch",
                        "module_name": "Dispatch_Model_B",
                        "primary_target": "driver_assignment_converges",
                        "back_translation": "Assignment converges in all cases.",
                        "theorem_statement": "driver_assignment_converges",
                        "assumptions_used": [
                            {
                                "carrier": "locale",
                                "statement": "finite_driver_pool",
                            },
                            {
                                "carrier": "premise",
                                "statement": "finite_order_horizon",
                            },
                        ],
                    },
                    "back_translation": "Assignment converges in all cases.",
                },
            ],
            "divergence": {
                "classification": "minor_divergence",
                "summary": "Both attempts succeeded with minor divergence in assumptions.",
                "primary_target_match": True,
                "back_translation_match": False,
                "code_sha_match": False,
                "assumptions_only_in_a": [],
                "assumptions_only_in_b": ["premise:finite_order_horizon"],
            },
        },
    )


def seed_build_and_verifier_events(
    store: ArtifactStore,
    claim_id: str,
) -> None:
    """Insert a review event containing build and verifier results."""
    store.append_review_event(
        target_claim_id=claim_id,
        artifact_kind="audit_workflow",
        artifact_id=f"workflow.audit.test",
        event_type="audit_workflow",
        actor="test.auditor",
        actor_role="system",
        notes="Audit workflow completed.",
        metadata={
            "build_results": {
                "A": {
                    "success": True,
                    "sorry_count": 0,
                    "oops_count": 0,
                    "theorems": ["driver_assignment_converges"],
                },
            },
            "verifier_results": {
                "A": {
                    "proof_status": "verified",
                    "sorry_free": True,
                },
            },
        },
    )


def seed_promotion_transition(
    store: ArtifactStore,
    profile: AssuranceProfile,
) -> None:
    """Perform a promotion transition for the claim."""
    machine = PromotionStateMachine(store)
    machine.transition(
        profile,
        target_gate=Gate.queued,
        actor="test.reviewer",
        actor_role=ReviewActorRole.reviewer,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_phase2_claim_with_full_data() -> None:
    """Phase2 per-claim assembly includes all sections when data is present."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        cg, profile = seed_store(store)
        claim_id = canonical_artifact_id(profile.claim_id)

        seed_dual_formalization_event(store, claim_id)
        seed_build_and_verifier_events(store, claim_id)
        seed_promotion_transition(store, profile)

        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase2_claim(
            str(profile.project_id), claim_id
        )

        # -- dual_formalization --
        df = result["dual_formalization"]
        assert df["attempt_a"] is not None, "attempt_a should be populated"
        assert df["attempt_b"] is not None, "attempt_b should be populated"
        assert "output" in df["attempt_a"]
        assert "sha256" in df["attempt_a"]
        assert "assumptions" in df["attempt_a"]
        assert "back_translation" in df["attempt_a"]
        assert isinstance(df["attempt_a"]["assumptions"], list)

        # divergence
        div = df["divergence"]
        assert div["classification"] == "minor_divergence"
        assert div["primary_target_match"] is True
        assert div["back_translation_match"] is False
        assert isinstance(div["assumptions_only_in_b"], list)

        # -- build_results --
        assert "A" in result["build_results"]
        assert result["build_results"]["A"]["success"] is True

        # -- verifier_results --
        assert "A" in result["verifier_results"]
        assert result["verifier_results"]["A"]["proof_status"] == "verified"

        # -- audit --
        audit = result["audit"]
        assert "trust_frontier" in audit
        assert "model_health" in audit
        assert "intent_alignment" in audit
        assert "blocking_issues" in audit
        assert isinstance(audit["blocking_issues"], list)

        # -- profile --
        assert result["profile"] is not None
        assert result["profile"]["gate"] == "certified"

        # -- promotion_transitions --
        assert isinstance(result["promotion_transitions"], list)
        assert len(result["promotion_transitions"]) >= 1
        transition = result["promotion_transitions"][0]
        assert transition["from_gate"] == "draft"
        assert transition["to_gate"] == "queued"


def test_phase2_claim_with_no_data() -> None:
    """Phase2 per-claim assembly fills null/empty when claim has no artifacts."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase2_claim(
            "project.test", "claim.nonexistent"
        )

        # All fields must be present -- nothing omitted
        assert "dual_formalization" in result
        assert "build_results" in result
        assert "verifier_results" in result
        assert "audit" in result
        assert "profile" in result
        assert "promotion_transitions" in result

        # Dual formalization has empty structure
        df = result["dual_formalization"]
        assert df["attempt_a"] is None
        assert df["attempt_b"] is None
        assert df["divergence"]["classification"] == ""

        # Empty dicts / lists for other sections
        assert result["build_results"] == {}
        assert result["verifier_results"] == {}
        assert result["profile"] is None
        assert result["promotion_transitions"] == []

        # Audit has null sub-sections
        audit = result["audit"]
        assert audit["trust_frontier"] is None
        assert audit["model_health"] is None
        assert audit["intent_alignment"] is None
        assert audit["blocking_issues"] == []


def test_phase2_full_structure() -> None:
    """Full phase2 assembly includes per_claim and phase2_flags."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        cg, profile = seed_store(store)
        claim_id = canonical_artifact_id(profile.claim_id)
        seed_dual_formalization_event(store, claim_id)

        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase2(
            str(profile.project_id),
            [claim_id],
        )

        assert "per_claim" in result
        assert "phase2_flags" in result
        assert claim_id in result["per_claim"]

        flags = result["phase2_flags"]
        assert flags["phase2_executed"] is True
        assert isinstance(flags["certification_eligible"], bool)


def test_phase2_flags_not_executed() -> None:
    """phase2_flags reflect no execution when no formalization has occurred."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase2(
            "project.empty",
            ["claim.empty"],
        )

        assert result["phase2_flags"]["phase2_executed"] is False
        assert result["phase2_flags"]["certification_eligible"] is False


def test_phase2_certification_not_eligible_with_blocking_issues() -> None:
    """Claims with blocking obligations are not certification-eligible."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        # Create a profile with blocking obligations
        profile_data = load_profile().model_dump(mode="json", exclude_none=True)
        profile_data["gate"] = "blocked"
        profile_data["allowed_downstream"] = ["research"]
        profile_data["obligations"]["blocking_obligations"] = [
            "Resolve countermodel."
        ]
        profile_data["required_actions"] = ["Resolve countermodel."]
        blocked_profile = AssuranceProfile.model_validate(profile_data)

        store.save_assurance_profile(blocked_profile, actor="test", reason="seed")
        claim_id = canonical_artifact_id(blocked_profile.claim_id)
        seed_dual_formalization_event(store, claim_id)

        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase2(
            str(blocked_profile.project_id),
            [claim_id],
        )

        assert result["phase2_flags"]["phase2_executed"] is True
        assert result["phase2_flags"]["certification_eligible"] is False


def test_phase3_claim_with_evidence() -> None:
    """Phase3 per-claim assembly returns research_output and updated_profile."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        _, profile = seed_store(store)
        claim_id = canonical_artifact_id(profile.claim_id)

        # Seed evaluation evidence for the claim
        store.save_json_artifact(
            "evaluation_evidence_bundles",
            f"evidence.{claim_id}",
            {
                "schema_version": "1.0.0",
                "project_id": str(profile.project_id),
                "claim_id": claim_id,
                "items": [
                    {
                        "evidence_id": "ev.sim.1",
                        "status": "resolved",
                        "title": "Simulation run 1",
                        "summary": "All 1000 seeds converged.",
                    }
                ],
            },
            actor="test",
            reason="seed_evidence",
        )

        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase3_claim(
            str(profile.project_id), claim_id
        )

        assert "research_output" in result
        assert "updated_profile" in result
        assert result["research_output"] is not None
        assert result["updated_profile"] is not None
        assert result["updated_profile"]["gate"] == "certified"


def test_phase3_claim_with_no_evidence() -> None:
    """Phase3 per-claim assembly returns None for missing evidence."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase3_claim(
            "project.test", "claim.nonexistent"
        )

        assert result["research_output"] is None
        assert result["updated_profile"] is None


def test_phase3_full_structure() -> None:
    """Full phase3 assembly returns evidence dict keyed by claim_id."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        _, profile = seed_store(store)
        claim_id = canonical_artifact_id(profile.claim_id)

        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase3(
            str(profile.project_id),
            [claim_id],
        )

        assert "evidence" in result
        assert claim_id in result["evidence"]
        claim_data = result["evidence"][claim_id]
        assert "research_output" in claim_data
        assert "updated_profile" in claim_data


def test_trace_export_builder_full() -> None:
    """TraceExportBuilder.from_store produces a complete trace.json structure."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        cg, profile = seed_store(store)
        claim_id = canonical_artifact_id(profile.claim_id)
        seed_dual_formalization_event(store, claim_id)

        trace = TraceExportBuilder.from_store(
            run_id=str(profile.project_id),
            store=store,
            project_id=str(profile.project_id),
            claim_graph=cg,
        )

        assert trace["trace_version"] == "1.0.0"
        assert trace["project_id"] == str(profile.project_id)
        assert "generated_at" in trace
        assert "phase2" in trace
        assert "phase3" in trace

        # Phase2 structure
        phase2 = trace["phase2"]
        assert "per_claim" in phase2
        assert "phase2_flags" in phase2
        assert claim_id in phase2["per_claim"]

        # Phase3 structure
        phase3 = trace["phase3"]
        assert "evidence" in phase3
        assert claim_id in phase3["evidence"]


def test_trace_export_build_phase2_delegates() -> None:
    """PhaseAssembler.assemble_phase2 produces per_claim and phase2_flags."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase2("project.test", ["claim.test"])

        assert "per_claim" in result
        assert "phase2_flags" in result
        # PH2-002: unavailable_reason when not executed
        assert result["phase2_flags"]["unavailable_reason"] == "runtime_not_captured"


def test_trace_export_build_phase3_delegates() -> None:
    """PhaseAssembler.assemble_phase3 produces evidence and phase3_flags."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase3("project.test", ["claim.test"])

        assert "evidence" in result
        # PH2-002: phase3_flags present with unavailable_reason
        assert "phase3_flags" in result
        assert result["phase3_flags"]["phase3_executed"] is False
        assert result["phase3_flags"]["unavailable_reason"] == "runtime_not_captured"


def test_phase2_multiple_claims_partial_data() -> None:
    """Phase2 assembly handles mixed claims -- some with data, some without."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        _, profile = seed_store(store)
        claim_id = canonical_artifact_id(profile.claim_id)
        seed_dual_formalization_event(store, claim_id)

        missing_claim_id = "claim.nonexistent"

        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase2(
            str(profile.project_id),
            [claim_id, missing_claim_id],
        )

        per_claim = result["per_claim"]
        assert claim_id in per_claim
        assert missing_claim_id in per_claim

        # Populated claim has data
        assert per_claim[claim_id]["dual_formalization"]["attempt_a"] is not None

        # Missing claim has empty structure
        assert per_claim[missing_claim_id]["dual_formalization"]["attempt_a"] is None
        assert per_claim[missing_claim_id]["profile"] is None

        # Flags reflect partial execution
        assert result["phase2_flags"]["phase2_executed"] is True
        assert result["phase2_flags"]["certification_eligible"] is False


def test_dual_formalization_attempt_b_assumptions_only() -> None:
    """Divergence correctly reports assumptions only in B."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ArtifactStore(tmp)
        _, profile = seed_store(store)
        claim_id = canonical_artifact_id(profile.claim_id)
        seed_dual_formalization_event(store, claim_id)

        assembler = PhaseAssembler(store)
        result = assembler.assemble_phase2_claim(
            str(profile.project_id), claim_id
        )

        df = result["dual_formalization"]
        # attempt B has an extra assumption the seed data gave it
        b_assumptions = df["attempt_b"]["assumptions"]
        a_assumptions = df["attempt_a"]["assumptions"]
        assert len(b_assumptions) > len(a_assumptions)

        # Divergence captures this
        div = df["divergence"]
        assert len(div["assumptions_only_in_b"]) > 0


def main() -> None:
    test_phase2_claim_with_full_data()
    test_phase2_claim_with_no_data()
    test_phase2_full_structure()
    test_phase2_flags_not_executed()
    test_phase2_certification_not_eligible_with_blocking_issues()
    test_phase3_claim_with_evidence()
    test_phase3_claim_with_no_evidence()
    test_phase3_full_structure()
    test_trace_export_builder_full()
    test_trace_export_build_phase2_delegates()
    test_trace_export_build_phase3_delegates()
    test_phase2_multiple_claims_partial_data()
    test_dual_formalization_attempt_b_assumptions_only()
    print("All phase assembler tests passed.")


if __name__ == "__main__":
    main()
