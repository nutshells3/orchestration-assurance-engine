"""Canonical contract imports for graph-model without legacy naming."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_contracts_src() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "packages" / "contracts-py" / "src"
        if candidate.exists():
            return candidate
    raise RuntimeError("Could not locate packages/contracts-py/src from graph-model.")


CONTRACTS_PY_SRC = resolve_contracts_src()
if str(CONTRACTS_PY_SRC) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_PY_SRC))

from formal_claim_contracts import AssuranceGraph, ClaimGraph  # noqa: E402
from formal_claim_contracts.assurance_graph import Edge, Node  # noqa: E402
from formal_claim_contracts.claim_graph import Claim, Relation  # noqa: E402


def canonical_id(value: object) -> str:
    root_value = getattr(value, "root", None)
    if isinstance(root_value, str):
        return root_value
    return str(value)


def node_payload(node: Node):
    return getattr(node, "root", node)


__all__ = [
    "AssuranceGraph",
    "Claim",
    "ClaimGraph",
    "Edge",
    "Node",
    "Relation",
    "canonical_id",
    "node_payload",
]
