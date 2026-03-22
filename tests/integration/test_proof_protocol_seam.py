"""Smoke test for the engine-facing proof protocol seam."""

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
    raise RuntimeError("Could not locate monorepo root from proof protocol seam test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine import PipelineConfig, PipelineOrchestrator  # noqa: E402
from formal_claim_engine.llm_client import LLMClient  # noqa: E402
from formal_claim_engine.proof_protocol import FilesystemProofAdapter  # noqa: E402


class DummyLLM(LLMClient):
    async def complete(self, *args, **kwargs):  # pragma: no cover - defensive guard
        raise AssertionError("Proof protocol seam smoke should not hit a real LLM.")


class FakeIsabelle:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def write_theory(self, session_dir: str, theory_name: str, content: str) -> Path:
        self.calls.append(("write_theory", theory_name))
        path = Path(session_dir)
        path.mkdir(parents=True, exist_ok=True)
        theory_path = path / f"{theory_name}.thy"
        theory_path.write_text(content, encoding="utf-8")
        return theory_path

    def write_root(self, session_dir: str, session_name: str, theories: list[str]) -> Path:
        self.calls.append(("write_root", session_name))
        path = Path(session_dir) / "ROOT"
        path.write_text("\n".join(theories), encoding="utf-8")
        return path

    def build(self, session_name: str, session_dir: str):
        self.calls.append(("build", session_name))
        return type(
            "BuildResult",
            (),
            {
                "success": True,
                "stdout": "ok",
                "stderr": "",
                "sorry_count": 0,
                "oops_count": 0,
                "sorry_locations": [],
                "theorems": ["demo"],
                "definitions": [],
                "locales": [],
                "session_fingerprint": "fp-demo",
            },
        )()


class FakeRunnerCli:
    def run_audit(self, request_path: Path) -> dict:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        return {
            "success": True,
            "session_name": request["session_name"],
            "target_theorem": request["target_theorem"],
            "trust": {"success": True, "surface": {}},
            "probe_results": [
                {"kind": "counterexample", "outcome": "untested"},
                {"kind": "proofSearch", "outcome": "untested"},
            ],
        }


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        orchestrator = PipelineOrchestrator(
            PipelineConfig(data_dir=tmp),
            llm=DummyLLM(),
        )
        fake_isabelle = FakeIsabelle()
        fake_runner = FakeRunnerCli()
        orchestrator.proof_client = FilesystemProofAdapter(
            orchestrator.config,
            builder=fake_isabelle,
            audit_client=fake_runner,
        )

        session_dir = str(Path(tmp) / "session")
        orchestrator.proof_client.prepare_theory_session(
            session_dir=session_dir,
            session_name="DemoSession",
            theory_name="DemoTheory",
            theory_body="theory DemoTheory imports Main begin end",
        )
        build_result = orchestrator.proof_client.build_session(
            session_name="DemoSession",
            session_dir=session_dir,
            target_theory="DemoTheory",
            target_theorem="demo_theorem",
        )
        assert build_result.success is True
        assert fake_isabelle.calls == [
            ("write_theory", "DemoTheory"),
            ("write_root", "DemoSession"),
            ("build", "DemoSession"),
        ], fake_isabelle.calls

        request_path = Path(tmp) / "audit-request.json"
        request_path.write_text(
            json.dumps(
                {
                    "session_name": "DemoSession",
                    "target_theorem": "demo_theorem",
                }
            ),
            encoding="utf-8",
        )
        audit_payload = orchestrator.proof_client.run_audit(request_path)
        assert audit_payload["target_backend"] == "isabelle-local", audit_payload
        assert audit_payload["transport"] == "filesystem_adapter", audit_payload
        assert audit_payload["probe_results"][0]["kind"] == "counterexample", audit_payload


if __name__ == "__main__":
    main()
