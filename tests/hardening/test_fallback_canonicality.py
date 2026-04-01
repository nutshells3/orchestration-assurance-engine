"""VRF-003: Bridge tests for fallback canonicality and raw lineage markers.

Tests:
1. Canonical artifact passes validation
2. Unmarked fallback from run.logs is detected as violation
3. Fallback in canonical position (build_results) is detected as violation
4. ProvenanceRegistry tracks all artifacts with correct categorization
5. Silent fallback impossible: export without provenance is caught by guard
"""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from fallback canonicality test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.provenance_registry import (  # noqa: E402
    ArtifactLineage,
    ProvenanceRegistry,
    ProvenanceViolation,
)


# ---------------------------------------------------------------------------
# Test 1: Canonical artifact passes validation
# ---------------------------------------------------------------------------

def test_canonical_artifact_passes_validation() -> None:
    """Mark artifact as canonical -> no violations in canonical position check."""
    registry = ProvenanceRegistry()
    registry.register(
        "artifact.lean-proof-001",
        ArtifactLineage.canonical,
        source="primary_pipeline",
    )

    violations = registry.validate_canonical_position(
        "artifact.lean-proof-001",
        position="build_results",
    )
    assert len(violations) == 0, \
        f"Canonical artifact should pass validation, got violations: {violations}"


def test_canonical_artifact_export_guard() -> None:
    """Canonical artifacts pass the export guard."""
    registry = ProvenanceRegistry()
    registry.register("art.001", ArtifactLineage.canonical)
    registry.register("art.002", ArtifactLineage.canonical)

    violations = registry.validate_export_guard(["art.001", "art.002"])
    assert len(violations) == 0, \
        f"Registered artifacts should pass export guard: {violations}"


# ---------------------------------------------------------------------------
# Test 2: Unmarked fallback detected
# ---------------------------------------------------------------------------

def test_unmarked_fallback_detected() -> None:
    """Artifact from run.logs without lineage marker -> violation detected."""
    registry = ProvenanceRegistry()
    # Do NOT register the artifact

    violations = registry.detect_unmarked_fallback(
        "artifact.untracked-output-001",
        source_location="run.logs",
    )
    assert len(violations) == 1, \
        f"Expected 1 violation for unmarked fallback, got {len(violations)}"
    assert "no lineage marker" in violations[0].message
    assert "run.logs" in violations[0].message


def test_marked_artifact_no_unmarked_violation() -> None:
    """Artifact with explicit fallback marker -> no unmarked-fallback violation."""
    registry = ProvenanceRegistry()
    registry.register(
        "artifact.fallback-001",
        ArtifactLineage.fallback,
        source="recovery_path",
    )

    violations = registry.detect_unmarked_fallback(
        "artifact.fallback-001",
        source_location="run.logs",
    )
    assert len(violations) == 0, \
        "Explicitly marked fallback should not trigger unmarked violation"


# ---------------------------------------------------------------------------
# Test 3: Fallback in canonical position detected
# ---------------------------------------------------------------------------

def test_fallback_in_canonical_position_detected() -> None:
    """Fallback artifact in build_results -> violation detected."""
    registry = ProvenanceRegistry()
    registry.register(
        "artifact.cached-proof-001",
        ArtifactLineage.fallback,
        source="cache_recovery",
    )

    violations = registry.validate_canonical_position(
        "artifact.cached-proof-001",
        position="build_results",
    )
    assert len(violations) == 1, \
        f"Expected 1 violation for fallback in canonical position, got {len(violations)}"
    assert "Fallback artifact" in violations[0].message
    assert "canonical position" in violations[0].message
    assert "build_results" in violations[0].message


def test_unregistered_in_canonical_position() -> None:
    """Unregistered artifact in canonical position -> violation."""
    registry = ProvenanceRegistry()

    violations = registry.validate_canonical_position(
        "artifact.mystery-001",
        position="build_results",
    )
    assert len(violations) == 1
    assert "no provenance registration" in violations[0].message


# ---------------------------------------------------------------------------
# Test 4: ProvenanceRegistry tracks all artifacts correctly
# ---------------------------------------------------------------------------

def test_registry_tracks_mixed_artifacts() -> None:
    """Register mix of canonical/fallback -> correct categorization."""
    registry = ProvenanceRegistry()

    # Register canonical artifacts
    registry.register("art.canonical-001", ArtifactLineage.canonical, source="primary")
    registry.register("art.canonical-002", ArtifactLineage.canonical, source="primary")
    registry.register("art.canonical-003", ArtifactLineage.canonical, source="primary")

    # Register fallback artifacts
    registry.register("art.fallback-001", ArtifactLineage.fallback, source="cache")
    registry.register("art.fallback-002", ArtifactLineage.fallback, source="recovery")

    canonical = registry.list_canonical()
    fallback = registry.list_fallback()

    assert len(canonical) == 3, f"Expected 3 canonical, got {len(canonical)}"
    assert len(fallback) == 2, f"Expected 2 fallback, got {len(fallback)}"

    assert "art.canonical-001" in canonical
    assert "art.canonical-002" in canonical
    assert "art.canonical-003" in canonical
    assert "art.fallback-001" in fallback
    assert "art.fallback-002" in fallback


def test_registry_lineage_queries() -> None:
    """get_lineage returns correct lineage for each artifact."""
    registry = ProvenanceRegistry()
    registry.register("art.a", ArtifactLineage.canonical)
    registry.register("art.b", ArtifactLineage.fallback)

    assert registry.get_lineage("art.a") == ArtifactLineage.canonical
    assert registry.get_lineage("art.b") == ArtifactLineage.fallback
    assert registry.get_lineage("art.nonexistent") is None


def test_registry_is_registered() -> None:
    """is_registered correctly identifies registered vs unregistered artifacts."""
    registry = ProvenanceRegistry()
    registry.register("art.registered", ArtifactLineage.canonical)

    assert registry.is_registered("art.registered") is True
    assert registry.is_registered("art.unknown") is False


def test_registry_metadata_preserved() -> None:
    """Registration metadata is preserved."""
    registry = ProvenanceRegistry()
    registry.register(
        "art.meta-test",
        ArtifactLineage.canonical,
        source="integration_test",
        metadata={"build_id": "build.001", "step": 3},
    )

    # Access internal state to verify metadata
    entry = registry._artifacts["art.meta-test"]
    assert entry["source"] == "integration_test"
    assert entry["metadata"]["build_id"] == "build.001"
    assert entry["metadata"]["step"] == 3


# ---------------------------------------------------------------------------
# Test 5: Silent fallback impossible
# ---------------------------------------------------------------------------

def test_silent_fallback_caught_by_export_guard() -> None:
    """Attempt to export without provenance -> caught by guard."""
    registry = ProvenanceRegistry()
    # Register only some artifacts
    registry.register("art.registered-001", ArtifactLineage.canonical)

    # Try to export a mix of registered and unregistered artifacts
    export_list = [
        "art.registered-001",
        "art.unregistered-001",
        "art.unregistered-002",
    ]

    violations = registry.validate_export_guard(export_list)
    assert len(violations) == 2, \
        f"Expected 2 violations for unregistered artifacts, got {len(violations)}"

    violated_ids = {v.artifact_id for v in violations}
    assert "art.unregistered-001" in violated_ids
    assert "art.unregistered-002" in violated_ids
    assert "art.registered-001" not in violated_ids


def test_empty_export_no_violations() -> None:
    """Exporting no artifacts produces no violations."""
    registry = ProvenanceRegistry()
    violations = registry.validate_export_guard([])
    assert len(violations) == 0


def test_all_registered_export_no_violations() -> None:
    """Exporting all registered artifacts produces no violations."""
    registry = ProvenanceRegistry()
    registry.register("art.a", ArtifactLineage.canonical)
    registry.register("art.b", ArtifactLineage.fallback)

    violations = registry.validate_export_guard(["art.a", "art.b"])
    assert len(violations) == 0, \
        "All registered artifacts (even fallback) should pass export guard"


def test_export_guard_catches_single_missing() -> None:
    """Even one unregistered artifact in a large batch triggers a violation."""
    registry = ProvenanceRegistry()
    for i in range(20):
        registry.register(f"art.batch-{i:03d}", ArtifactLineage.canonical)

    export_list = [f"art.batch-{i:03d}" for i in range(20)] + ["art.sneaky-unregistered"]

    violations = registry.validate_export_guard(export_list)
    assert len(violations) == 1
    assert violations[0].artifact_id == "art.sneaky-unregistered"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    test_canonical_artifact_passes_validation()
    test_canonical_artifact_export_guard()
    test_unmarked_fallback_detected()
    test_marked_artifact_no_unmarked_violation()
    test_fallback_in_canonical_position_detected()
    test_unregistered_in_canonical_position()
    test_registry_tracks_mixed_artifacts()
    test_registry_lineage_queries()
    test_registry_is_registered()
    test_registry_metadata_preserved()
    test_silent_fallback_caught_by_export_guard()
    test_empty_export_no_violations()
    test_all_registered_export_no_violations()
    test_export_guard_catches_single_missing()
    print("VRF-003: All 14 fallback canonicality tests passed.")


if __name__ == "__main__":
    main()
