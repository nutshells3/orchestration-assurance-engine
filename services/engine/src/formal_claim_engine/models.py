"""Compatibility shim over the canonical generated contract bindings."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_contracts_src() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "packages" / "contracts-py" / "src"
        if candidate.exists():
            return candidate
    raise RuntimeError("Could not locate packages/contracts-py/src from engine package.")


CONTRACTS_PY_SRC = resolve_contracts_src()

if str(CONTRACTS_PY_SRC) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_PY_SRC))

from formal_claim_contracts import (  # noqa: E402
    AssuranceGraph,
    AssuranceProfile,
    ClaimGraph,
    ClaimStatus,
    FormalStatus,
    Gate,
)

__all__ = [
    "AssuranceGraph",
    "AssuranceProfile",
    "ClaimGraph",
    "ClaimStatus",
    "FormalStatus",
    "Gate",
]
