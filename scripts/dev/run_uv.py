"""Resolve and delegate to uv without assuming PATH or venv module state."""

from __future__ import annotations

import subprocess
import sys

from common import ROOT, require_uv_command


def main() -> None:
    command = require_uv_command() + sys.argv[1:]
    raise SystemExit(subprocess.run(command, cwd=str(ROOT)).returncode)


if __name__ == "__main__":
    main()
