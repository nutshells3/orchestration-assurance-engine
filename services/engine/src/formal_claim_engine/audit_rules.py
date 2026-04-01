"""Compatibility shim over the canonical deterministic audit-rules package."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_audit_rules_src() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "packages" / "audit-rules" / "src"
        if candidate.exists():
            return candidate
    raise RuntimeError("Could not locate packages/audit-rules/src from engine.")


AUDIT_RULES_SRC = resolve_audit_rules_src()
if str(AUDIT_RULES_SRC) not in sys.path:
    sys.path.insert(0, str(AUDIT_RULES_SRC))

from formal_claim_audit_rules import (  # noqa: E402
    AssuranceComputationInput,
    ContractPack,
    compute_assurance_profile,
    emit_contract_pack,
    project_downstream_policy,
    validate_promotion_rules,
)

__all__ = [
    "AssuranceComputationInput",
    "ContractPack",
    "compute_assurance_profile",
    "emit_contract_pack",
    "project_downstream_policy",
    "validate_promotion_rules",
]
