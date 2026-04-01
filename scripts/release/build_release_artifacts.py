"""Build release artifacts for internal dogfooding."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def resolve_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "scripts" / "dev" / "check_repo.py").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from release packaging script.")


ROOT = resolve_root()
DEV_SCRIPTS = ROOT / "scripts" / "dev"
if str(DEV_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(DEV_SCRIPTS))

from common import require_uv_command  # noqa: E402


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=str(cwd or ROOT), env=env, check=True)


def sha256_for(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_artifact(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / source.name
    shutil.copy2(source, target)
    return target


def build_wheel(package_dir: str, out_dir: Path) -> Path:
    run(
        require_uv_command()
        + ["build", package_dir, "--wheel", "--out-dir", str(out_dir), "--clear"]
    )
    wheels = sorted(out_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one wheel in {out_dir}, found {len(wheels)}.")
    return wheels[0]


def artifact_record(kind: str, path: Path) -> dict[str, object]:
    return {
        "kind": kind,
        "path": str(path.relative_to(ROOT)),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_for(path),
    }


def main() -> None:
    out_root = ROOT / ".tmp" / "dist" / "release"
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict[str, object]] = []
    artifacts.append(
        artifact_record("cli_wheel", build_wheel("apps/cli", out_root / "cli"))
    )
    artifacts.append(
        artifact_record("engine_wheel", build_wheel("services/engine", out_root / "engine"))
    )
    artifacts.append(
        artifact_record(
            "mcp_server_wheel",
            build_wheel("services/mcp-server", out_root / "mcp-server"),
        )
    )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(),
        "python": sys.version,
        "output_root": str(out_root.relative_to(ROOT)),
        "artifacts": artifacts,
        "commands": [
            "python scripts/dev/check_repo.py --mode test",
            "python scripts/release/build_release_artifacts.py",
            "python scripts/release/smoke_release.py",
        ],
        "known_limitations": [
            "release artifacts do not bundle the external proof-assistant server",
            "proof job control remains a thin FWP/proof-assistant pass-through",
            "managed proof jobs are control-plane jobs only",
        ],
    }
    manifest_path = out_root / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
