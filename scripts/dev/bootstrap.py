"""Bootstrap root tooling for the formal-claim monorepo."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from common import PNPM_VERSION, ROOT, UV_VERSION, resolve_uv_command


COREPACK = "corepack.cmd" if os.name == "nt" else "corepack"


def run(command):
    print("+", " ".join(command))
    subprocess.run(command, cwd=str(ROOT), check=True)


def ensure_command(name, hint):
    if shutil.which(name) is None:
        raise SystemExit(f"{name} is required for bootstrap. {hint}")


def ensure_uv():
    command = resolve_uv_command()
    if command is not None:
        return command

    run([sys.executable, "-m", "pip", "install", "--user", f"uv=={UV_VERSION}"])
    command = resolve_uv_command()
    if command is None:
        raise SystemExit(
            "uv installation completed but the CLI is still not discoverable. "
            "Set FORMAL_CLAIM_UV to the absolute uv executable path."
        )
    return command


def main():
    ensure_command("python", "Install Python 3.8 or newer.")
    ensure_command(COREPACK, "Install Node.js with Corepack enabled.")

    uv_command = ensure_uv()
    run([COREPACK, "prepare", f"pnpm@{PNPM_VERSION}", "--activate"])
    run(uv_command + ["lock"])
    run(uv_command + ["sync", "--group", "dev"])
    run(uv_command + ["run", "--group", "dev", "pre-commit", "install"])

    pnpm_lock = Path(ROOT) / "pnpm-lock.yaml"
    pnpm_command = [COREPACK, "pnpm", "install"]
    if pnpm_lock.exists():
        pnpm_command.append("--frozen-lockfile")
    run(pnpm_command)


if __name__ == "__main__":
    main()
