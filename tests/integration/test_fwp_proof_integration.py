"""FWP-backed proof integration smoke for formal-claim."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from FWP proof integration test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine import PipelineConfig  # noqa: E402
from formal_claim_engine.proof_control import ProofControlPlane  # noqa: E402
from formal_claim_engine.proof_protocol import FwpProofAdapter  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config = PipelineConfig(data_dir=tmp)
        adapter = FwpProofAdapter(config)
        session_dir = str(Path(tmp) / "theories" / "claim.demo" / "A")
        theory_body = (
            "theory DemoTheory imports Main\n"
            "begin\n\n"
            'lemma demo: "True" by simp\n\n'
            "end\n"
        )
        adapter.prepare_theory_session(
            session_dir=session_dir,
            session_name="DemoSession",
            theory_name="DemoTheory",
            theory_body=theory_body,
        )
        build = adapter.build_session(
            session_name="DemoSession",
            session_dir=session_dir,
            target_theory="DemoTheory",
            target_theorem="demo",
            subject_id="claim.demo",
        )
        assert build.success is True, build
        assert build.session_fingerprint, build

        request_path = Path(tmp) / "audit-request.json"
        request_path.write_text(
            json.dumps(
                {
                    "subject_id": "claim.demo",
                    "session_name": "DemoSession",
                    "session_dir": session_dir,
                    "target_theory": "DemoTheory",
                    "target_theorem": "demo",
                    "target_backend": "isabelle-local",
                    "export_requirements": ["contractPack"],
                    "trust_frontier_requirements": ["trustFrontier"],
                    "probe_requirements": ["dependencySlice"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        audit = adapter.run_audit(request_path)
        assert audit["success"] is True, audit
        assert audit["transport"] == "local_hub", audit

        control = ProofControlPlane(data_dir=tmp)
        search = control.start_job(
            session_name="SearchSession",
            session_dir=session_dir,
            run_kind="search",
            target_theory="DemoTheory",
            target_theorem="stubborn",
            label="smoke",
        )
        assert search["status"] in {"running", "cancel_requested"}, search
        pending = control.cancel_job(search["job_id"])
        assert pending["status"] in {"cancel_requested", "cancelled"}, pending
        killed = control.kill_job(search["job_id"])
        assert killed["status"] == "killed", killed


if __name__ == "__main__":
    main()
