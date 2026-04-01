"""Tracks provenance of pipeline artifacts: canonical vs fallback.

Every artifact produced during a pipeline run must be registered in the
ProvenanceRegistry with an explicit marker indicating whether it is
canonical (produced by the primary pipeline path) or a fallback (produced
by a recovery path, cached result, or degraded mode).

The registry enforces:
* No artifact can be exported without provenance registration.
* Fallback artifacts cannot silently appear in canonical positions.
* All artifacts are categorized and queryable.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ArtifactLineage(str, Enum):
    canonical = "canonical"
    fallback = "fallback"


class ProvenanceViolation:
    """Represents a provenance policy violation."""

    def __init__(self, artifact_id: str, message: str) -> None:
        self.artifact_id = artifact_id
        self.message = message

    def __repr__(self) -> str:
        return f"ProvenanceViolation({self.artifact_id!r}, {self.message!r})"


class ProvenanceRegistry:
    """Tracks artifact provenance and enforces lineage policies."""

    def __init__(self) -> None:
        self._artifacts: dict[str, dict[str, Any]] = {}

    def register(
        self,
        artifact_id: str,
        lineage: ArtifactLineage,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register an artifact with its lineage marker."""
        self._artifacts[artifact_id] = {
            "artifact_id": artifact_id,
            "lineage": lineage,
            "source": source,
            "metadata": metadata or {},
        }

    def get_lineage(self, artifact_id: str) -> ArtifactLineage | None:
        """Return the lineage of a registered artifact, or None if unregistered."""
        entry = self._artifacts.get(artifact_id)
        if entry is None:
            return None
        return entry["lineage"]

    def is_registered(self, artifact_id: str) -> bool:
        return artifact_id in self._artifacts

    def list_canonical(self) -> list[str]:
        """Return IDs of all canonical artifacts."""
        return sorted(
            aid for aid, entry in self._artifacts.items()
            if entry["lineage"] == ArtifactLineage.canonical
        )

    def list_fallback(self) -> list[str]:
        """Return IDs of all fallback artifacts."""
        return sorted(
            aid for aid, entry in self._artifacts.items()
            if entry["lineage"] == ArtifactLineage.fallback
        )

    def validate_canonical_position(
        self,
        artifact_id: str,
        position: str = "build_results",
    ) -> list[ProvenanceViolation]:
        """Check that an artifact in a canonical position is actually canonical.

        Returns a list of violations (empty = OK).
        """
        violations: list[ProvenanceViolation] = []
        if not self.is_registered(artifact_id):
            violations.append(ProvenanceViolation(
                artifact_id,
                f"Artifact '{artifact_id}' in {position} has no provenance registration.",
            ))
            return violations
        lineage = self.get_lineage(artifact_id)
        if lineage == ArtifactLineage.fallback:
            violations.append(ProvenanceViolation(
                artifact_id,
                f"Fallback artifact '{artifact_id}' found in canonical position '{position}'.",
            ))
        return violations

    def validate_export_guard(
        self,
        artifact_ids: list[str],
    ) -> list[ProvenanceViolation]:
        """Verify all artifacts have provenance before export.

        Any artifact without registration is flagged as a violation.
        """
        violations: list[ProvenanceViolation] = []
        for aid in artifact_ids:
            if not self.is_registered(aid):
                violations.append(ProvenanceViolation(
                    aid,
                    f"Artifact '{aid}' cannot be exported: no provenance registration.",
                ))
        return violations

    def detect_unmarked_fallback(
        self,
        artifact_id: str,
        source_location: str = "run.logs",
    ) -> list[ProvenanceViolation]:
        """Check if an artifact from a given source location lacks a lineage marker.

        If the artifact is registered, no violation.  If unregistered, it is
        treated as an unmarked fallback.
        """
        violations: list[ProvenanceViolation] = []
        if not self.is_registered(artifact_id):
            violations.append(ProvenanceViolation(
                artifact_id,
                f"Unmarked artifact '{artifact_id}' from {source_location}: "
                f"no lineage marker found.",
            ))
        return violations
