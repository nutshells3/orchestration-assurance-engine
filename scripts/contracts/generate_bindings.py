#!/usr/bin/env python3
"""Generate canonical Python and TypeScript bindings from the frozen schemas."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "packages" / "contracts" / "schemas"
PYTHON_OUT_DIR = ROOT / "packages" / "contracts-py" / "src" / "formal_claim_contracts"
PYTHON_JOBS = [
    ("claim-graph.schema.json", "claim_graph.py"),
    ("assurance-graph.schema.json", "assurance_graph.py"),
    ("assurance-profile.schema.json", "assurance_profile.py"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-only", action="store_true")
    return parser.parse_args()


def run_command(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=str(cwd), check=True)


def generate_python() -> None:
    PYTHON_OUT_DIR.mkdir(parents=True, exist_ok=True)
    for schema_name, output_name in PYTHON_JOBS:
        schema_path = SCHEMA_DIR / schema_name
        output_path = PYTHON_OUT_DIR / output_name
        run_command(
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(schema_path),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(output_path),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.11",
                "--use-standard-collections",
                "--field-constraints",
                "--disable-timestamp",
            ],
            ROOT,
        )


def main() -> None:
    parse_args()
    generate_python()


if __name__ == "__main__":
    main()
