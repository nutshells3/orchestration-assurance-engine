"""Optional safeslice integration helpers.

This module keeps the safeslice dependency strictly optional. The engine can
export claim-graph-derived safeslice tasks when the sibling safeslice source is
available, but the core workflows do not require it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from .config import REPO_ROOT


@dataclass(frozen=True)
class SafeSliceAvailability:
    available: bool
    source_path: str | None = None
    reason: str | None = None


def _candidate_safeslice_src_paths() -> tuple[Path, ...]:
    env_override = str(os.environ.get("FORMAL_CLAIM_SAFESLICE_SRC") or "").strip()
    candidates: list[Path] = []
    if env_override:
        candidates.append(Path(env_override))
    candidates.extend(
        [
            REPO_ROOT.parent / "safeslice" / "src",
            REPO_ROOT.parent / "_push" / "safeslice" / "src",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve()) if candidate.exists() else str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return tuple(unique)


def resolve_safeslice_src(src_path_override: str | None = None) -> Path | None:
    if src_path_override:
        override = Path(src_path_override)
        if (override / "safeslice" / "__init__.py").exists():
            return override
    for candidate in _candidate_safeslice_src_paths():
        if (candidate / "safeslice" / "__init__.py").exists():
            return candidate
    return None


def _activate_safeslice_src(path: Path) -> None:
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


def get_safeslice_availability(src_path_override: str | None = None) -> SafeSliceAvailability:
    source_path = resolve_safeslice_src(src_path_override=src_path_override)
    if source_path is None:
        return SafeSliceAvailability(
            available=False,
            reason="safeslice source not found; set FORMAL_CLAIM_SAFESLICE_SRC to enable.",
        )
    try:
        _activate_safeslice_src(source_path)
        import safeslice  # noqa: F401
    except Exception as exc:  # pragma: no cover - optional import failure
        return SafeSliceAvailability(
            available=False,
            source_path=str(source_path),
            reason=f"failed to import safeslice: {exc}",
        )
    return SafeSliceAvailability(available=True, source_path=str(source_path))


def build_safeslice_task_payload(
    claim_graph: dict[str, Any],
    *,
    target_claim_ids: Sequence[str] | None = None,
    thresholds: dict[str, Any] | None = None,
    ambiguity: dict[str, Any] | None = None,
    adapter_config: dict[str, Any] | None = None,
    src_path_override: str | None = None,
) -> dict[str, Any]:
    availability = get_safeslice_availability(src_path_override=src_path_override)
    if not availability.available:
        raise RuntimeError(availability.reason or "safeslice is unavailable")

    import safeslice  # type: ignore

    threshold_obj = (
        safeslice.ThresholdSpec(**dict(thresholds or {}))
        if thresholds
        else safeslice.ThresholdSpec()
    )
    ambiguity_obj = (
        safeslice.AmbiguitySpec(**dict(ambiguity or {}))
        if ambiguity
        else None
    )
    adapter_obj = (
        safeslice.ClaimGraphAdapterConfig(**dict(adapter_config or {}))
        if adapter_config
        else safeslice.ClaimGraphAdapterConfig()
    )
    task = safeslice.task_from_claim_graph(
        claim_graph,
        thresholds=threshold_obj,
        ambiguity=ambiguity_obj,
        target_claim_ids=target_claim_ids,
        config=adapter_obj,
    )
    return {
        "task": asdict(task),
        "availability": asdict(availability),
        "adapter_config": dict(adapter_config or {}),
    }
