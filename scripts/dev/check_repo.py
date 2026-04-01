"""Repository checks for the monorepo bootstrap phase."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path

from common import (
    BACKLOG_CSV,
    BACKLOG_GENERATOR,
    EXPECTED_FILES,
    EXPECTED_SUBDIRS,
    EXPECTED_TOP_LEVEL_DIRS,
    GENERATED_DIR,
    MANAGED_TEXT_FILES,
    MANIFEST_PATH,
    PNPM_VERSION,
    ROOT,
    require_cargo_command,
    require_uv_command,
)

COREPACK = "corepack.cmd" if sys.platform == "win32" else "corepack"
SKIP_PYTHON_PARTS = {".venv", ".tmp", "__pycache__", "node_modules"}
SKIP_CONFIG_PARTS = {".venv", ".tmp", "__pycache__", "node_modules", "target", "dist"}
AGENT_CONTRACT = ROOT / "docs" / "product" / "agent-runtime-contract.md"
AGENT_SETTINGS = ROOT / "settings" / "agent-control.json"
AGENT_SETTINGS_REL = "settings/agent-control.json"
AGENT_SETTINGS_SYNC = ROOT / "scripts" / "dev" / "sync_agent_settings.py"
REQUIRED_AGENT_CONTRACT_TOKENS = [
    "services/engine",
    "services/mcp-server",
    "apps/cli",
    "packages/evidence-connectors",
    "FWP",
    "proof-assistant",
    "python scripts/dev/check_repo.py --mode test",
    "python scripts/dev/sync_agent_settings.py",
    "python scripts/release/build_release_artifacts.py",
    "python scripts/release/smoke_release.py",
    "project.create",
    "document.ingest",
    "claim.structure",
    "formalize.dual",
    "audit.run",
    "profile.recompute",
    "promotion.transition",
    "trace.export",
    "prefix.extract",
    "bundle.export",
    "proof.run.start",
    "job.get",
    "job.cancel",
    "job.kill",
]
PYTHON_SOURCE_ROOTS = [
    ROOT / "scripts",
    ROOT / "tests",
    ROOT / "services",
    ROOT / "packages",
]


def load_generator_module():
    spec = importlib.util.spec_from_file_location(
        "generate_backlog_scripts", str(BACKLOG_GENERATOR)
    )
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError("Unable to load backlog generator module.")
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ensure_paths_exist():
    missing = []

    for rel_path in EXPECTED_TOP_LEVEL_DIRS + EXPECTED_SUBDIRS:
        if not (ROOT / rel_path).exists():
            missing.append(rel_path)

    for rel_path in EXPECTED_FILES:
        if not (ROOT / rel_path).exists():
            missing.append(rel_path)

    if missing:
        raise RuntimeError("Missing expected repo paths:\n- " + "\n- ".join(missing))


def validate_package_json():
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package_json.get("scripts", {})
    expected_scripts = {"bootstrap", "test", "lint"}
    missing_scripts = sorted(expected_scripts.difference(scripts))
    if missing_scripts:
        raise RuntimeError(
            "package.json is missing scripts: " + ", ".join(missing_scripts)
        )

    package_manager = package_json.get("packageManager")
    expected_package_manager = f"pnpm@{PNPM_VERSION}"
    if package_manager != expected_package_manager:
        raise RuntimeError(
            f"package.json packageManager must be {expected_package_manager}, "
            f"found {package_manager!r}"
        )


def validate_backlog():
    generator = load_generator_module()
    items = generator.load_backlog(BACKLOG_CSV)
    generator.validate_backlog(items)
    if len(items) == 0:
        raise RuntimeError("Backlog CSV must contain at least one item.")
    return items


def iter_python_sources():
    for source_root in PYTHON_SOURCE_ROOTS:
        if not source_root.exists():
            continue
        for path in sorted(source_root.rglob("*.py")):
            if SKIP_PYTHON_PARTS.intersection(path.parts):
                continue
            yield path


def compile_python_sources():
    failures = []
    for path in iter_python_sources():
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc.msg}")

    if failures:
        raise RuntimeError("Python compilation failed:\n- " + "\n- ".join(failures))


def lint_text_files():
    failures = []
    for rel_path in MANAGED_TEXT_FILES:
        path = ROOT / rel_path
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        for line_number, line in enumerate(lines, start=1):
            if line.rstrip(" \t") != line:
                failures.append(f"{rel_path}:{line_number}: trailing whitespace")

        if text and not text.endswith("\n"):
            failures.append(f"{rel_path}: missing trailing newline")

    if failures:
        raise RuntimeError("Text lint failed:\n- " + "\n- ".join(failures))


def ensure_no_cursor_config():
    violations: list[str] = []
    for path in ROOT.rglob(".cursor"):
        if SKIP_CONFIG_PARTS.intersection(path.parts):
            continue
        violations.append(str(path.relative_to(ROOT)))
    if violations:
        raise RuntimeError(
            "Cursor config must not remain in the repo:\n- " + "\n- ".join(sorted(violations))
        )


def validate_agent_runtime_files():
    contract_text = AGENT_CONTRACT.read_text(encoding="utf-8")
    missing_contract_tokens = [
        token for token in REQUIRED_AGENT_CONTRACT_TOKENS if token not in contract_text
    ]
    if missing_contract_tokens:
        raise RuntimeError(
            "Agent runtime contract is missing required tokens:\n- "
            + "\n- ".join(missing_contract_tokens)
        )

    if not AGENT_SETTINGS.exists():
        raise RuntimeError("settings/agent-control.json must exist as the shared agent shell source.")

    codex_config_path = ROOT / ".codex" / "config.toml"
    codex_text = codex_config_path.read_text(encoding="utf-8")
    if "docs/product/agent-runtime-contract.md" not in codex_text:
        raise RuntimeError(".codex/config.toml must reference the shared agent runtime contract.")
    if AGENT_SETTINGS_REL not in codex_text:
        raise RuntimeError(".codex/config.toml must reference settings/agent-control.json.")
    if '[mcp_servers."formal-claim"]' not in codex_text:
        raise RuntimeError(".codex/config.toml must define the formal-claim MCP server.")

    for rel_path in ["AGENTS.md", "CLAUDE.md"]:
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        if "docs/product/agent-runtime-contract.md" not in text:
            raise RuntimeError(f"{rel_path} must reference the shared agent runtime contract.")
        if AGENT_SETTINGS_REL not in text:
            raise RuntimeError(f"{rel_path} must reference settings/agent-control.json.")

    claude_settings = json.loads(
        (ROOT / ".claude" / "settings.local.json").read_text(encoding="utf-8")
    )
    if "permissions" not in claude_settings:
        raise RuntimeError(".claude/settings.local.json must define local command permissions.")


def sync_agent_runtime_files():
    subprocess.run(
        [sys.executable, str(AGENT_SETTINGS_SYNC)],
        cwd=str(ROOT),
        check=True,
    )


def regenerate_backlog_artifacts():
    subprocess.run(
        [sys.executable, str(BACKLOG_GENERATOR)],
        cwd=str(ROOT),
        check=True,
    )


def run_uv(args: list[str], env: dict[str, str] | None = None):
    subprocess.run(
        require_uv_command() + args,
        cwd=str(ROOT),
        check=True,
        env=env,
    )


def regenerate_contract_artifacts():
    run_uv(
        [
            "run",
            "--python",
            "3.12",
            "--group",
            "dev",
            "python",
            "scripts/contracts/generate_bindings.py",
        ]
    )


def run_uv_python(package_dir: str, code: str, env: dict[str, str] | None = None):
    run_uv(
        [
            "run",
            "--directory",
            package_dir,
            "python",
            "-c",
            code,
        ],
        env=env,
    )


def run_schema_tests():
    run_uv(
        [
            "run",
            "--python",
            "3.12",
            "--group",
            "dev",
            "python",
            "tests/schema/test_schema_conformance.py",
        ]
    )


def run_graph_model_tests():
    run_uv(
        [
            "run",
            "--directory",
            "packages/graph-model",
            "python",
            str(ROOT / "tests" / "audit" / "test_graph_queries.py"),
        ]
    )


def run_audit_rules_tests():
    run_uv(
        [
            "run",
            "--directory",
            "packages/audit-rules",
            "python",
            str(ROOT / "tests" / "audit" / "test_audit_rules.py"),
        ]
    )


def run_fwp_proof_integration_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_fwp_proof_integration.py"),
        ]
    )


def build_python_package(package_dir: str, out_dir: str):
    run_uv(
        [
            "build",
            package_dir,
            "--wheel",
            "--out-dir",
            out_dir,
            "--clear",
        ]
    )


def build_python_contracts():
    build_python_package("packages/contracts-py", ".tmp/dist/contracts-py")


def build_core_packages():
    build_python_package("packages/graph-model", ".tmp/dist/graph-model")
    build_python_package("packages/audit-rules", ".tmp/dist/audit-rules")
    build_python_package("packages/evidence-connectors", ".tmp/dist/evidence-connectors")


def run_cargo(args: list[str]):
    subprocess.run(
        require_cargo_command() + args,
        cwd=str(ROOT),
        check=True,
    )


def run_engine_smoke():
    code = """
import tempfile
from formal_claim_engine import ClaimTraceService, PipelineConfig

with tempfile.TemporaryDirectory() as tmp:
    service = ClaimTraceService(config=PipelineConfig(), data_dir=tmp)
    project = service.create_project("root-check", "general", "engine smoke")
    result = service.add_claim(project.id, "Axiom", "A", "axiom")
    summary = service.get_summary(project.id)
    assert summary["total_claims"] == 1, summary
    assert result["claim_id"].startswith("c."), result
    project_record, _ = service.repository.load(project.id)
    assert project_record is not None
    errors = service.repository.artifact_store.validate_file(
        "claim_graphs",
        str(project_record.claim_graph_id),
    )
    assert errors == [], errors
"""
    run_uv_python("services/engine", code)


def run_mcp_server_smoke():
    code = """
import json
from formal_claim_mcp_server import server

created = json.loads(server.create_project("root-check", "general", "mcp smoke"))
project_id = created["project_id"]
json.loads(server.add_claim(project_id, "Premise", "P", "premise"))
graph = json.loads(server.get_graph(project_id))
summary = json.loads(server.get_summary(project_id))
assert graph["total_claims"] == 1, graph
assert summary["total_claims"] == 1, summary
"""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["TRACER_DATA_DIR"] = tmp
        run_uv_python("services/mcp-server", code, env=env)


def run_mcp_server_surface_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/mcp-server",
            "python",
            str(ROOT / "tests" / "integration" / "test_mcp_server_surface.py"),
        ]
    )


def run_mcp_server_parity_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/mcp-server",
            "python",
            str(ROOT / "tests" / "integration" / "test_mcp_server_parity.py"),
        ]
    )


def run_artifact_store_journal_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_artifact_store_journal.py"),
        ]
    )


def run_claim_trace_repository_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_claim_trace_repository_sqlite.py"),
        ]
    )


def run_claim_structuring_workflow_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_claim_structuring_workflow.py"),
        ]
    )


def run_document_ingest_adapter_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_document_ingest_adapter.py"),
        ]
    )


def run_dual_formalization_workflow_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_dual_formalization_workflow.py"),
        ]
    )


def run_proof_protocol_seam_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_proof_protocol_seam.py"),
        ]
    )


def run_prompt_provider_modules_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_prompt_provider_modules.py"),
        ]
    )


def run_seed_backlog_sync_smoke():
    run_uv(
        [
            "run",
            "--python",
            "3.12",
            "--group",
            "dev",
            "python",
            str(ROOT / "tests" / "integration" / "test_seed_backlog_sync.py"),
        ]
    )


def run_audit_workflow_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_audit_workflow.py"),
        ]
    )


def run_promotion_state_machine_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_promotion_state_machine.py"),
        ]
    )


def run_engine_api_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/engine",
            "python",
            str(ROOT / "tests" / "integration" / "test_engine_api.py"),
        ]
    )


def run_cli_operator_flows_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "apps/cli",
            "python",
            str(ROOT / "tests" / "e2e" / "test_cli_operator_flows.py"),
        ]
    )


def run_scenario_replay_smoke():
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release" / "replay_scenarios.py"),
        ],
        cwd=str(ROOT),
        check=True,
    )


def run_scenario_replay_e2e_smoke():
    run_uv(
        [
            "run",
            "--directory",
            "services/mcp-server",
            "python",
            str(ROOT / "tests" / "e2e" / "test_scenario_replays.py"),
        ]
    )


def build_app_packages():
    build_python_package("apps/cli", ".tmp/dist/apps-cli")


def build_migration_packages():
    build_python_package("services/engine", ".tmp/dist/engine")
    build_python_package("services/mcp-server", ".tmp/dist/mcp-server")


def run_migration_smokes():
    run_engine_smoke()
    run_mcp_server_smoke()
    run_mcp_server_surface_smoke()
    run_mcp_server_parity_smoke()
    run_artifact_store_journal_smoke()
    run_claim_structuring_workflow_smoke()
    run_document_ingest_adapter_smoke()
    run_dual_formalization_workflow_smoke()
    run_audit_workflow_smoke()
    run_promotion_state_machine_smoke()
    run_engine_api_smoke()
    run_prompt_provider_modules_smoke()
    run_claim_trace_repository_smoke()
    run_seed_backlog_sync_smoke()
    run_cli_operator_flows_smoke()
    run_scenario_replay_smoke()
    run_scenario_replay_e2e_smoke()


def run_core_runner_smokes():
    run_graph_model_tests()
    run_audit_rules_tests()
    run_fwp_proof_integration_smoke()
    run_proof_protocol_seam_smoke()


def validate_generated_artifacts(item_count):
    if not GENERATED_DIR.exists():
        raise RuntimeError("Generated backlog directory is missing.")
    if not MANIFEST_PATH.exists():
        raise RuntimeError("Generated backlog manifest is missing.")

    issue_bodies = list((GENERATED_DIR / "issue-bodies").glob("*.md"))
    if len(issue_bodies) != item_count:
        raise RuntimeError(
            f"Expected {item_count} generated issue bodies, found {len(issue_bodies)}."
        )


def run_lint():
    ensure_paths_exist()
    validate_package_json()
    validate_backlog()
    compile_python_sources()
    lint_text_files()
    ensure_no_cursor_config()
    validate_agent_runtime_files()


def run_test():
    ensure_paths_exist()
    validate_package_json()
    items = validate_backlog()
    sync_agent_runtime_files()
    regenerate_backlog_artifacts()
    regenerate_contract_artifacts()
    validate_generated_artifacts(len(items))
    run_schema_tests()
    build_python_contracts()
    build_core_packages()
    run_core_runner_smokes()
    run_migration_smokes()
    build_app_packages()
    build_migration_packages()


def parse_args():
    parser = argparse.ArgumentParser(description="Run monorepo root checks.")
    parser.add_argument("--mode", choices=["lint", "test"], required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.mode == "lint":
        run_lint()
    else:
        run_test()


if __name__ == "__main__":
    main()
