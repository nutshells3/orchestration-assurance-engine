"""Release smoke for the FWP proof seam, scenario parity, and release packaging."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def resolve_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "scripts" / "dev" / "check_repo.py").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from release smoke script.")


ROOT = resolve_root()
def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=str(cwd or ROOT), check=True)


def main() -> None:
    run([sys.executable, str(ROOT / "scripts" / "dev" / "check_repo.py"), "--mode", "lint"])
    run([sys.executable, str(ROOT / "scripts" / "dev" / "run_uv.py"), "run", "--directory", "services/engine", "python", str(ROOT / "tests" / "integration" / "test_fwp_proof_integration.py")])
    run([sys.executable, str(ROOT / "scripts" / "release" / "replay_scenarios.py")])
    run([sys.executable, str(ROOT / "scripts" / "dev" / "run_uv.py"), "run", "--directory", "services/mcp-server", "python", str(ROOT / "tests" / "e2e" / "test_scenario_replays.py")])
    run([sys.executable, str(ROOT / "scripts" / "release" / "build_release_artifacts.py")])


if __name__ == "__main__":
    main()
