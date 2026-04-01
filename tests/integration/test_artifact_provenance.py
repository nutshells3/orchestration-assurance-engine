"""Integration tests for artifact provenance tracking and fallback guard.

BRG-003: Sidecar provenance refs are emitted for all artifact sources.
BRG-004: Fallback artifacts are never silently promoted to canonical export data.
"""

from __future__ import annotations

import sys
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from integration test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.artifact_provenance import (  # noqa: E402
    ArtifactCanonicity,
    ArtifactProvenanceRef,
    FallbackGuard,
    ProvenanceRegistry,
)


# ---------------------------------------------------------------------------
# ArtifactCanonicity enum
# ---------------------------------------------------------------------------


def test_canonicity_values():
    assert ArtifactCanonicity.canonical.value == "canonical"
    assert ArtifactCanonicity.fallback.value == "fallback"
    assert ArtifactCanonicity.surrogate.value == "surrogate"
    assert ArtifactCanonicity.unknown.value == "unknown"


def test_canonicity_is_str_enum():
    assert isinstance(ArtifactCanonicity.canonical, str)
    assert ArtifactCanonicity("canonical") is ArtifactCanonicity.canonical


# ---------------------------------------------------------------------------
# ArtifactProvenanceRef
# ---------------------------------------------------------------------------


def test_provenance_ref_to_dict():
    ref = ArtifactProvenanceRef(
        artifact_id="art-001",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:abc123",
        timestamp="2026-03-26T00:00:00+00:00",
        notes="primary result",
    )
    d = ref.to_dict()
    assert d["artifact_id"] == "art-001"
    assert d["canonicity"] == "canonical"
    assert d["source_type"] == "proof_session"
    assert d["source_ref"] == "sha256:abc123"
    assert d["timestamp"] == "2026-03-26T00:00:00+00:00"
    assert d["notes"] == "primary result"


def test_provenance_ref_to_dict_no_notes():
    ref = ArtifactProvenanceRef(
        artifact_id="art-002",
        canonicity=ArtifactCanonicity.fallback,
        source_type="run_log",
        source_ref="log-xyz",
    )
    d = ref.to_dict()
    assert "notes" not in d
    assert d["canonicity"] == "fallback"


def test_provenance_ref_auto_timestamp():
    ref = ArtifactProvenanceRef(
        artifact_id="art-003",
        canonicity=ArtifactCanonicity.unknown,
        source_type="transcript",
        source_ref=None,
    )
    assert ref.timestamp is not None
    assert len(ref.timestamp) > 0


def test_provenance_ref_repr():
    ref = ArtifactProvenanceRef(
        artifact_id="art-004",
        canonicity=ArtifactCanonicity.surrogate,
        source_type="best_effort_analysis",
        source_ref="ref-99",
    )
    r = repr(ref)
    assert "art-004" in r
    assert "surrogate" in r
    assert "best_effort_analysis" in r


# ---------------------------------------------------------------------------
# ProvenanceRegistry
# ---------------------------------------------------------------------------


def test_registry_register_and_get():
    registry = ProvenanceRegistry()
    ref = ArtifactProvenanceRef(
        artifact_id="art-010",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:aaa",
    )
    registry.register(ref)
    assert registry.get("art-010") is ref
    assert registry.get("nonexistent") is None


def test_registry_get_fallbacks():
    registry = ProvenanceRegistry()
    canonical_ref = ArtifactProvenanceRef(
        artifact_id="c-1",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:111",
    )
    fallback_ref = ArtifactProvenanceRef(
        artifact_id="f-1",
        canonicity=ArtifactCanonicity.fallback,
        source_type="run_log",
        source_ref="log-001",
    )
    surrogate_ref = ArtifactProvenanceRef(
        artifact_id="s-1",
        canonicity=ArtifactCanonicity.surrogate,
        source_type="best_effort_analysis",
        source_ref="ref-001",
    )
    unknown_ref = ArtifactProvenanceRef(
        artifact_id="u-1",
        canonicity=ArtifactCanonicity.unknown,
        source_type="legacy",
        source_ref=None,
    )
    for ref in [canonical_ref, fallback_ref, surrogate_ref, unknown_ref]:
        registry.register(ref)

    fallbacks = registry.get_fallbacks()
    ids = {r.artifact_id for r in fallbacks}
    assert "c-1" not in ids, "Canonical artifact should not appear in fallbacks"
    assert "f-1" in ids
    assert "s-1" in ids
    assert "u-1" in ids


def test_registry_to_sidecar_section():
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="c-1",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:111",
    ))
    registry.register(ArtifactProvenanceRef(
        artifact_id="f-1",
        canonicity=ArtifactCanonicity.fallback,
        source_type="run_log",
        source_ref="log-001",
    ))

    section = registry.to_sidecar_section()
    assert section["fallback_count"] == 1
    assert section["canonical_count"] == 1
    assert section["has_non_canonical"] is True
    assert len(section["artifact_provenance"]) == 2


def test_registry_sidecar_no_fallbacks():
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="c-1",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:111",
    ))

    section = registry.to_sidecar_section()
    assert section["fallback_count"] == 0
    assert section["has_non_canonical"] is False


# ---------------------------------------------------------------------------
# ProvenanceRegistry.validate_no_silent_fallback
# ---------------------------------------------------------------------------


def test_validate_catches_unknown_canonicity():
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="u-1",
        canonicity=ArtifactCanonicity.unknown,
        source_type="legacy",
        source_ref=None,
    ))
    violations = registry.validate_no_silent_fallback()
    assert len(violations) >= 1
    assert any("unknown" in v for v in violations)
    assert any("u-1" in v for v in violations)


def test_validate_catches_missing_source_ref():
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="f-1",
        canonicity=ArtifactCanonicity.fallback,
        source_type="run_log",
        source_ref=None,  # Missing source_ref for fallback
    ))
    violations = registry.validate_no_silent_fallback()
    assert len(violations) >= 1
    assert any("source_ref" in v for v in violations)


def test_validate_clean_registry():
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="c-1",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:111",
    ))
    registry.register(ArtifactProvenanceRef(
        artifact_id="f-1",
        canonicity=ArtifactCanonicity.fallback,
        source_type="run_log",
        source_ref="log-001",  # Properly documented fallback
    ))
    violations = registry.validate_no_silent_fallback()
    assert violations == [], f"Expected no violations but got: {violations}"


# ---------------------------------------------------------------------------
# FallbackGuard.mark_artifact
# ---------------------------------------------------------------------------


def test_mark_artifact_canonical():
    artifact = {"id": "a1", "data": "value"}
    result = FallbackGuard.mark_artifact(artifact, ArtifactCanonicity.canonical)
    assert result is artifact  # Mutates in place
    assert artifact["_canonicity"] == "canonical"
    assert "_source_type" not in artifact


def test_mark_artifact_fallback_with_source():
    artifact = {"id": "a2"}
    FallbackGuard.mark_artifact(
        artifact,
        ArtifactCanonicity.fallback,
        source_type="run_log",
    )
    assert artifact["_canonicity"] == "fallback"
    assert artifact["_source_type"] == "run_log"


def test_strip_markers():
    artifact = {"id": "a3", "_canonicity": "fallback", "_source_type": "run_log", "data": 42}
    FallbackGuard.strip_markers(artifact)
    assert "_canonicity" not in artifact
    assert "_source_type" not in artifact
    assert artifact["data"] == 42


# ---------------------------------------------------------------------------
# FallbackGuard.check_export_data
# ---------------------------------------------------------------------------


def test_check_export_canonical_artifact_passes():
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="proof-1",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:abc",
    ))
    export_data = {
        "build_results": {
            "artifact_id": "proof-1",
            "_canonicity": "canonical",
            "status": "success",
        },
    }
    violations = FallbackGuard.check_export_data(export_data, registry)
    assert violations == []


def test_check_export_fallback_in_canonical_position_detected():
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="fb-1",
        canonicity=ArtifactCanonicity.fallback,
        source_type="run_log",
        source_ref="log-001",
    ))
    export_data = {
        "build_results": {
            "artifact_id": "fb-1",
            "_canonicity": "fallback",
            "status": "reconstructed",
        },
    }
    violations = FallbackGuard.check_export_data(export_data, registry)
    assert len(violations) >= 1, "Fallback in canonical position should be a violation"
    combined = " ".join(violations)
    assert "fb-1" in combined or "fallback" in combined


def test_check_export_run_log_without_marker_detected():
    registry = ProvenanceRegistry()
    export_data = {
        "audit_output": {
            "id": "audit-1",
            "_source_type": "run_log",
            # No _canonicity marker -- this is the silent fallback
        },
    }
    violations = FallbackGuard.check_export_data(export_data, registry)
    assert len(violations) >= 1
    assert any("run_log" in v for v in violations)


def test_check_export_transcript_without_marker_detected():
    registry = ProvenanceRegistry()
    export_data = {
        "verifier_results": {
            "id": "verify-1",
            "_source_type": "transcript",
            # No _canonicity marker
        },
    }
    violations = FallbackGuard.check_export_data(export_data, registry)
    assert len(violations) >= 1
    assert any("transcript" in v for v in violations)


def test_check_export_registered_fallback_promoted_to_canonical():
    """BRG-004: A fallback artifact promoted to a canonical position without clearance."""
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="log-artifact-42",
        canonicity=ArtifactCanonicity.fallback,
        source_type="run_log",
        source_ref="log-042",
    ))
    export_data = {
        "profile": {
            "artifact_id": "log-artifact-42",
            # No _canonicity marker at all
        },
    }
    violations = FallbackGuard.check_export_data(export_data, registry)
    assert len(violations) >= 1
    assert any("log-artifact-42" in v for v in violations)


def test_check_export_clean_data():
    """No violations when export only contains properly marked canonical artifacts."""
    registry = ProvenanceRegistry()
    registry.register(ArtifactProvenanceRef(
        artifact_id="good-1",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:good",
    ))
    export_data = {
        "build_results": {
            "artifact_id": "good-1",
            "_canonicity": "canonical",
        },
        "verifier_results": {
            "artifact_id": "good-2",
            "_canonicity": "canonical",
        },
        "audit_output": {"summary": "clean"},
        "profile": {"status": "approved"},
    }
    violations = FallbackGuard.check_export_data(export_data, registry)
    assert violations == []


# ---------------------------------------------------------------------------
# FallbackGuard.artifact_content_hash
# ---------------------------------------------------------------------------


def test_content_hash_ignores_markers():
    artifact_a = {"id": "x", "data": 1}
    artifact_b = {"id": "x", "data": 1, "_canonicity": "fallback", "_source_type": "run_log"}
    assert FallbackGuard.artifact_content_hash(artifact_a) == FallbackGuard.artifact_content_hash(artifact_b)


def test_content_hash_changes_with_data():
    artifact_a = {"id": "x", "data": 1}
    artifact_b = {"id": "x", "data": 2}
    assert FallbackGuard.artifact_content_hash(artifact_a) != FallbackGuard.artifact_content_hash(artifact_b)


# ---------------------------------------------------------------------------
# End-to-end: full provenance workflow
# ---------------------------------------------------------------------------


def test_full_provenance_workflow():
    """Simulate a complete export workflow with mixed canonical and fallback artifacts."""
    registry = ProvenanceRegistry()

    # Register a canonical proof result
    registry.register(ArtifactProvenanceRef(
        artifact_id="proof-session-1",
        canonicity=ArtifactCanonicity.canonical,
        source_type="proof_session",
        source_ref="sha256:deadbeef",
        notes="Primary Isabelle session build",
    ))

    # Register a fallback from run.logs
    registry.register(ArtifactProvenanceRef(
        artifact_id="runlog-reconstruction-1",
        canonicity=ArtifactCanonicity.fallback,
        source_type="run_log",
        source_ref="log-20260326-001",
        notes="Reconstructed from run.logs after session timeout",
    ))

    # Build sidecar section
    sidecar = registry.to_sidecar_section()
    assert sidecar["fallback_count"] == 1
    assert sidecar["canonical_count"] == 1
    assert sidecar["has_non_canonical"] is True

    # Validate no silent fallback (should pass -- both are properly documented)
    violations = registry.validate_no_silent_fallback()
    assert violations == []

    # Build export data with canonical result in canonical position
    export = {
        "build_results": FallbackGuard.mark_artifact(
            {"artifact_id": "proof-session-1", "status": "success"},
            ArtifactCanonicity.canonical,
        ),
    }
    assert FallbackGuard.check_export_data(export, registry) == []

    # Attempt to put the fallback in a canonical position -- should fail
    bad_export = {
        "build_results": FallbackGuard.mark_artifact(
            {"artifact_id": "runlog-reconstruction-1", "status": "reconstructed"},
            ArtifactCanonicity.fallback,
            source_type="run_log",
        ),
    }
    bad_violations = FallbackGuard.check_export_data(bad_export, registry)
    assert len(bad_violations) >= 1


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    test_canonicity_values()
    test_canonicity_is_str_enum()
    test_provenance_ref_to_dict()
    test_provenance_ref_to_dict_no_notes()
    test_provenance_ref_auto_timestamp()
    test_provenance_ref_repr()
    test_registry_register_and_get()
    test_registry_get_fallbacks()
    test_registry_to_sidecar_section()
    test_registry_sidecar_no_fallbacks()
    test_validate_catches_unknown_canonicity()
    test_validate_catches_missing_source_ref()
    test_validate_clean_registry()
    test_mark_artifact_canonical()
    test_mark_artifact_fallback_with_source()
    test_strip_markers()
    test_check_export_canonical_artifact_passes()
    test_check_export_fallback_in_canonical_position_detected()
    test_check_export_run_log_without_marker_detected()
    test_check_export_transcript_without_marker_detected()
    test_check_export_registered_fallback_promoted_to_canonical()
    test_check_export_clean_data()
    test_content_hash_ignores_markers()
    test_content_hash_changes_with_data()
    test_full_provenance_workflow()
    print("All artifact_provenance tests passed.")


if __name__ == "__main__":
    main()
