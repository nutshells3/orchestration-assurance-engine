"""Per-document acceptance tests for relation fidelity.

These tests verify that the structuring pipeline produces semantically
correct relations, not just schema-valid ones.  They catch systematic
label bias from the structural fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Bootstrap imports
_REPO = Path(__file__).resolve().parents[2]
for _src in (
    _REPO / "services" / "engine" / "src",
    _REPO / "packages" / "contracts-py" / "src",
):
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

from formal_claim_engine.orchestrator import (
    PipelineOrchestrator,
    _critique_relation_polarity,
    _derive_missing_relations,
)
from formal_claim_engine.config import PipelineConfig


# ===================================================================
# Fixtures: simulated LLM outputs for each document
# ===================================================================
# These represent what a correctly-functioning LLM *should* produce.
# We test that the pipeline correctly admits them and that the critic
# catches specific known misclassifications.

def _make_orch() -> PipelineOrchestrator:
    return PipelineOrchestrator(PipelineConfig(data_dir="/tmp/test_rel_fidelity"))


# -------------------------------------------------------------------
# Test 1: _derive_missing_relations uses chain, not star
# -------------------------------------------------------------------

class TestFallbackTopology:
    def test_chain_not_star(self):
        claims = [
            {"claim_id": "c1"},
            {"claim_id": "c2"},
            {"claim_id": "c3"},
            {"claim_id": "c4"},
        ]
        rels = _derive_missing_relations(claims)
        # Chain: c1->c2, c2->c3, c3->c4
        assert len(rels) == 3
        pairs = [(r["from_claim_id"], r["to_claim_id"]) for r in rels]
        assert ("c1", "c2") in pairs
        assert ("c2", "c3") in pairs
        assert ("c3", "c4") in pairs
        # No star: c2->c1, c3->c1, c4->c1 must NOT appear
        tgts = [r["to_claim_id"] for r in rels]
        assert tgts.count("c1") == 0, "Star topology detected"

    def test_fallback_uses_depends_on_unknown(self):
        claims = [{"claim_id": "c1"}, {"claim_id": "c2"}]
        rels = _derive_missing_relations(claims)
        for r in rels:
            assert r["relation_type"] == "depends_on"
            assert r["strength"] == "unknown"
            assert r["label_source"] == "fallback"
            assert r["weak_label"] is True

    def test_fallback_single_claim_no_relations(self):
        claims = [{"claim_id": "c1"}]
        assert _derive_missing_relations(claims) == []


# -------------------------------------------------------------------
# Test 2: Provenance counters in candidate_ledger
# -------------------------------------------------------------------

class TestProvenanceCounters:
    def test_all_fallback_when_no_raw_relations(self):
        orch = _make_orch()
        candidate = {
            "claims": [
                {"claim_id": "c1", "statement": "Main", "role": "theorem", "title": "A"},
                {"claim_id": "c2", "statement": "Sub", "role": "lemma", "title": "B"},
            ],
            "relations": [],
        }
        _, ledger = orch._validate_claim_graph_candidate(candidate)
        prov = ledger["_relation_provenance"][0]
        assert prov["raw_relation_count"] == 0
        assert prov["fallback_relation_count"] > 0
        assert prov["all_relations_are_fallback"] is True

    def test_not_fallback_when_llm_relations_present(self):
        orch = _make_orch()
        candidate = {
            "claims": [
                {"claim_id": "c1", "statement": "Main", "role": "theorem", "title": "A"},
                {"claim_id": "c2", "statement": "Sub", "role": "lemma", "title": "B"},
            ],
            "relations": [
                {"from_claim_id": "c2", "to_claim_id": "c1",
                 "relation_type": "supports", "strength": "statistical"},
            ],
        }
        _, ledger = orch._validate_claim_graph_candidate(candidate)
        prov = ledger["_relation_provenance"][0]
        assert prov["accepted_raw_relation_count"] == 1
        assert prov["fallback_relation_count"] == 0
        assert prov["all_relations_are_fallback"] is False

    def test_label_source_llm_on_accepted_relations(self):
        orch = _make_orch()
        candidate = {
            "claims": [
                {"claim_id": "c1", "statement": "A", "role": "theorem", "title": "A"},
                {"claim_id": "c2", "statement": "B", "role": "lemma", "title": "B"},
            ],
            "relations": [
                {"from_claim_id": "c2", "to_claim_id": "c1",
                 "relation_type": "challenges", "strength": "abductive"},
            ],
        }
        _, ledger = orch._validate_claim_graph_candidate(candidate)
        for entry in ledger.get("relations_accepted", []):
            assert entry["label_source"] == "llm"
            assert entry["weak_label"] is False


# -------------------------------------------------------------------
# Test 3: Polarity critic catches known misclassifications
# -------------------------------------------------------------------

class TestPolarityCritic:
    """Test that the critic catches the specific misclassifications
    identified in the user's analysis of the 5 e2e documents."""

    def test_remote_work_collaboration_not_supports(self):
        """collaboration_network_fragmentation should NOT be supports
        to the main productivity claim."""
        claims = [
            {"claim_id": "main", "nl_statement": "Remote work increases organizational productivity"},
            {"claim_id": "collab", "nl_statement": "However collaboration networks fragment and become siloed"},
        ]
        # If the pipeline incorrectly labels this as supports:
        bad_rels = [
            {"from_claim_id": "collab", "to_claim_id": "main",
             "relation_type": "supports", "strength": "statistical"},
        ]
        warnings = _critique_relation_polarity(claims, bad_rels)
        assert len(warnings) > 0, "Critic should flag supports for a challenge-cue claim"
        assert any("polarity" in w.lower() for w in warnings)

    def test_ai_medical_deployment_gap_not_supports(self):
        """deployment_gap should NOT support the claim that AI transforms
        clinical diagnosis."""
        claims = [
            {"claim_id": "main", "nl_statement": "AI will transform clinical diagnosis"},
            {"claim_id": "gap", "nl_statement": "However accuracy dropped significantly in real-world deployment"},
        ]
        bad_rels = [
            {"from_claim_id": "gap", "to_claim_id": "main",
             "relation_type": "supports", "strength": "abductive"},
        ]
        warnings = _critique_relation_polarity(claims, bad_rels)
        assert len(warnings) > 0

    def test_ai_medical_training_bias_not_supports(self):
        """training_bias raises equity concerns and should not be supports."""
        claims = [
            {"claim_id": "main", "nl_statement": "AI will transform clinical diagnosis"},
            {"claim_id": "bias", "nl_statement": "Training data bias could worsen healthcare disparities"},
        ]
        bad_rels = [
            {"from_claim_id": "bias", "to_claim_id": "main",
             "relation_type": "supports", "strength": "abductive"},
        ]
        warnings = _critique_relation_polarity(claims, bad_rels)
        assert len(warnings) > 0

    def test_carbon_offset_additionality_not_supports(self):
        """Over 90% of credits did not represent real reductions - challenges."""
        claims = [
            {"claim_id": "main", "nl_statement": "Carbon offsets represent genuine emission reductions"},
            {"claim_id": "add", "nl_statement": "Investigation found over 90% of credits did not represent real emission reductions"},
        ]
        bad_rels = [
            {"from_claim_id": "add", "to_claim_id": "main",
             "relation_type": "supports", "strength": "abductive"},
        ]
        warnings = _critique_relation_polarity(claims, bad_rels)
        assert len(warnings) > 0

    def test_correct_labeling_no_warnings(self):
        """Correctly labeled relations should not trigger warnings."""
        claims = [
            {"claim_id": "main", "nl_statement": "Remote work increases productivity"},
            {"claim_id": "collab", "nl_statement": "However collaboration networks fragment"},
            {"claim_id": "stanford", "nl_statement": "Stanford study found 3-5% productivity increase"},
        ]
        good_rels = [
            {"from_claim_id": "collab", "to_claim_id": "main",
             "relation_type": "challenges", "strength": "statistical"},
            {"from_claim_id": "stanford", "to_claim_id": "main",
             "relation_type": "supports", "strength": "statistical"},
        ]
        warnings = _critique_relation_polarity(claims, good_rels)
        assert len(warnings) == 0, f"Unexpected warnings: {warnings}"

    def test_empirical_cue_strength_mismatch(self):
        """Claims citing studies should not default to abductive."""
        claims = [
            {"claim_id": "c1", "nl_statement": "A 2024 meta-analysis pooled 82 studies"},
        ]
        bad_rels = [
            {"from_claim_id": "c1", "to_claim_id": "main",
             "relation_type": "supports", "strength": "abductive"},
        ]
        warnings = _critique_relation_polarity(claims, bad_rels)
        assert any("strength" in w.lower() for w in warnings)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
