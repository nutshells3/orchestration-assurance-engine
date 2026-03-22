"""Compatibility helpers for the audit rules package."""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_src(relative_parts: tuple[str, ...], label: str) -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent.joinpath(*relative_parts)
        if candidate.exists():
            return candidate
    raise RuntimeError(f"Could not locate {label} from audit-rules.")


CONTRACTS_PY_SRC = resolve_src(("packages", "contracts-py", "src"), "contracts-py")
GRAPH_MODEL_SRC = resolve_src(("packages", "graph-model", "src"), "graph-model")

for path in (CONTRACTS_PY_SRC, GRAPH_MODEL_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from formal_claim_contracts.assurance_profile import AssuranceProfile  # noqa: E402
from formal_claim_graph import ClaimGraphQueries, summarize_theorem_trust  # noqa: E402


def canonical_id(value: object) -> str:
    root_value = getattr(value, "root", None)
    if isinstance(root_value, str):
        return root_value
    return str(value)


__all__ = ["AssuranceProfile", "ClaimGraphQueries", "canonical_id", "summarize_theorem_trust"]
