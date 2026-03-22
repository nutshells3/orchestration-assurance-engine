"""Compatibility helpers for canonical contract imports."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_contracts_src() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "packages" / "contracts-py" / "src"
        if candidate.exists():
            return candidate
    raise RuntimeError("Could not locate packages/contracts-py/src from evidence connectors.")


CONTRACTS_SRC = resolve_contracts_src()
if str(CONTRACTS_SRC) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_SRC))

from formal_claim_contracts.claim_graph import ClaimGraph  # noqa: E402

__all__ = ["ClaimGraph"]
