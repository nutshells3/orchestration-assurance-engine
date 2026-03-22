#!/usr/bin/env python3
"""
CLI entry point for the Formal Claim Engine.

Usage:
    python -m formal_claim_engine.cli run "Prove that X converges"
    python -m formal_claim_engine.cli run --config config.json "..."
    python -m formal_claim_engine.cli validate claim_graphs cg.dispatch
    python -m formal_claim_engine.cli list claim_graphs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .config import PipelineConfig
from .orchestrator import PipelineOrchestrator
from .store import ArtifactStore
from .unified_config import load_config as _load_unified_config, to_pipeline_config


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(args):
    """Run the full pipeline on user input."""
    config = _load_config(args.config) if args.config else _default_config()
    if args.project:
        config.project_id = args.project

    orch = PipelineOrchestrator(config)
    user_input = " ".join(args.input)

    result = asyncio.run(orch.run(user_input))
    summary = result.summary()
    print(json.dumps(summary, indent=2))

    if args.output:
        with open(args.output, "w") as handle:
            json.dump(summary, handle, indent=2)
        print(f"\nFull result written to {args.output}")


def cmd_validate(args):
    """Validate an artifact against its JSON Schema."""
    config = _load_config(args.config) if args.config else _default_config()
    store = ArtifactStore(config.data_dir)
    errors = store.validate_file(args.kind, args.artifact_id)
    if errors:
        print(f"Validation FAILED ({len(errors)} errors):")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)
    print("Validation OK")


def cmd_list(args):
    """List artifacts of a given kind."""
    config = _load_config(args.config) if args.config else _default_config()
    store = ArtifactStore(config.data_dir)
    items = store._list(args.kind)
    if items:
        for item in sorted(items):
            print(item)
    else:
        print(f"No {args.kind} found in {config.data_dir}")


def cmd_show(args):
    """Show a stored artifact."""
    config = _load_config(args.config) if args.config else _default_config()
    store = ArtifactStore(config.data_dir)
    try:
        data = store.load_payload(args.kind, args.artifact_id)
    except FileNotFoundError:
        path = store._path(args.kind, args.artifact_id)
        print(f"Not found: {path}")
        sys.exit(1)
    print(json.dumps(data, indent=2))
def _load_config(path: str) -> PipelineConfig:
    """Load config from a JSON file (legacy) or verification.toml."""
    if path.endswith(".toml"):
        from pathlib import Path
        uc = _load_unified_config(Path(path))
        return to_pipeline_config(uc)
    with open(path) as handle:
        data = json.load(handle)
    return PipelineConfig(
        project_id=data.get("project_id", "project.default"),
        data_dir=data.get("data_dir", "./pipeline_data"),
    )


def _default_config() -> PipelineConfig:
    """Try verification.toml first, fall back to bare PipelineConfig defaults."""
    try:
        uc = _load_unified_config()
        return to_pipeline_config(uc)
    except FileNotFoundError:
        return PipelineConfig()


def main():
    parser = argparse.ArgumentParser(
        prog="formal-claim-engine",
        description="Formal Claim Engine CLI",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", help="Path to config JSON file")
    parser.add_argument("--project", help="Override project_id")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run pipeline on user input")
    p_run.add_argument("input", nargs="+", help="Free-form user input")
    p_run.add_argument("-o", "--output", help="Write result JSON to file")
    p_run.set_defaults(func=cmd_run)

    p_val = sub.add_parser("validate", help="Validate a stored artifact")
    p_val.add_argument(
        "kind", choices=["claim_graphs", "assurance_graphs", "assurance_profiles"]
    )
    p_val.add_argument("artifact_id")
    p_val.set_defaults(func=cmd_validate)

    p_list = sub.add_parser("list", help="List stored artifacts")
    p_list.add_argument(
        "kind", choices=["claim_graphs", "assurance_graphs", "assurance_profiles"]
    )
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show a stored artifact")
    p_show.add_argument(
        "kind", choices=["claim_graphs", "assurance_graphs", "assurance_profiles"]
    )
    p_show.add_argument("artifact_id")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
