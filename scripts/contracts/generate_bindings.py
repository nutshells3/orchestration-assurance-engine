#!/usr/bin/env python3
"""Generate canonical Python bindings from the frozen schemas."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "packages" / "contracts" / "schemas"
PYTHON_OUT_DIR = ROOT / "packages" / "contracts-py" / "src" / "formal_claim_contracts"

PYTHON_JOBS = [
    # v1 schemas
    ("claim-graph.schema.json", "claim_graph.py"),
    ("assurance-graph.schema.json", "assurance_graph.py"),
    ("assurance-profile.schema.json", "assurance_profile.py"),
    ("pipeline-trace.schema.json", "pipeline_trace.py"),
    ("pipeline-event.schema.json", "pipeline_event.py"),
    ("trace-sidecar-meta.schema.json", "trace_sidecar_meta.py"),
    ("prefix-slice.schema.json", "prefix_slice.py"),
    ("prefix-slice-graph.schema.json", "prefix_slice_graph.py"),
    # v2 schemas
    ("pipeline-trace-v2.schema.json", "pipeline_trace_v2.py"),
    ("pipeline-event-v2.schema.json", "pipeline_event_v2.py"),
    ("trace-sidecar-meta-v2.schema.json", "trace_sidecar_meta_v2.py"),
    ("prefix-slice-text-v1.schema.json", "prefix_slice_text_v1.py"),
    ("prefix-slice-graph-v1.schema.json", "prefix_slice_graph_v1.py"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--typescript-only", action="store_true")
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
    args = parse_args()
    if args.typescript_only:
        raise SystemExit("TypeScript bindings package (contracts-ts) has been removed.")

    generate_python()


if __name__ == "__main__":
    main()
