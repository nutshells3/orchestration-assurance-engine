"""Artifact provenance tracking and fallback guard for OAE export integrity.

BRG-003: Expose proof-assistant transcript / run-log provenance as sidecar refs.
BRG-004: Forbid silent run.logs fallback from becoming canonical export data.

Every artifact that appears in a trace export must carry an explicit
``ArtifactCanonicity`` marker.  Fallback or reconstructed artifacts are
permitted only in sidecar positions (``sidecar_meta.json``), never as
canonical export data in ``trace.json`` or ``build_results``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Canonicity classification
# ---------------------------------------------------------------------------

class ArtifactCanonicity(str, Enum):
    """Marks whether an artifact is canonical or reconstructed."""

    canonical = "canonical"      # Primary result from authoritative source
    fallback = "fallback"        # Reconstructed from logs / transcripts
    surrogate = "surrogate"      # Substitute when primary unavailable
    unknown = "unknown"          # Legacy artifact without provenance


_NON_CANONICAL = {
    ArtifactCanonicity.fallback,
    ArtifactCanonicity.surrogate,
    ArtifactCanonicity.unknown,
}

# Artifact dict keys that signal a canonical export position.
_CANONICAL_EXPORT_KEYS = {
    "build_results",
    "verifier_results",
    "proof_audit",
    "audit_output",
    "profile",
}


# ---------------------------------------------------------------------------
# Provenance ref
# ---------------------------------------------------------------------------

class ArtifactProvenanceRef:
    """Reference to an artifact's origin without including raw data."""

    __slots__ = (
        "artifact_id",
        "canonicity",
        "source_type",
        "source_ref",
        "timestamp",
        "notes",
    )

    def __init__(
        self,
        artifact_id: str,
        canonicity: ArtifactCanonicity,
        source_type: str,
        source_ref: str | None,
        timestamp: str | None = None,
        notes: str | None = None,
    ) -> None:
        self.artifact_id = artifact_id
        self.canonicity = ArtifactCanonicity(canonicity)
        self.source_type = source_type
        self.source_ref = source_ref
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        self.notes = notes

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "canonicity": self.canonicity.value,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "timestamp": self.timestamp,
        }
        if self.notes:
            result["notes"] = self.notes
        return result

    def __repr__(self) -> str:
        return (
            f"ArtifactProvenanceRef({self.artifact_id!r}, "
            f"canonicity={self.canonicity.value!r}, "
            f"source_type={self.source_type!r})"
        )


# ---------------------------------------------------------------------------
# Provenance registry
# ---------------------------------------------------------------------------

class ProvenanceRegistry:
    """Tracks artifact provenance for a trace export session."""

    def __init__(self) -> None:
        self._refs: dict[str, ArtifactProvenanceRef] = {}

    # -- registration --------------------------------------------------------

    def register(self, ref: ArtifactProvenanceRef) -> None:
        """Register an artifact's provenance."""
        self._refs[ref.artifact_id] = ref

    def get(self, artifact_id: str) -> ArtifactProvenanceRef | None:
        """Look up provenance for a single artifact."""
        return self._refs.get(artifact_id)

    # -- queries -------------------------------------------------------------

    def get_fallbacks(self) -> list[ArtifactProvenanceRef]:
        """Return all non-canonical artifact refs."""
        return [
            ref for ref in self._refs.values()
            if ref.canonicity in _NON_CANONICAL
        ]

    @property
    def all_refs(self) -> list[ArtifactProvenanceRef]:
        return list(self._refs.values())

    # -- export --------------------------------------------------------------

    def to_sidecar_section(self) -> dict[str, Any]:
        """Export provenance data for ``sidecar_meta.json``."""
        refs_list = [ref.to_dict() for ref in self._refs.values()]
        fallbacks = self.get_fallbacks()
        return {
            "artifact_provenance": refs_list,
            "fallback_count": len(fallbacks),
            "canonical_count": len(refs_list) - len(fallbacks),
            "has_non_canonical": len(fallbacks) > 0,
        }

    # -- validation ----------------------------------------------------------

    def validate_no_silent_fallback(self) -> list[str]:
        """Check that no fallback artifact lacks explicit marking.

        Returns a list of violation descriptions.  An empty list means
        all non-canonical artifacts are properly annotated.
        """
        violations: list[str] = []
        for ref in self._refs.values():
            if ref.canonicity == ArtifactCanonicity.unknown:
                violations.append(
                    f"artifact {ref.artifact_id!r} has canonicity='unknown' "
                    f"(source_type={ref.source_type!r}) -- must be explicitly "
                    f"classified as canonical, fallback, or surrogate"
                )
            if ref.canonicity in _NON_CANONICAL and not ref.source_ref:
                violations.append(
                    f"artifact {ref.artifact_id!r} is {ref.canonicity.value} "
                    f"but has no source_ref provenance pointer"
                )
        return violations


# ---------------------------------------------------------------------------
# Fallback guard
# ---------------------------------------------------------------------------

class FallbackGuard:
    """Prevents fallback artifacts from being treated as canonical."""

    @staticmethod
    def check_export_data(
        export_data: dict[str, Any],
        registry: ProvenanceRegistry,
    ) -> list[str]:
        """Validate that no fallback artifact appears in canonical export positions.

        Inspects the top-level keys in *export_data* against a known set
        of canonical positions (``build_results``, ``audit_output``, etc.)
        and cross-references with the provenance *registry*.

        Returns a list of human-readable violation strings.
        """
        violations: list[str] = []

        for key in _CANONICAL_EXPORT_KEYS:
            value = export_data.get(key)
            if not isinstance(value, dict):
                continue

            # Check for the inline canonicity marker
            marker = value.get("_canonicity")
            if marker and marker != ArtifactCanonicity.canonical.value:
                violations.append(
                    f"artifact in '{key}' is marked "
                    f"_canonicity={marker!r} but sits in a canonical "
                    f"export position"
                )

            # Check for run.logs or transcript source without marking
            source_hint = value.get("_source_type") or ""
            if source_hint in ("run_log", "transcript") and marker is None:
                violations.append(
                    f"artifact in '{key}' originates from "
                    f"{source_hint!r} but lacks _canonicity marker"
                )

        # Cross-check against the registry: any registered non-canonical
        # artifact whose id appears at a canonical position is a violation.
        for ref in registry.get_fallbacks():
            for key in _CANONICAL_EXPORT_KEYS:
                value = export_data.get(key)
                if not isinstance(value, dict):
                    continue
                # Match on artifact_id if present, or on the key itself
                artifact_id = value.get("artifact_id") or value.get("id") or ""
                if artifact_id == ref.artifact_id:
                    violations.append(
                        f"artifact {ref.artifact_id!r} from "
                        f"{ref.source_type!r} promoted to canonical "
                        f"position '{key}' without provenance clearance"
                    )

        return violations

    @staticmethod
    def mark_artifact(
        artifact: dict[str, Any],
        canonicity: ArtifactCanonicity,
        *,
        source_type: str | None = None,
    ) -> dict[str, Any]:
        """Add canonicity marker to an artifact dict.

        Returns the same dict (mutated in place) for chaining convenience.
        """
        artifact["_canonicity"] = canonicity.value
        if source_type is not None:
            artifact["_source_type"] = source_type
        return artifact

    @staticmethod
    def strip_markers(artifact: dict[str, Any]) -> dict[str, Any]:
        """Remove internal canonicity markers before final serialisation."""
        artifact.pop("_canonicity", None)
        artifact.pop("_source_type", None)
        return artifact

    @staticmethod
    def artifact_content_hash(artifact: dict[str, Any]) -> str:
        """Deterministic hash of artifact content, ignoring internal markers."""
        clean = {
            k: v for k, v in artifact.items()
            if not k.startswith("_")
        }
        serialized = json.dumps(clean, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
