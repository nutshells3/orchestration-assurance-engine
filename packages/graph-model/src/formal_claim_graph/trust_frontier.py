"""Theorem-local trust frontier summaries derived from runner payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _canonical_string(value: object) -> str:
    root_value = getattr(value, "root", None)
    if isinstance(root_value, str):
        return root_value
    return str(value)


def _get_field(payload: Any, name: str, default: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


@dataclass(frozen=True)
class TheoremTrustSummary:
    target_theorem: str
    global_axiom_dependency_count: int
    locale_assumption_count: int
    premise_assumption_count: int
    oracle_dependency_count: int
    reviewed_exception_count: int
    transitive_dependency_count: int
    oracle_ids: list[str]
    reviewed_global_axiom_ids: list[str]
    reviewed_exception_ids: list[str]
    hotspot_artifact_ids: list[str]
    notes: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "target_theorem": self.target_theorem,
            "global_axiom_dependency_count": self.global_axiom_dependency_count,
            "locale_assumption_count": self.locale_assumption_count,
            "premise_assumption_count": self.premise_assumption_count,
            "oracle_dependency_count": self.oracle_dependency_count,
            "reviewed_exception_count": self.reviewed_exception_count,
            "transitive_dependency_count": self.transitive_dependency_count,
            "oracle_ids": list(self.oracle_ids),
            "reviewed_global_axiom_ids": list(self.reviewed_global_axiom_ids),
            "reviewed_exception_ids": list(self.reviewed_exception_ids),
            "hotspot_artifact_ids": list(self.hotspot_artifact_ids),
            "notes": list(self.notes),
        }


def summarize_theorem_trust(payload: Any) -> TheoremTrustSummary:
    """Normalize theorem-local trust payloads into frontier counts and hotspots."""
    surface = _get_field(payload, "surface", payload)
    target_theorem = _canonical_string(_get_field(surface, "target_theorem", "unknown"))
    global_axiom_ids = sorted(
        {_canonical_string(value) for value in _get_field(surface, "global_axiom_ids", [])}
    )
    reviewed_global_axiom_ids = sorted(
        {
            _canonical_string(value)
            for value in _get_field(surface, "reviewed_global_axiom_ids", [])
            if _canonical_string(value) in set(global_axiom_ids)
        }
    )
    reviewed_exception_ids = sorted(
        {
            _canonical_string(value)
            for value in _get_field(surface, "reviewed_exception_ids", [])
        }
    )
    oracle_ids = sorted(
        {
            _canonical_string(value)
            for value in _get_field(surface, "oracle_ids", [])
        }
    )
    hotspot_artifact_ids = sorted(
        {
            _canonical_string(value)
            for value in _get_field(surface, "imported_theory_hotspots", [])
        }
    )
    notes = list(
        dict.fromkeys(
            _canonical_string(value)
            for value in _get_field(surface, "notes", [])
            if str(value).strip()
        )
    )
    return TheoremTrustSummary(
        target_theorem=target_theorem,
        global_axiom_dependency_count=len(global_axiom_ids),
        locale_assumption_count=len(_get_field(surface, "locale_assumptions", [])),
        premise_assumption_count=len(_get_field(surface, "premise_assumptions", [])),
        oracle_dependency_count=len(oracle_ids),
        reviewed_exception_count=len(reviewed_exception_ids),
        transitive_dependency_count=len(
            {
                _canonical_string(value)
                for value in _get_field(surface, "transitive_theorem_dependencies", [])
            }
        ),
        oracle_ids=oracle_ids,
        reviewed_global_axiom_ids=reviewed_global_axiom_ids,
        reviewed_exception_ids=reviewed_exception_ids,
        hotspot_artifact_ids=hotspot_artifact_ids,
        notes=notes,
    )


__all__ = ["TheoremTrustSummary", "summarize_theorem_trust"]
