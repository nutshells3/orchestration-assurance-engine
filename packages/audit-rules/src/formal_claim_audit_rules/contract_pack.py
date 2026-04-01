"""Deterministic Contract Pack emitter and downstream policy projection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .compat import canonical_id


@dataclass(frozen=True)
class ContractPack:
    pack_id: str
    project_id: str
    claim_id: str
    profile_id: str
    gate: str
    allowed_downstream: list[str]
    blocked_actions: list[str]
    allowed_assumptions: list[str]
    referenced_artifact_ids: list[str]
    required_runtime_guards: list[str]
    review_status: str
    notes: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _profile_value(profile: Any, field: str, default: Any = None) -> Any:
    if isinstance(profile, dict):
        return profile.get(field, default)
    return getattr(profile, field, default)


def _nested_value(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _blocked_actions(gate: str, allowed_downstream: list[str]) -> list[str]:
    blocked = {"treat_as_certified", "ship_to_release", "ignore_required_actions"}
    if "dev" not in allowed_downstream:
        blocked.add("use_for_implementation")
    if "release" in allowed_downstream:
        blocked.discard("ship_to_release")
    if gate == "certified":
        blocked.discard("treat_as_certified")
    return sorted(blocked)


def project_downstream_policy(profile: Any) -> dict[str, Any]:
    """Project allowed consumers and blocked actions from an assurance profile."""
    gate = str(_profile_value(profile, "gate", "draft"))
    allowed_downstream = [
        canonical_id(value)
        for value in _profile_value(profile, "allowed_downstream", [])
    ]
    return {
        "gate": gate,
        "allowed_downstream": allowed_downstream,
        "blocked_actions": _blocked_actions(gate, allowed_downstream),
        "requires_human_review": gate not in {"certified", "dev_guarded"},
    }


def emit_contract_pack(profile: Any) -> ContractPack:
    """Emit a deterministic downstream contract pack from an assurance profile."""
    policy = project_downstream_policy(profile)
    trust_frontier = _profile_value(profile, "trust_frontier", {}) or {}
    target_formal_artifact = _profile_value(profile, "target_formal_artifact", {}) or {}
    trust_get = (
        trust_frontier.get
        if isinstance(trust_frontier, dict)
        else lambda field, default=None: getattr(trust_frontier, field, default)
    )
    required_actions = [
        canonical_id(value)
        for value in _profile_value(profile, "required_actions", [])
    ]

    allowed_assumptions = sorted(
        {
            canonical_id(value)
            for value in trust_get("reviewed_global_axiom_ids", [])
        }
    )
    referenced_artifact_ids = sorted(
        {
            canonical_id(_nested_value(target_formal_artifact, "artifact_id", ""))
            if _nested_value(target_formal_artifact, "artifact_id")
            else "",
            *[
                canonical_id(value)
                for value in trust_get("hotspot_artifact_ids", [])
            ],
        }
        - {""}
    )
    required_runtime_guards = []
    if int(trust_get("locale_assumption_count", 0)) > 0:
        required_runtime_guards.append("preserve_locale_assumptions")
    if _profile_value(profile, "gate", "draft") != "certified":
        required_runtime_guards.append("human_review_before_release")

    review_status = str(
        (_profile_value(profile, "intent_alignment", {}) or {}).get(
            "backtranslation_review",
            "unreviewed",
        )
    )
    notes = list(dict.fromkeys(required_actions + list(trust_get("notes", []))))
    return ContractPack(
        pack_id=f"contract.{canonical_id(_profile_value(profile, 'claim_id', 'unknown'))}",
        project_id=canonical_id(_profile_value(profile, "project_id", "project.unknown")),
        claim_id=canonical_id(_profile_value(profile, "claim_id", "claim.unknown")),
        profile_id=canonical_id(_profile_value(profile, "profile_id", "profile.unknown")),
        gate=str(_profile_value(profile, "gate", "draft")),
        allowed_downstream=policy["allowed_downstream"],
        blocked_actions=policy["blocked_actions"],
        allowed_assumptions=allowed_assumptions,
        referenced_artifact_ids=referenced_artifact_ids,
        required_runtime_guards=required_runtime_guards,
        review_status=review_status,
        notes=notes,
    )


__all__ = ["ContractPack", "emit_contract_pack", "project_downstream_policy"]
