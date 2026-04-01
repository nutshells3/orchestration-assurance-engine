"""Tests for BRG-001 / BRG-002: FWP proof lineage and audit provenance.

Verifies:
- ProofLineageCollector extracts workspace/session/artifact refs from
  BuildSessionResult and dict payloads.
- Raw payloads are NOT included (only references / hashes).
- Canonical marker distinguishes primary results from fallbacks.
- Contract-pack provenance is reference-only.
- ProofAuditProvenance captures request/response refs without raw payloads.
- Enrichment helpers produce the expected trace/sidecar split.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure engine src is importable
REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"
if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.proof_lineage import (
    ProofAuditProvenance,
    ProofLineageCollector,
    build_proof_audit_provenance,
    enrich_build_results_with_lineage,
    enrich_verifier_results_with_lineage,
    split_lineage_for_export,
)
from formal_claim_engine.proof_protocol import BuildSessionResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_build_session_result(**overrides) -> BuildSessionResult:
    defaults = dict(
        success=True,
        stdout="Build succeeded",
        stderr="",
        return_code=0,
        sorry_count=0,
        oops_count=0,
        sorry_locations=[],
        theorems=["demo_theorem"],
        definitions=["demo_def"],
        locales=[],
        session_fingerprint="fp-abc123",
        timeout_classification="completed",
        duration_seconds=4.2,
        command=["fwp-client", "local_hub", "isabelle", "Module_A", "demo_theorem"],
        workspace_dir="workspace://fwp/abc123",
        session_dir="/tmp/theories/claim-1/A",
        root_path=None,
        stdout_path=None,
        stderr_path=None,
        artifact_paths={
            "art-001": "/tmp/theories/claim-1/A/Module_A.thy",
            "art-002": "/tmp/theories/claim-1/A/build.log",
        },
    )
    defaults.update(overrides)
    return BuildSessionResult(**defaults)


def _make_build_result_dict(**overrides) -> dict:
    defaults = dict(
        success=True,
        stdout="Build succeeded",
        stderr="",
        sorry_count=0,
        oops_count=0,
        sorry_locations=[],
        theorems=["demo_theorem"],
        definitions=["demo_def"],
        locales=[],
        session_fingerprint="fp-abc123",
    )
    defaults.update(overrides)
    return defaults


def _make_verifier_result(**overrides) -> dict:
    defaults = dict(
        proof_status="proof_complete",
        sorry_count=0,
        oops_count=0,
        verification_notes="All obligations discharged.",
    )
    defaults.update(overrides)
    return defaults


def _make_audit_response(**overrides) -> dict:
    defaults = dict(
        success=True,
        session_name="DemoSession",
        target_theorem="demo_theorem",
        workspace_ref="workspace://fwp/audit-xyz",
        contract_pack_ref="cp://fwp/audit-xyz/pack",
        artifact_paths={"art-audit-01": "/tmp/audit/out.thy"},
        trust={"success": True, "surface": {}},
        probe_results=[],
        sorry_count=0,
        session_fingerprint="fp-audit-xyz",
    )
    defaults.update(overrides)
    return defaults


# ===========================================================================
# BRG-001 — ProofLineageCollector
# ===========================================================================


class TestProofLineageCollector:
    """Tests for ProofLineageCollector."""

    def test_extract_lineage_from_build_session_result(self):
        br = _make_build_session_result()
        collector = ProofLineageCollector()
        lineage = collector.extract_lineage(br)

        assert lineage["workspace_id"] == "workspace://fwp/abc123"
        assert lineage["session_id"] == "/tmp/theories/claim-1/A"
        assert lineage["backend_type"] == "fwp"
        assert lineage["session_fingerprint"] == "fp-abc123"
        assert lineage["duration_seconds"] == 4.2
        assert lineage["return_code"] == 0
        assert lineage["canonical"] is True

        # Artifact refs must be reference-only
        assert len(lineage["artifact_refs"]) == 2
        for ref in lineage["artifact_refs"]:
            assert "artifact_id" in ref
            assert "path" in ref
            assert "kind" in ref
            assert "sha256" in ref
            # Must NOT contain raw content
            assert "content" not in ref
            assert "payload" not in ref
            assert "body" not in ref

    def test_extract_lineage_from_dict(self):
        data = {
            "success": True,
            "workspace_dir": "ws-from-dict",
            "session_dir": "/tmp/session-d",
            "session_fingerprint": "fp-dict",
            "duration_seconds": 1.5,
            "return_code": 0,
            "artifact_paths": {"a1": "/tmp/a1.thy"},
            "command": ["fwp-client", "http"],
        }
        collector = ProofLineageCollector()
        lineage = collector.extract_lineage(data)

        assert lineage["workspace_id"] == "ws-from-dict"
        assert lineage["session_id"] == "/tmp/session-d"
        assert lineage["backend_type"] == "fwp"
        assert lineage["canonical"] is True
        assert len(lineage["artifact_refs"]) == 1

    def test_extract_lineage_from_none(self):
        collector = ProofLineageCollector()
        lineage = collector.extract_lineage(None)

        assert lineage["workspace_id"] is None
        assert lineage["session_id"] is None
        assert lineage["backend_type"] is None
        assert lineage["artifact_refs"] == []
        assert lineage["canonical"] is False

    def test_canonical_is_false_for_failed_build(self):
        br = _make_build_session_result(success=False, return_code=1)
        collector = ProofLineageCollector()
        lineage = collector.extract_lineage(br)

        assert lineage["canonical"] is False

    def test_artifact_refs_classify_kinds(self):
        br = _make_build_session_result(
            artifact_paths={
                "thy-1": "/tmp/Module.thy",
                "lean-1": "/tmp/Module.lean",
                "coq-1": "/tmp/Module.v",
                "log-1": "/tmp/build_log.txt",
                "other-1": "/tmp/misc.dat",
            }
        )
        collector = ProofLineageCollector()
        lineage = collector.extract_lineage(br)
        kinds = {ref["artifact_id"]: ref["kind"] for ref in lineage["artifact_refs"]}

        assert kinds["thy-1"] == "theory"
        assert kinds["lean-1"] == "lean_source"
        assert kinds["coq-1"] == "coq_source"
        assert kinds["log-1"] == "build_log"
        assert kinds["other-1"] == "unknown"

    def test_no_raw_stdout_stderr_in_lineage(self):
        """Lineage must never include raw stdout/stderr content."""
        br = _make_build_session_result(
            stdout="VERY LONG BUILD OUTPUT " * 100,
            stderr="WARNING: something" * 50,
        )
        collector = ProofLineageCollector()
        lineage = collector.extract_lineage(br)

        lineage_str = str(lineage)
        assert "VERY LONG BUILD OUTPUT" not in lineage_str
        assert "WARNING: something" not in lineage_str

    def test_extract_verifier_lineage(self):
        v_result = _make_verifier_result()
        collector = ProofLineageCollector()
        v_lineage = collector.extract_verifier_lineage(v_result)

        assert v_lineage["verifier_ref"] is not None
        assert len(v_lineage["verifier_ref"]) == 64  # SHA-256 hex
        assert v_lineage["proof_status"] == "proof_complete"
        assert v_lineage["sorry_count"] == 0
        assert v_lineage["canonical"] is True

        # Must NOT contain raw verification notes
        assert "verification_notes" not in v_lineage
        assert "All obligations" not in str(v_lineage)

    def test_extract_verifier_lineage_non_canonical(self):
        v_result = _make_verifier_result(proof_status="failed")
        collector = ProofLineageCollector()
        v_lineage = collector.extract_verifier_lineage(v_result)

        assert v_lineage["canonical"] is False

    def test_extract_verifier_lineage_none(self):
        collector = ProofLineageCollector()
        v_lineage = collector.extract_verifier_lineage(None)

        assert v_lineage["verifier_ref"] is None
        assert v_lineage["canonical"] is False

    def test_extract_contract_pack_provenance(self):
        audit = _make_audit_response()
        collector = ProofLineageCollector()
        cp = collector.extract_contract_pack_provenance(audit)

        assert cp["contract_pack_ref"] == "cp://fwp/audit-xyz/pack"
        assert cp["contract_pack_hash"] is not None
        assert len(cp["contract_pack_hash"]) == 64
        assert cp["session_ref"] == "workspace://fwp/audit-xyz"
        assert cp["canonical"] is True

        # Must NOT contain raw trust surface, probe results, etc.
        cp_str = str(cp)
        assert "probe_results" not in cp_str
        assert "trust" not in cp_str

    def test_extract_contract_pack_provenance_none(self):
        collector = ProofLineageCollector()
        cp = collector.extract_contract_pack_provenance(None)

        assert cp["contract_pack_ref"] is None
        assert cp["contract_pack_hash"] is None
        assert cp["canonical"] is False


# ===========================================================================
# BRG-002 — ProofAuditProvenance
# ===========================================================================


class TestProofAuditProvenance:
    """Tests for ProofAuditProvenance."""

    def test_capture_request_ref(self):
        request = {
            "session_name": "DemoSession",
            "session_dir": "/tmp/session",
            "target_theory": "Module_A",
            "target_theorem": "demo_theorem",
            "claim_id": "claim-001",
        }
        prov = ProofAuditProvenance()
        ref = prov.capture_request_ref(request)

        assert ref["request_hash"] is not None
        assert len(ref["request_hash"]) == 64  # SHA-256 hex
        assert ref["theory_session_id"] == "DemoSession"
        assert ref["claim_id"] == "claim-001"
        assert ref["timestamp"] is not None

        # Must NOT contain the raw request payload
        ref_str = str(ref)
        assert "target_theory" not in ref_str
        assert "Module_A" not in ref_str

    def test_capture_request_ref_none(self):
        prov = ProofAuditProvenance()
        ref = prov.capture_request_ref(None)

        assert ref["request_hash"] is None
        assert ref["theory_session_id"] is None
        assert ref["claim_id"] is None

    def test_capture_response_ref(self):
        response = _make_audit_response()
        prov = ProofAuditProvenance()
        ref = prov.capture_response_ref(response)

        assert ref["response_hash"] is not None
        assert len(ref["response_hash"]) == 64
        assert ref["success"] is True
        assert ref["sorry_count"] == 0
        assert ref["artifact_count"] == 1  # one artifact_path in fixture
        assert ref["timestamp"] is not None

        # Must NOT contain raw probe results, signals, etc.
        ref_str = str(ref)
        assert "probe_results" not in ref_str
        assert "workspace_ref" not in ref_str

    def test_capture_response_ref_none(self):
        prov = ProofAuditProvenance()
        ref = prov.capture_response_ref(None)

        assert ref["response_hash"] is None
        assert ref["success"] is None
        assert ref["artifact_count"] is None


# ===========================================================================
# Enrichment helpers
# ===========================================================================


class TestEnrichmentHelpers:
    """Tests for the trace-export integration helpers."""

    def test_enrich_build_results_with_lineage(self):
        build_results = {
            "A": _make_build_result_dict(),
            "B": _make_build_result_dict(success=False),
        }
        raw_session_results = {
            "A": _make_build_session_result(),
            "B": _make_build_session_result(success=False, return_code=1),
        }
        enriched = enrich_build_results_with_lineage(
            build_results, raw_session_results
        )

        assert "proof_lineage" in enriched["A"]
        assert enriched["A"]["proof_lineage"]["canonical"] is True
        assert enriched["A"]["proof_lineage"]["workspace_id"] == "workspace://fwp/abc123"

        assert "proof_lineage" in enriched["B"]
        assert enriched["B"]["proof_lineage"]["canonical"] is False

    def test_enrich_verifier_results_with_lineage(self):
        verifier_results = {
            "A": _make_verifier_result(),
            "B": _make_verifier_result(proof_status="sorry_present"),
        }
        enriched = enrich_verifier_results_with_lineage(verifier_results)

        assert "verifier_lineage" in enriched["A"]
        assert enriched["A"]["verifier_lineage"]["canonical"] is True

        assert "verifier_lineage" in enriched["B"]
        assert enriched["B"]["verifier_lineage"]["canonical"] is False

    def test_build_proof_audit_provenance(self):
        request = {"session_name": "S", "claim_id": "c1"}
        response = _make_audit_response()
        prov = build_proof_audit_provenance(request, response)

        assert "request_ref" in prov
        assert "response_ref" in prov
        assert prov["request_ref"]["claim_id"] == "c1"
        assert prov["response_ref"]["success"] is True

    def test_split_lineage_for_export(self):
        build_results = {
            "A": {
                **_make_build_result_dict(),
                "proof_lineage": ProofLineageCollector().extract_lineage(
                    _make_build_session_result()
                ),
            },
        }
        verifier_results = {
            "A": {
                **_make_verifier_result(),
                "verifier_lineage": ProofLineageCollector().extract_verifier_lineage(
                    _make_verifier_result()
                ),
            },
        }
        proof_audit = _make_audit_response()

        trace, sidecar = split_lineage_for_export(
            build_results, verifier_results, proof_audit
        )

        # trace.json: only IDs and fingerprint, no artifact_refs
        assert "A" in trace["build_lineage"]
        tl = trace["build_lineage"]["A"]
        assert "workspace_id" in tl
        assert "session_fingerprint" in tl
        assert "canonical" in tl
        assert "artifact_refs" not in tl

        # sidecar: extended lineage including artifact_refs
        assert "A" in sidecar["build_lineage_extended"]
        sl = sidecar["build_lineage_extended"]["A"]
        assert "artifact_refs" in sl

        # contract pack provenance is reference-only
        assert sidecar["contract_pack_provenance"] is not None
        assert sidecar["contract_pack_provenance"]["contract_pack_hash"] is not None

        # verifier lineage in trace
        assert "A" in trace["verifier_lineage"]
        assert trace["verifier_lineage"]["A"]["canonical"] is True

    def test_split_lineage_no_proof_audit(self):
        build_results = {"A": _make_build_result_dict()}
        verifier_results = {"A": _make_verifier_result()}

        trace, sidecar = split_lineage_for_export(
            build_results, verifier_results, None
        )

        assert sidecar["contract_pack_provenance"] is None
        assert "A" in trace["build_lineage"]


# ===========================================================================
# Raw payload exclusion
# ===========================================================================


class TestRawPayloadExclusion:
    """Verify that raw payloads are NEVER included in lineage / provenance."""

    def test_build_lineage_excludes_stdout_stderr(self):
        br = _make_build_session_result(
            stdout="SENSITIVE OUTPUT DATA " * 200,
            stderr="ERROR DETAILS " * 100,
        )
        collector = ProofLineageCollector()
        lineage = collector.extract_lineage(br)
        lineage_text = str(lineage)

        assert "SENSITIVE OUTPUT DATA" not in lineage_text
        assert "ERROR DETAILS" not in lineage_text

    def test_verifier_lineage_excludes_raw_notes(self):
        v = _make_verifier_result(
            verification_notes="DETAILED INTERNAL ANALYSIS " * 50,
        )
        collector = ProofLineageCollector()
        vl = collector.extract_verifier_lineage(v)
        vl_text = str(vl)

        assert "DETAILED INTERNAL ANALYSIS" not in vl_text

    def test_contract_pack_provenance_excludes_signals(self):
        audit = _make_audit_response(
            signals=[{"kind": "secret", "data": "CONFIDENTIAL"}],
        )
        collector = ProofLineageCollector()
        cp = collector.extract_contract_pack_provenance(audit)
        cp_text = str(cp)

        assert "CONFIDENTIAL" not in cp_text
        assert "signals" not in cp_text

    def test_request_ref_excludes_proof_source(self):
        request = {
            "session_name": "S",
            "proof_source": "theory Main begin PRIVATE_LEMMA sorry end",
            "claim_id": "c1",
        }
        prov = ProofAuditProvenance()
        ref = prov.capture_request_ref(request)
        ref_text = str(ref)

        # The request_hash covers the full request, but the ref itself
        # must not contain the raw proof source
        assert "PRIVATE_LEMMA" not in ref_text

    def test_response_ref_excludes_build_output(self):
        response = {
            "success": True,
            "stdout": "RAW BUILD LOG " * 100,
            "stderr": "RAW STDERR " * 100,
            "sorry_count": 0,
            "artifact_paths": {},
            "session_fingerprint": "fp-x",
        }
        prov = ProofAuditProvenance()
        ref = prov.capture_response_ref(response)
        ref_text = str(ref)

        assert "RAW BUILD LOG" not in ref_text
        assert "RAW STDERR" not in ref_text


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> None:
    """Run all tests manually without pytest."""
    import traceback

    test_classes = [
        TestProofLineageCollector,
        TestProofAuditProvenance,
        TestEnrichmentHelpers,
        TestRawPayloadExclusion,
    ]
    passed = 0
    failed = 0
    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if not method_name.startswith("test_"):
                continue
            method = getattr(instance, method_name)
            try:
                method()
                passed += 1
                print(f"  PASS  {cls.__name__}.{method_name}")
            except Exception:
                failed += 1
                print(f"  FAIL  {cls.__name__}.{method_name}")
                traceback.print_exc()
    total = passed + failed
    print(f"\n{passed}/{total} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
