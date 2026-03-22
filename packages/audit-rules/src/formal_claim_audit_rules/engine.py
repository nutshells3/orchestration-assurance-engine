"""Deterministic assurance-profile computation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any

from .compat import AssuranceProfile, ClaimGraphQueries, canonical_id, summarize_theorem_trust


@dataclass(frozen=True)
class AssuranceComputationInput:
    project_id: str
    claim: dict[str, Any]
    verifier_output: dict[str, Any] | None = None
    audit_output: dict[str, Any] | None = None
    research_output: dict[str, Any] | None = None
    coverage_data: dict[str, Any] | None = None
    claim_graph: Any | None = None
    runner_trust: dict[str, Any] | None = None
    probe_results: dict[str, Any] | list[dict[str, Any]] | None = None
    robustness_harness: dict[str, Any] | None = None
    existing_profile: dict[str, Any] | None = None
    profile_id: str | None = None
    claim_graph_ref: str | None = None
    assurance_graph_ref: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_int(value: float, *, minimum: int = 0, maximum: int = 100) -> int:
    return max(minimum, min(maximum, int(round(value))))


def _bounded_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


PROOF_CLAIM_SCORE_METHOD = "qbaf_df_quad"
PROOF_CLAIM_SCORE_VERSION = "1.0.0"


def _best_verifier_result(verifier_output: dict[str, Any] | None) -> dict[str, Any]:
    entries = list((verifier_output or {}).values())
    if not entries:
        return {}

    def rank(entry: dict[str, Any]) -> tuple[int, int, int]:
        proof_status = entry.get("proof_status")
        if proof_status == "proof_complete":
            status_rank = 3
        elif proof_status == "built":
            status_rank = 2
        elif entry.get("build_success"):
            status_rank = 1
        else:
            status_rank = 0
        theorem_rank = len(entry.get("targets_found", []))
        obligation_rank = -(
            entry.get("sorry_count", 0) + entry.get("oops_count", 0)
        )
        return status_rank, theorem_rank, obligation_rank

    return max(entries, key=rank)


def _formal_status(best_verifier: dict[str, Any]) -> str:
    if not best_verifier:
        return "unformalized"
    if best_verifier.get("proof_status") == "proof_complete":
        return "proof_complete"
    if best_verifier.get("build_success"):
        if best_verifier.get("targets_found"):
            return "build_passed_no_proof"
        return "skeleton_only"
    return "build_failed"


def _normalized_formal_system(best_verifier: dict[str, Any], artifact: dict[str, Any]) -> str:
    explicit = str(artifact.get("system") or "").strip()
    if explicit in {"isabelle_hol", "lean", "coq", "agda", "other"}:
        return explicit
    proof_language = str(best_verifier.get("proof_language") or "").strip().lower()
    if proof_language == "isabelle":
        return "isabelle_hol"
    if proof_language == "lean":
        return "lean"
    if proof_language in {"rocq", "coq"}:
        return "coq"
    return "other"


def _intent_status(intent_alignment: dict[str, Any]) -> str:
    review = intent_alignment.get("backtranslation_review", "unreviewed")
    violations = intent_alignment.get("semantics_guard_violations", [])
    agreement = float(intent_alignment.get("agreement_score", 0.0))
    if review == "fail":
        return "misaligned"
    if review == "needs_revision" or violations:
        return "partially_aligned"
    if review == "pass" and agreement >= 0.75:
        return "aligned"
    return "unreviewed"


def _support_status(research_output: dict[str, Any] | None) -> str:
    if not research_output:
        return "none"
    recommended = research_output.get("recommended_support_status")
    if recommended:
        return recommended

    items = research_output.get("evidence_items", [])
    polarities = {item.get("result_polarity") for item in items}
    kinds = {item.get("evidence_kind") for item in items}
    if "refutes" in polarities:
        return "refuted"
    if "challenges" in polarities and len(polarities) == 1:
        return "challenged"
    if "simulation" in kinds:
        return "simulation_supported"
    if "test_run" in kinds:
        return "test_supported"
    if "literature" in kinds:
        return "literature_supported"
    if "experiment" in kinds or "benchmark" in kinds:
        return "experimentally_supported"
    if {"supports", "mixed"} & polarities:
        return "mixed_supported"
    return "none"


def _default_audit_section(audit_output: dict[str, Any] | None, field: str, defaults: dict[str, Any]) -> dict[str, Any]:
    section = (audit_output or {}).get(field)
    if isinstance(section, dict):
        merged = dict(defaults)
        merged.update(section)
        return merged
    return dict(defaults)


def _normalize_probe_results(
    probe_results: dict[str, Any] | list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not probe_results:
        return []
    if isinstance(probe_results, list):
        return [entry for entry in probe_results if isinstance(entry, dict)]
    if isinstance(probe_results, dict):
        if "kind" in probe_results:
            return [probe_results]
        return [entry for entry in probe_results.values() if isinstance(entry, dict)]
    return []


def _trust_frontier_from_runner(data: AssuranceComputationInput) -> dict[str, Any] | None:
    if not data.runner_trust:
        return None

    summary = summarize_theorem_trust(data.runner_trust)
    if isinstance(data.runner_trust, dict):
        surface = data.runner_trust.get("surface", data.runner_trust)
    else:
        surface = getattr(data.runner_trust, "surface", data.runner_trust)
    oracle_ids = list(
        surface.get("oracle_ids", summary.oracle_ids)
        if isinstance(surface, dict)
        else getattr(surface, "oracle_ids", summary.oracle_ids)
    )
    notes = list(summary.notes)
    if summary.reviewed_exception_count:
        notes.append(
            f"{summary.reviewed_exception_count} reviewed exception(s) are attached to the theorem-local closure."
        )
    return {
        "global_axiom_dependency_count": summary.global_axiom_dependency_count,
        "locale_assumption_count": summary.locale_assumption_count,
        "premise_assumption_count": summary.premise_assumption_count,
        "oracle_dependency_count": summary.oracle_dependency_count,
        "unreviewed_import_count": len(summary.hotspot_artifact_ids),
        "transitive_dependency_count": summary.transitive_dependency_count,
        "reviewed_global_axiom_ids": list(summary.reviewed_global_axiom_ids),
        "oracle_ids": list(oracle_ids),
        "hotspot_artifact_ids": list(summary.hotspot_artifact_ids),
        "notes": list(dict.fromkeys(notes)),
    }


def _merge_model_health_signals(
    model_health: dict[str, Any],
    probe_results: list[dict[str, Any]],
    robustness_harness: dict[str, Any] | None,
) -> dict[str, Any]:
    result = dict(model_health)
    notes = list(result.get("notes", []))

    for probe in probe_results:
        kind = probe.get("kind")
        outcome = probe.get("outcome")
        summary = probe.get("summary", "")
        if kind in {"counterexample", "nitpick"}:
            if outcome in {"countermodel_found", "found"}:
                result["countermodel_probe"] = "countermodel_found"
            elif outcome in {"no_countermodel_found", "none"} and result.get("countermodel_probe") == "untested":
                result["countermodel_probe"] = "no_countermodel_found"
            elif outcome == "inconclusive" and result.get("countermodel_probe") == "untested":
                result["countermodel_probe"] = "inconclusive"
        elif kind in {"proofSearch", "sledgehammer"} and summary:
            notes.append(summary)

        if summary:
            notes.append(summary)

    if robustness_harness:
        premise_status = robustness_harness.get("premise_sensitivity", "untested")
        conclusion_status = robustness_harness.get("conclusion_perturbation", "untested")
        result["premise_sensitivity"] = premise_status
        result["conclusion_perturbation"] = conclusion_status
        if premise_status == "fragile":
            result["vacuity_check"] = "fail"
        elif premise_status == "stable" and result.get("vacuity_check") == "untested":
            result["vacuity_check"] = "pass"
        notes.extend(robustness_harness.get("notes", []))

    result["notes"] = list(dict.fromkeys(notes))
    return result


def _merge_trust_frontier(
    audit_output: dict[str, Any] | None,
    runner_trust: dict[str, Any] | None,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    trust_frontier = _default_audit_section(audit_output, "trust_frontier", defaults)
    if not runner_trust:
        return trust_frontier

    merged = dict(trust_frontier)
    merged.update({key: value for key, value in runner_trust.items() if key != "notes"})
    merged["notes"] = list(
        dict.fromkeys(
            list(trust_frontier.get("notes", [])) + list(runner_trust.get("notes", []))
        )
    )
    return merged


def _derive_coverage(data: AssuranceComputationInput, formal_status: str) -> dict[str, Any]:
    if data.coverage_data:
        return dict(data.coverage_data)

    required_claim_ids: list[str] = []
    if data.claim_graph is not None:
        claim_id = data.claim["claim_id"]
        queries = ClaimGraphQueries(data.claim_graph)
        required_claim_ids = [
            dependency
            for dependency in queries.dependency_closure([claim_id])
            if dependency != claim_id
        ]

    if formal_status == "proof_complete":
        uncovered = []
        dependency_coverage_score = 1.0 if required_claim_ids else 0.0
    elif formal_status in {"build_passed_no_proof", "skeleton_only"}:
        uncovered = list(required_claim_ids)
        dependency_coverage_score = 0.5 if required_claim_ids else 0.0
    else:
        uncovered = list(required_claim_ids)
        dependency_coverage_score = 0.0

    return {
        "formalized_subclaim_count": len(required_claim_ids) - len(uncovered),
        "residual_subclaim_count": len(uncovered),
        "dependency_coverage_score": dependency_coverage_score,
        "evidence_link_coverage_score": 0.0,
        "required_claim_ids": required_claim_ids,
        "uncovered_claim_ids": uncovered,
    }


def _derive_support_profile(research_output: dict[str, Any] | None) -> dict[str, Any]:
    items = list((research_output or {}).get("evidence_items", []))
    counts = {
        "experiment": 0,
        "literature": 0,
        "simulation": 0,
        "test_run": 0,
        "countermodel": 0,
        "manual_review": 0,
    }
    for item in items:
        kind = item.get("evidence_kind")
        if kind in counts:
            counts[kind] += 1

    def score(item: dict[str, Any]) -> tuple[float, str]:
        return float(item.get("confidence", 0.0)), item.get("node_id", "")

    positive = sorted(
        [
            item
            for item in items
            if item.get("result_polarity") in {"supports", "mixed", "neutral"}
        ],
        key=score,
        reverse=True,
    )
    negative = sorted(
        [
            item
            for item in items
            if item.get("result_polarity") in {"challenges", "refutes"}
        ],
        key=score,
        reverse=True,
    )

    return {
        "evidence_counts": counts,
        "strongest_positive_evidence_ids": [
            item["node_id"] for item in positive[:3] if item.get("node_id")
        ],
        "strongest_negative_evidence_ids": [
            item["node_id"] for item in negative[:3] if item.get("node_id")
        ],
        "bridge_notes": [
            (research_output or {}).get("overall_assessment", "").strip()
        ]
        if (research_output or {}).get("overall_assessment")
        else [],
    }


def _derive_obligations(best_verifier: dict[str, Any], audit_output: dict[str, Any] | None) -> dict[str, Any]:
    blocking_issues = list((audit_output or {}).get("blocking_issues", []))
    return {
        "sorry_count": int(best_verifier.get("open_obligation_count", best_verifier.get("sorry_count", 0)) or 0),
        "oops_count": int(best_verifier.get("oops_count", 0) or 0),
        "open_goal_count": int(best_verifier.get("open_goal_count", 0) or 0),
        "unresolved_bridge_count": len(blocking_issues),
        "missing_backtranslation_review_count": 0
        if (audit_output or {}).get("intent_alignment", {}).get("backtranslation_review")
        in {"pass", "fail", "needs_revision"}
        else 1,
        "blocking_obligations": blocking_issues,
    }


def _derive_robustness(verifier_output: dict[str, Any] | None, audit_output: dict[str, Any] | None) -> dict[str, Any]:
    entries = list((verifier_output or {}).values())
    warnings = [
        warning
        for entry in entries
        for warning in entry.get("warnings", [])
        if isinstance(warning, str)
    ] + list((audit_output or {}).get("warnings", []))
    successful = [entry for entry in entries if entry.get("build_success")]
    fingerprints = {
        entry.get("session_fingerprint") for entry in successful if entry.get("session_fingerprint")
    }
    if successful and len(fingerprints) == len(successful):
        replay_status = "pass"
        reproducibility = 0.95
    elif successful:
        replay_status = "flaky"
        reproducibility = 0.6
    else:
        replay_status = "untested"
        reproducibility = 0.0
    if entries and not successful:
        replay_status = "fail"
        reproducibility = 0.1

    return {
        "linter_finding_count": len(warnings),
        "replay_status": replay_status,
        "rebuild_count": len(entries),
        "reproducibility_score": reproducibility,
        "cross_tool_review_status": "pass" if len(entries) > 1 else "not_applicable",
        "notes": warnings,
    }


def _derive_base_vector_scores(
    trust_frontier: dict[str, Any],
    obligations: dict[str, Any],
    model_health: dict[str, Any],
    intent_alignment: dict[str, Any],
    coverage: dict[str, Any],
    robustness: dict[str, Any],
    support_profile: dict[str, Any],
) -> dict[str, Any]:
    trust_score = 100
    trust_score -= 12 * int(trust_frontier.get("global_axiom_dependency_count", 0))
    trust_score -= 18 * int(trust_frontier.get("oracle_dependency_count", 0))
    trust_score -= 8 * int(trust_frontier.get("unreviewed_import_count", 0))
    trust_score -= 10 * int(obligations.get("sorry_count", 0))
    trust_score -= 10 * int(obligations.get("oops_count", 0))
    if model_health.get("countermodel_probe") == "countermodel_found":
        trust_score -= 35
    if model_health.get("vacuity_check") == "fail":
        trust_score -= 25

    intent_score = 100 * float(intent_alignment.get("agreement_score", 0.0))
    intent_score = max(intent_score, 100 * float(intent_alignment.get("paraphrase_robustness_score", 0.0)))
    review = intent_alignment.get("backtranslation_review")
    if review == "pass":
        intent_score = max(intent_score, 85)
    elif review == "needs_revision":
        intent_score = min(intent_score, 60)
    elif review == "fail":
        intent_score = min(intent_score, 20)
    intent_score -= 15 * len(intent_alignment.get("semantics_guard_violations", []))

    evidence_counts = support_profile["evidence_counts"]
    evidence_score = (
        30 * evidence_counts["literature"]
        + 35 * evidence_counts["experiment"]
        + 35 * evidence_counts["simulation"]
        + 30 * evidence_counts["test_run"]
        + 20 * evidence_counts["manual_review"]
        - 25 * evidence_counts["countermodel"]
    )

    coverage_score = 100 * float(coverage.get("dependency_coverage_score", 0.0))
    coverage_score = max(
        coverage_score,
        100 * float(coverage.get("evidence_link_coverage_score", 0.0)),
    )
    if coverage.get("required_claim_ids") and not coverage.get("uncovered_claim_ids"):
        coverage_score = max(coverage_score, 90)

    robustness_score = 100 * float(robustness.get("reproducibility_score", 0.0))
    if robustness.get("replay_status") == "pass":
        robustness_score = max(robustness_score, 85)
    elif robustness.get("replay_status") == "fail":
        robustness_score = min(robustness_score, 25)
    robustness_score -= 5 * int(robustness.get("linter_finding_count", 0))

    vector_scores = {
        "trust_base_integrity": _bounded_int(trust_score),
        "intent_alignment": _bounded_int(intent_score),
        "evidence_support": _bounded_int(evidence_score),
        "coverage": _bounded_int(coverage_score),
        "robustness": _bounded_int(robustness_score),
    }
    vector_scores["scheduler_score"] = _bounded_int(
        sum(vector_scores.values()) / len(vector_scores)
    )
    return vector_scores


def df_quad_strength(base: float, attackers: list[float], supporters: list[float]) -> float:
    base = _bounded_unit(base)
    va = 1 - math.prod(1 - _bounded_unit(value) for value in attackers) if attackers else 0.0
    vs = 1 - math.prod(1 - _bounded_unit(value) for value in supporters) if supporters else 0.0
    if math.isclose(va, vs, rel_tol=1e-9, abs_tol=1e-9):
        return base
    if va > vs:
        return _bounded_unit(base - base * abs(vs - va))
    return _bounded_unit(base + (1 - base) * abs(vs - va))


def _qbaf_argument(
    argument_id: str,
    label: str,
    polarity: str,
    node_confidence: float,
    relation_strength: float,
    summary: str,
) -> dict[str, Any]:
    effective_strength = _bounded_unit(node_confidence) * _bounded_unit(relation_strength)
    return {
        "argumentId": canonical_id(argument_id),
        "label": label,
        "polarity": polarity,
        "nodeConfidence": round(_bounded_unit(node_confidence), 4),
        "relationStrength": round(_bounded_unit(relation_strength), 4),
        "effectiveStrength": round(effective_strength, 4),
        "summary": summary,
    }


def _dimension_qbaf_arguments(
    trust_frontier: dict[str, Any],
    obligations: dict[str, Any],
    model_health: dict[str, Any],
    intent_alignment: dict[str, Any],
    coverage: dict[str, Any],
    robustness: dict[str, Any],
    support_profile: dict[str, Any],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    evidence_counts = support_profile.get("evidence_counts", {})
    required_claim_ids = list(coverage.get("required_claim_ids") or [])
    uncovered_claim_ids = list(coverage.get("uncovered_claim_ids") or [])
    missing_global_axioms = max(
        0,
        int(trust_frontier.get("global_axiom_dependency_count", 0))
        - len(list(trust_frontier.get("reviewed_global_axiom_ids") or [])),
    )
    linter_finding_count = int(robustness.get("linter_finding_count", 0))

    dimensions: dict[str, dict[str, list[dict[str, Any]]]] = {
        "trust_base_integrity": {"supporters": [], "attackers": []},
        "intent_alignment": {"supporters": [], "attackers": []},
        "evidence_support": {"supporters": [], "attackers": []},
        "coverage": {"supporters": [], "attackers": []},
        "robustness": {"supporters": [], "attackers": []},
    }

    trust_supporters = dimensions["trust_base_integrity"]["supporters"]
    trust_attackers = dimensions["trust_base_integrity"]["attackers"]
    if int(trust_frontier.get("oracle_dependency_count", 0)) == 0:
        trust_supporters.append(
            _qbaf_argument(
                "arg.trust.no_oracles",
                "No oracle dependency",
                "support",
                1.0,
                0.55,
                "The theorem-local trust frontier reports no oracle dependencies.",
            )
        )
    if missing_global_axioms == 0 and int(trust_frontier.get("global_axiom_dependency_count", 0)) > 0:
        trust_supporters.append(
            _qbaf_argument(
                "arg.trust.reviewed_global_axioms",
                "Reviewed global axioms",
                "support",
                1.0,
                0.35,
                "Every global axiom dependency is explicitly reviewed.",
            )
        )
    if missing_global_axioms > 0:
        trust_attackers.append(
            _qbaf_argument(
                "arg.trust.unreviewed_global_axioms",
                "Unreviewed global axioms",
                "attack",
                min(1.0, 0.45 + 0.2 * missing_global_axioms),
                0.7,
                "Global axiom dependencies remain unreviewed in the trust frontier.",
            )
        )
    oracle_dependency_count = int(trust_frontier.get("oracle_dependency_count", 0))
    if oracle_dependency_count > 0:
        trust_attackers.append(
            _qbaf_argument(
                "arg.trust.oracle_dependencies",
                "Oracle dependency",
                "attack",
                min(1.0, 0.4 + 0.2 * oracle_dependency_count),
                0.8,
                "Oracle-backed proof steps weaken trust-base integrity.",
            )
        )
    unreviewed_import_count = int(trust_frontier.get("unreviewed_import_count", 0))
    if unreviewed_import_count > 0:
        trust_attackers.append(
            _qbaf_argument(
                "arg.trust.hotspot_imports",
                "Hotspot imports",
                "attack",
                min(1.0, 0.25 + 0.1 * unreviewed_import_count),
                0.45,
                "Imported hotspot artifacts require operator review.",
            )
        )
    if int(obligations.get("sorry_count", 0)) > 0:
        trust_attackers.append(
            _qbaf_argument(
                "arg.trust.sorry",
                "Open sorry obligations",
                "attack",
                min(1.0, 0.35 + 0.15 * int(obligations.get("sorry_count", 0))),
                0.85,
                "Remaining sorry obligations undercut the trusted base.",
            )
        )
    if int(obligations.get("oops_count", 0)) > 0:
        trust_attackers.append(
            _qbaf_argument(
                "arg.trust.oops",
                "Open oops obligations",
                "attack",
                min(1.0, 0.35 + 0.15 * int(obligations.get("oops_count", 0))),
                0.85,
                "Remaining oops obligations undercut the trusted base.",
            )
        )
    if model_health.get("countermodel_probe") == "countermodel_found":
        trust_attackers.append(
            _qbaf_argument(
                "arg.trust.countermodel",
                "Countermodel found",
                "attack",
                1.0,
                0.9,
                "A countermodel directly attacks trust-base integrity.",
            )
        )
    if model_health.get("vacuity_check") == "fail":
        trust_attackers.append(
            _qbaf_argument(
                "arg.trust.vacuity",
                "Vacuity failure",
                "attack",
                0.95,
                0.75,
                "A vacuity failure indicates the proof may not track the intended dependency frontier.",
            )
        )

    intent_supporters = dimensions["intent_alignment"]["supporters"]
    intent_attackers = dimensions["intent_alignment"]["attackers"]
    intent_supporters.append(
        _qbaf_argument(
            "arg.intent.agreement",
            "Formalizer agreement",
            "support",
            float(intent_alignment.get("agreement_score", 0.0)),
            0.7,
            "Independent formalizations agree on the intended claim meaning.",
        )
    )
    intent_supporters.append(
        _qbaf_argument(
            "arg.intent.paraphrase",
            "Paraphrase robustness",
            "support",
            float(intent_alignment.get("paraphrase_robustness_score", 0.0)),
            0.55,
            "Paraphrase robustness supports intent preservation.",
        )
    )
    review_state = intent_alignment.get("backtranslation_review")
    if review_state == "pass":
        intent_supporters.append(
            _qbaf_argument(
                "arg.intent.backtranslation_pass",
                "Backtranslation review passed",
                "support",
                1.0,
                0.6,
                "Backtranslation review confirms the formalization tracks the original claim.",
            )
        )
    elif review_state == "needs_revision":
        intent_attackers.append(
            _qbaf_argument(
                "arg.intent.backtranslation_revision",
                "Backtranslation needs revision",
                "attack",
                0.7,
                0.55,
                "Backtranslation review still requires revision.",
            )
        )
    elif review_state == "fail":
        intent_attackers.append(
            _qbaf_argument(
                "arg.intent.backtranslation_fail",
                "Backtranslation review failed",
                "attack",
                1.0,
                0.85,
                "Backtranslation review reports an intent mismatch.",
            )
        )
    semantics_guard_violations = list(intent_alignment.get("semantics_guard_violations") or [])
    if semantics_guard_violations:
        intent_attackers.append(
            _qbaf_argument(
                "arg.intent.guard_violations",
                "Semantics-guard violations",
                "attack",
                min(1.0, 0.4 + 0.15 * len(semantics_guard_violations)),
                0.7,
                "Semantics-guard violations indicate drift from the original claim intent.",
            )
        )

    evidence_supporters = dimensions["evidence_support"]["supporters"]
    evidence_attackers = dimensions["evidence_support"]["attackers"]
    evidence_weights = {
        "literature": ("Literature support", 0.55),
        "experiment": ("Experimental support", 0.75),
        "simulation": ("Simulation support", 0.65),
        "test_run": ("Test-run support", 0.65),
        "manual_review": ("Manual review support", 0.4),
    }
    for key, (label, relation_strength) in evidence_weights.items():
        count = int(evidence_counts.get(key, 0))
        if count <= 0:
            continue
        evidence_supporters.append(
            _qbaf_argument(
                f"arg.evidence.{key}",
                label,
                "support",
                min(1.0, 0.35 + 0.15 * count),
                relation_strength,
                f"{count} {key.replace('_', ' ')} item(s) support the claim.",
            )
        )
    countermodel_count = int(evidence_counts.get("countermodel", 0))
    if countermodel_count > 0:
        evidence_attackers.append(
            _qbaf_argument(
                "arg.evidence.countermodel",
                "Countermodel evidence",
                "attack",
                min(1.0, 0.45 + 0.2 * countermodel_count),
                0.85,
                "Countermodel evidence attacks the evidence-support dimension.",
            )
        )

    coverage_supporters = dimensions["coverage"]["supporters"]
    coverage_attackers = dimensions["coverage"]["attackers"]
    coverage_supporters.append(
        _qbaf_argument(
            "arg.coverage.dependencies",
            "Dependency coverage",
            "support",
            float(coverage.get("dependency_coverage_score", 0.0)),
            0.75,
            "Covered dependency links support the coverage dimension.",
        )
    )
    coverage_supporters.append(
        _qbaf_argument(
            "arg.coverage.evidence_links",
            "Evidence-link coverage",
            "support",
            float(coverage.get("evidence_link_coverage_score", 0.0)),
            0.55,
            "Evidence links strengthen the coverage dimension.",
        )
    )
    if required_claim_ids and not uncovered_claim_ids:
        coverage_supporters.append(
            _qbaf_argument(
                "arg.coverage.no_uncovered_claims",
                "No uncovered supporting claims",
                "support",
                1.0,
                0.45,
                "All required supporting claims are covered.",
            )
        )
    if uncovered_claim_ids:
        coverage_attackers.append(
            _qbaf_argument(
                "arg.coverage.uncovered_claims",
                "Uncovered supporting claims",
                "attack",
                min(1.0, len(uncovered_claim_ids) / max(len(required_claim_ids), 1)),
                0.8,
                "Missing supporting-claim coverage lowers the coverage dimension.",
            )
        )

    robustness_supporters = dimensions["robustness"]["supporters"]
    robustness_attackers = dimensions["robustness"]["attackers"]
    robustness_supporters.append(
        _qbaf_argument(
            "arg.robustness.reproducibility",
            "Reproducibility",
            "support",
            float(robustness.get("reproducibility_score", 0.0)),
            0.75,
            "Stable replay and rebuild behavior supports robustness.",
        )
    )
    replay_status = robustness.get("replay_status")
    if replay_status == "pass":
        robustness_supporters.append(
            _qbaf_argument(
                "arg.robustness.replay_pass",
                "Replay passed",
                "support",
                1.0,
                0.55,
                "Replay passed under the current governed runner budget.",
            )
        )
    elif replay_status == "flaky":
        robustness_attackers.append(
            _qbaf_argument(
                "arg.robustness.replay_flaky",
                "Replay flaky",
                "attack",
                0.7,
                0.55,
                "Replay was flaky across rebuilds.",
            )
        )
    elif replay_status == "fail":
        robustness_attackers.append(
            _qbaf_argument(
                "arg.robustness.replay_fail",
                "Replay failed",
                "attack",
                1.0,
                0.8,
                "Replay failure directly attacks robustness.",
            )
        )
    if robustness.get("cross_tool_review_status") == "pass":
        robustness_supporters.append(
            _qbaf_argument(
                "arg.robustness.cross_tool_review",
                "Cross-tool review passed",
                "support",
                1.0,
                0.35,
                "Cross-tool review supports robustness.",
            )
        )
    if linter_finding_count > 0:
        robustness_attackers.append(
            _qbaf_argument(
                "arg.robustness.linter_findings",
                "Linter findings",
                "attack",
                min(1.0, 0.2 + 0.1 * linter_finding_count),
                0.35,
                "Linter findings reduce robustness confidence.",
            )
        )
    if model_health.get("premise_sensitivity") == "stable":
        robustness_supporters.append(
            _qbaf_argument(
                "arg.robustness.premise_stable",
                "Premise sensitivity stable",
                "support",
                0.85,
                0.45,
                "Premise-deletion replay remained stable.",
            )
        )
    elif model_health.get("premise_sensitivity") == "fragile":
        robustness_attackers.append(
            _qbaf_argument(
                "arg.robustness.premise_fragile",
                "Premise sensitivity fragile",
                "attack",
                0.85,
                0.65,
                "Premise deletion revealed fragility.",
            )
        )
    if model_health.get("conclusion_perturbation") == "stable":
        robustness_supporters.append(
            _qbaf_argument(
                "arg.robustness.conclusion_stable",
                "Conclusion perturbation stable",
                "support",
                0.8,
                0.35,
                "Conclusion perturbation replay remained stable.",
            )
        )
    elif model_health.get("conclusion_perturbation") == "fragile":
        robustness_attackers.append(
            _qbaf_argument(
                "arg.robustness.conclusion_fragile",
                "Conclusion perturbation fragile",
                "attack",
                0.8,
                0.55,
                "Conclusion perturbation revealed fragility.",
            )
        )
    return dimensions


def _derive_qbaf_scores(
    base_vector_scores: dict[str, Any],
    trust_frontier: dict[str, Any],
    obligations: dict[str, Any],
    model_health: dict[str, Any],
    intent_alignment: dict[str, Any],
    coverage: dict[str, Any],
    robustness: dict[str, Any],
    support_profile: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    dimensions = _dimension_qbaf_arguments(
        trust_frontier,
        obligations,
        model_health,
        intent_alignment,
        coverage,
        robustness,
        support_profile,
    )
    breakdown_dimensions: list[dict[str, Any]] = []
    final_scores: dict[str, int] = {}
    aggregate_base_scores: list[float] = []
    aggregate_final_scores: list[float] = []

    for dimension_name in [
        "trust_base_integrity",
        "intent_alignment",
        "evidence_support",
        "coverage",
        "robustness",
    ]:
        base_score = _bounded_unit(float(base_vector_scores.get(dimension_name, 0)) / 100.0)
        supporters = list(dimensions[dimension_name]["supporters"])
        attackers = list(dimensions[dimension_name]["attackers"])
        final_score = df_quad_strength(
            base_score,
            [float(item["effectiveStrength"]) for item in attackers],
            [float(item["effectiveStrength"]) for item in supporters],
        )
        aggregate_base_scores.append(base_score)
        aggregate_final_scores.append(final_score)
        final_scores[dimension_name] = _bounded_int(final_score * 100)
        breakdown_dimensions.append(
            {
                "dimension": dimension_name,
                "baseScore": round(base_score, 4),
                "finalScore": round(final_score, 4),
                "supporters": supporters,
                "attackers": attackers,
            }
        )

    final_scores["scheduler_score"] = _bounded_int(
        sum(final_scores[name] for name in [
            "trust_base_integrity",
            "intent_alignment",
            "evidence_support",
            "coverage",
            "robustness",
        ]) / 5.0
    )
    proof_claim_breakdown = {
        "scoreMethod": PROOF_CLAIM_SCORE_METHOD,
        "scoreVersion": PROOF_CLAIM_SCORE_VERSION,
        "aggregateBaseScore": round(sum(aggregate_base_scores) / len(aggregate_base_scores), 4),
        "aggregateFinalScore": round(sum(aggregate_final_scores) / len(aggregate_final_scores), 4),
        "dimensions": breakdown_dimensions,
    }
    return final_scores, proof_claim_breakdown


def _determine_gate(profile: dict[str, Any]) -> str:
    formal_status = profile["formal_status"]
    support_status = profile["support_status"]
    intent_status = profile["intent_status"]

    if formal_status == "proof_complete":
        if (
            profile["obligations"]["sorry_count"] == 0
            and profile["obligations"]["oops_count"] == 0
            and profile["obligations"]["open_goal_count"] == 0
            and profile["model_health"]["countermodel_probe"] != "countermodel_found"
            and profile["model_health"]["vacuity_check"] != "fail"
            and intent_status == "aligned"
            and not profile["coverage"]["uncovered_claim_ids"]
        ):
            if support_status in {
                "literature_supported",
                "experimentally_supported",
                "simulation_supported",
                "test_supported",
                "mixed_supported",
            }:
                return "certified"
            return "dev_guarded"
        return "blocked"

    if formal_status in {"build_passed_no_proof", "skeleton_only"}:
        return "research_only"
    if formal_status == "build_failed":
        return "blocked"
    return "draft"


def _allowed_downstream(gate: str, support_status: str, vector_scores: dict[str, Any]) -> list[str]:
    if gate == "certified":
        downstream = ["research", "dev", "monitoring"]
        if support_status != "none" and vector_scores["robustness"] >= 85:
            downstream.append("release")
        return downstream
    if gate == "dev_guarded":
        return ["research", "dev", "monitoring"]
    if gate == "research_only":
        return ["research"]
    if gate == "blocked":
        return ["research"]
    return []


def validate_promotion_rules(profile: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    gate = profile.get("gate", "draft")

    if gate == "certified":
        if profile.get("formal_status") != "proof_complete":
            violations.append("certified requires formal_status=proof_complete")

        obligations = profile.get("obligations", {})
        for field in ("sorry_count", "oops_count", "open_goal_count"):
            if int(obligations.get(field, 0)) > 0:
                violations.append(f"certified requires {field}=0")

        intent_alignment = profile.get("intent_alignment", {})
        if intent_alignment.get("backtranslation_review") == "fail":
            violations.append("certified requires backtranslation_review != fail")

        model_health = profile.get("model_health", {})
        if model_health.get("countermodel_probe") == "countermodel_found":
            violations.append("certified requires no countermodel found")
        if model_health.get("vacuity_check") == "fail":
            violations.append("certified requires vacuity_check != fail")

        trust_frontier = profile.get("trust_frontier", {})
        reviewed_global_axiom_ids = trust_frontier.get("reviewed_global_axiom_ids", [])
        if int(trust_frontier.get("global_axiom_dependency_count", 0)) > len(reviewed_global_axiom_ids):
            violations.append("certified requires all global axioms reviewed")
        if int(trust_frontier.get("oracle_dependency_count", 0)) > 0:
            violations.append("certified requires oracle_dependency_count=0 or explicit review")

        coverage = profile.get("coverage", {})
        if coverage.get("uncovered_claim_ids"):
            violations.append("certified requires required supporting claim links")

        if not profile.get("allowed_downstream"):
            violations.append("certified requires allowed_downstream explicitly set")

    if gate == "dev_guarded":
        intent_alignment = profile.get("intent_alignment", {})
        if intent_alignment.get("backtranslation_review") == "fail":
            violations.append("dev_guarded requires backtranslation_review != fail")

        obligations = profile.get("obligations", {})
        for field in ("sorry_count", "oops_count", "open_goal_count"):
            if int(obligations.get(field, 0)) > 0:
                violations.append(f"dev_guarded requires {field}=0 in promoted artifacts")

        model_health = profile.get("model_health", {})
        if model_health.get("countermodel_probe") == "countermodel_found":
            violations.append("dev_guarded requires no unresolved countermodel")

    return violations


def compute_assurance_profile(data: AssuranceComputationInput) -> AssuranceProfile:
    timestamp = _now()
    best_verifier = _best_verifier_result(data.verifier_output)
    formal_status = _formal_status(best_verifier)
    runner_trust = _trust_frontier_from_runner(data)
    probe_results = _normalize_probe_results(data.probe_results)

    trust_frontier_defaults = {
        "global_axiom_dependency_count": 0,
        "locale_assumption_count": 0,
        "premise_assumption_count": 0,
        "oracle_dependency_count": 0,
        "unreviewed_import_count": 0,
        "transitive_dependency_count": 0,
        "reviewed_global_axiom_ids": [],
        "oracle_ids": [],
        "hotspot_artifact_ids": [],
        "notes": [],
    }
    trust_frontier = _merge_trust_frontier(
        data.audit_output,
        runner_trust,
        trust_frontier_defaults,
    )
    conservativity = _default_audit_section(
        data.audit_output,
        "conservativity",
        {
            "definitional_only": True,
            "reviewed_global_axioms_required": False,
            "compile_away_known": False,
            "nondefinitional_hotspots": [],
            "trusted_mechanisms": [],
            "flagged_mechanisms": [],
        },
    )
    model_health = _merge_model_health_signals(
        _default_audit_section(
            data.audit_output,
            "model_health",
            {
                "locale_satisfiability": "untested",
                "countermodel_probe": "untested",
                "vacuity_check": "untested",
                "premise_sensitivity": "untested",
                "conclusion_perturbation": "untested",
                "notes": [],
            },
        ),
        probe_results,
        data.robustness_harness,
    )
    intent_alignment = _default_audit_section(
        data.audit_output,
        "intent_alignment",
        {
            "independent_formalization_count": 0,
            "agreement_score": 0.0,
            "backtranslation_review": "unreviewed",
            "paraphrase_robustness_score": 0.0,
            "semantics_guard_violations": [],
            "reviewer_notes": [],
        },
    )
    intent_status = _intent_status(intent_alignment)
    support_status = _support_status(data.research_output)
    coverage = _derive_coverage(data, formal_status)
    if data.research_output:
        coverage["evidence_link_coverage_score"] = 1.0 if data.research_output.get("evidence_items") else 0.0

    obligations = _derive_obligations(best_verifier, data.audit_output)
    robustness = _derive_robustness(data.verifier_output, data.audit_output)
    support_profile = _derive_support_profile(data.research_output)
    base_vector_scores = _derive_base_vector_scores(
        trust_frontier,
        obligations,
        model_health,
        intent_alignment,
        coverage,
        robustness,
        support_profile,
    )
    vector_scores, proof_claim_breakdown = _derive_qbaf_scores(
        base_vector_scores,
        trust_frontier,
        obligations,
        model_health,
        intent_alignment,
        coverage,
        robustness,
        support_profile,
    )

    artifact = best_verifier.get("formal_artifact", {})
    artifact_session = str(
        artifact.get("session")
        or artifact.get("module")
        or artifact.get("theory")
        or "unknown"
    )
    artifact_theory = str(
        artifact.get("module")
        or artifact.get("theory")
        or artifact.get("session")
        or "unknown"
    )
    target_formal_artifact = {
        "artifact_id": artifact.get(
            "node_id",
            f"artifact.{canonical_id(data.claim['claim_id'])}",
        ),
        "system": _normalized_formal_system(best_verifier, artifact),
        "session": artifact_session,
        "theory": artifact_theory,
        "theorem": artifact.get(
            "identifier",
            artifact.get("targets_found", ["unknown"])[0]
            if best_verifier.get("targets_found")
            else "unknown",
        ),
    }

    profile_data: dict[str, Any] = {
        "schema_version": "1.0.0",
        "profile_id": data.profile_id
        or f"profile.{canonical_id(data.claim['claim_id'])}",
        "project_id": data.project_id,
        "created_at": (
            data.existing_profile.get("created_at")
            if data.existing_profile and data.existing_profile.get("created_at")
            else timestamp.isoformat()
        ),
        "updated_at": timestamp.isoformat(),
        "claim_id": canonical_id(data.claim["claim_id"]),
        "claim_graph_ref": data.claim_graph_ref,
        "assurance_graph_ref": data.assurance_graph_ref,
        "target_formal_artifact": target_formal_artifact,
        "formal_status": formal_status,
        "support_status": support_status,
        "intent_status": intent_status,
        "decision_rationale": "",
        "trust_frontier": trust_frontier,
        "conservativity": conservativity,
        "obligations": obligations,
        "model_health": model_health,
        "intent_alignment": intent_alignment,
        "coverage": coverage,
        "robustness": robustness,
        "support_profile": support_profile,
        "vector_scores": vector_scores,
        "proofClaim": {
            "score": int(vector_scores["scheduler_score"]),
            "scoreMethod": PROOF_CLAIM_SCORE_METHOD,
            "scoreVersion": PROOF_CLAIM_SCORE_VERSION,
            "scoreBreakdownRef": "",
        },
        "proofClaimBreakdown": proof_claim_breakdown,
        "allowed_downstream": [],
        "required_actions": [],
        "expires_at": (timestamp + timedelta(days=90)).isoformat(),
    }

    gate = _determine_gate(profile_data)
    profile_data["gate"] = gate
    profile_data["allowed_downstream"] = _allowed_downstream(
        gate,
        support_status,
        vector_scores,
    )
    profile_data["proofClaim"]["scoreBreakdownRef"] = (
        f"profile:{profile_data['profile_id']}#proofClaimBreakdown"
    )

    required_actions = list((data.audit_output or {}).get("blocking_issues", []))
    required_actions.extend(list((data.audit_output or {}).get("warnings", [])))
    if profile_data["coverage"]["uncovered_claim_ids"]:
        required_actions.append("Link required supporting claims into the promotion surface.")
    if profile_data["model_health"]["countermodel_probe"] == "countermodel_found":
        required_actions.append("Resolve the countermodel before promotion.")
    if profile_data["model_health"]["vacuity_check"] == "fail":
        required_actions.append("Resolve the vacuity failure before promotion.")
    if profile_data["model_health"]["premise_sensitivity"] == "fragile":
        required_actions.append("Review fragile premise dependencies before promotion.")
    if profile_data["model_health"]["conclusion_perturbation"] == "fragile":
        required_actions.append("Review fragile conclusion perturbation results before promotion.")
    if profile_data["trust_frontier"]["global_axiom_dependency_count"] > len(
        profile_data["trust_frontier"]["reviewed_global_axiom_ids"]
    ):
        required_actions.append("Review theorem-local global axioms before certification.")
    if profile_data["trust_frontier"]["oracle_dependency_count"] > 0:
        required_actions.append("Review or eliminate theorem-local oracle dependencies.")
    if profile_data["intent_alignment"]["backtranslation_review"] in {"unreviewed", "needs_revision"}:
        required_actions.append("Complete backtranslation review before promotion.")
    if gate == "research_only":
        required_actions.append("Finish proof obligations before promotion beyond research.")

    profile_data["required_actions"] = list(dict.fromkeys(required_actions))
    profile_data["decision_rationale"] = (
        f"Gate={gate} from formal_status={formal_status}, "
        f"intent_status={intent_status}, support_status={support_status}, "
        f"coverage={profile_data['coverage']['dependency_coverage_score']:.2f}, "
        f"robustness={profile_data['vector_scores']['robustness']}, "
        f"proofClaim.score={profile_data['proofClaim']['score']} via {PROOF_CLAIM_SCORE_METHOD}."
    )

    violations = validate_promotion_rules(profile_data)
    if violations:
        profile_data["required_actions"].extend(
            [f"[VIOLATION] {violation}" for violation in violations]
        )
        if gate in {"certified", "dev_guarded"}:
            profile_data["gate"] = "blocked"
            profile_data["allowed_downstream"] = ["research"]
            profile_data["decision_rationale"] = (
                f"Gate downgraded to blocked because: {'; '.join(violations)}"
            )

    return AssuranceProfile.model_validate(profile_data)


__all__ = [
    "AssuranceComputationInput",
    "compute_assurance_profile",
    "validate_promotion_rules",
]
