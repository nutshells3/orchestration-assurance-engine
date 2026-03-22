"""Shared constants and tool resolution helpers for root-level developer scripts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKLOG_CSV = ROOT / "docs" / "FORMAL_CLAIM_MONOREPO_BACKLOG.csv"
BACKLOG_GENERATOR = ROOT / "scripts" / "github" / "generate_backlog_scripts.py"
GENERATED_DIR = ROOT / "scripts" / "github" / "generated"
MANIFEST_PATH = GENERATED_DIR / "backlog_seed_manifest.json"

PNPM_VERSION = "10.32.1"
UV_VERSION = "0.10.12"


def _uv_candidate_paths() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    executable_dir = Path(sys.executable).resolve().parent

    if os.name == "nt":
        candidates.extend(
            [
                executable_dir / "uv.exe",
                ROOT / ".venv" / "Scripts" / "uv.exe",
                home / ".cargo" / "bin" / "uv.exe",
            ]
        )
        for base in (
            home / "AppData" / "Roaming" / "Python",
            home / "AppData" / "Local" / "Programs" / "Python",
        ):
            if not base.exists():
                continue
            candidates.extend(
                sorted(base.glob("Python*/Scripts/uv.exe"), reverse=True)
            )
    else:
        candidates.extend(
            [
                executable_dir / "uv",
                ROOT / ".venv" / "bin" / "uv",
                home / ".cargo" / "bin" / "uv",
                home / ".local" / "bin" / "uv",
            ]
        )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def resolve_uv_command() -> list[str] | None:
    configured = os.environ.get("FORMAL_CLAIM_UV")
    if configured:
        return [configured]

    module_command = [sys.executable, "-m", "uv"]
    try:
        subprocess.run(
            module_command + ["--version"],
            cwd=str(ROOT),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return module_command
    except (OSError, subprocess.CalledProcessError):
        pass

    discovered = shutil.which("uv")
    if discovered:
        return [discovered]

    for candidate in _uv_candidate_paths():
        if candidate.exists():
            return [str(candidate)]
    return None


def require_uv_command() -> list[str]:
    command = resolve_uv_command()
    if command is not None:
        return command
    raise RuntimeError(
        "uv CLI could not be resolved. Install uv or set FORMAL_CLAIM_UV "
        "to the absolute uv executable path."
    )


def _cargo_candidate_paths() -> list[Path]:
    home = Path.home()
    executable_dir = Path(sys.executable).resolve().parent
    if os.name == "nt":
        return [
            executable_dir / "cargo.exe",
            home / ".cargo" / "bin" / "cargo.exe",
            home / ".rustup" / "toolchains" / "stable-x86_64-pc-windows-msvc" / "bin" / "cargo.exe",
        ]
    return [
        executable_dir / "cargo",
        home / ".cargo" / "bin" / "cargo",
    ]


def resolve_cargo_command() -> list[str] | None:
    configured = os.environ.get("FORMAL_CLAIM_CARGO")
    if configured:
        return [configured]

    discovered = shutil.which("cargo")
    if discovered:
        return [discovered]

    for candidate in _cargo_candidate_paths():
        if candidate.exists():
            return [str(candidate)]
    return None


def require_cargo_command() -> list[str]:
    command = resolve_cargo_command()
    if command is not None:
        return command
    raise RuntimeError(
        "cargo CLI could not be resolved. Install Rust or set FORMAL_CLAIM_CARGO "
        "to the absolute cargo executable path."
    )

EXPECTED_TOP_LEVEL_DIRS = [
    ".github",
    "settings",
    "apps",
    "services",
    "packages",
    "integrations",
    "examples",
    "tests",
    "docs",
    "scripts",
]

EXPECTED_SUBDIRS = [
    ".github/ISSUE_TEMPLATE",
    "apps/cli",
    "apps/cli/src/formal_claim_cli",
    "services/engine",
    "services/engine/src/formal_claim_engine",
    "services/engine/src/formal_claim_engine/agents",
    "services/engine/src/formal_claim_engine/agents/prompts",
    "services/mcp-server",
    "services/mcp-server/src/formal_claim_mcp_server",
    "packages/contracts",
    "packages/contracts-py",
    "packages/graph-model",
    "packages/graph-model/src/formal_claim_graph",
    "packages/audit-rules",
    "packages/audit-rules/src/formal_claim_audit_rules",
    "packages/evidence-connectors",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors",
    "integrations/isabelle/sessions",
    "integrations/isabelle/templates",
    "integrations/isabelle/exporters",
    "integrations/assurance/views",
    "integrations/assurance/imports",
    "examples/toy-claim",
    "examples/theorem-audit",
    "examples/paper-claim",
    "examples/legal-claim",
    "tests/schema",
    "tests/audit",
    "tests/integration",
    "tests/e2e",
    "docs/architecture",
    "docs/product",
    "docs/policies",
    "scripts/contracts",
    "scripts/dev",
    "scripts/github",
    "scripts/release",
    "settings",
    "packages/contracts-py/src/formal_claim_contracts",
    "tests/schema/fixtures/invalid",
    "tests/schema/fixtures/legacy",
]

EXPECTED_FILES = [
    "README.md",
    ".gitignore",
    ".editorconfig",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "pyproject.toml",
    "uv.lock",
    "justfile",
    ".pre-commit-config.yaml",
    ".github/CODEOWNERS",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/backlog_work_item.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/governance_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    "docs/FORMAL_CLAIM_MONOREPO_BACKLOG.csv",
    "docs/product/runtime-boundary-contract.md",
    "docs/product/release-packaging.md",
    "docs/policies/github-governance.md",
    "apps/cli/README.md",
    "apps/cli/pyproject.toml",
    "apps/cli/src/formal_claim_cli/__init__.py",
    "apps/cli/src/formal_claim_cli/__main__.py",
    "apps/cli/src/formal_claim_cli/cli.py",
    "examples/theorem-audit/claim-graph.json",
    "examples/theorem-audit/assurance-graph.json",
    "examples/theorem-audit/assurance-profile.json",
    "examples/theorem-audit/runner-fixtures/README.md",
    "examples/theorem-audit/runner-fixtures/audit-request.json",
    "examples/theorem-audit/runner-fixtures/expected-audit.json",
    "examples/theorem-audit/runner-fixtures/expected-profile.json",
    "examples/theorem-audit/runner-fixtures/profile-request.json",
    "examples/theorem-audit/runner-fixtures/sessions/definitional/workspace-request.json",
    "examples/theorem-audit/runner-fixtures/sessions/locale-based/workspace-request.json",
    "examples/theorem-audit/runner-fixtures/sessions/sorry-containing/workspace-request.json",
    "examples/theorem-audit/runner-fixtures/sessions/suspicious/workspace-request.json",
    "services/engine/README.md",
    "services/engine/pyproject.toml",
    "services/engine/src/formal_claim_engine/__init__.py",
    "services/engine/src/formal_claim_engine/audit_workflow.py",
    "services/engine/src/formal_claim_engine/claim_structuring_workflow.py",
    "services/engine/src/formal_claim_engine/claim_trace_repository.py",
    "services/engine/src/formal_claim_engine/claim_trace_service.py",
    "services/engine/src/formal_claim_engine/claim_trace_types.py",
    "services/engine/src/formal_claim_engine/cli.py",
    "services/engine/src/formal_claim_engine/config.py",
    "services/engine/src/formal_claim_engine/document_ingest.py",
    "services/engine/src/formal_claim_engine/dual_formalization_workflow.py",
    "services/engine/src/formal_claim_engine/engine_api.py",
    "services/engine/src/formal_claim_engine/evaluation_evidence.py",
    "services/engine/src/formal_claim_engine/promotion_state_machine.py",
    "services/engine/src/formal_claim_engine/proof_control.py",
    "services/engine/src/formal_claim_engine/proof_protocol.py",
    "services/engine/src/formal_claim_engine/store.py",
    "services/engine/src/formal_claim_engine/audit_rules.py",
    "services/engine/src/formal_claim_engine/agents/provider_adapters.py",
    "services/engine/src/formal_claim_engine/agents/prompt_registry.py",
    "services/engine/src/formal_claim_engine/agents/response_schema_registry.py",
    "services/engine/src/formal_claim_engine/agents/prompts/planner.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/claim_graph_agent.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/formalizer.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/proof_verifier.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/auditor.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/research_agent.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/dev_agent.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/policy_engine.system.md",
    "services/mcp-server/README.md",
    "services/mcp-server/pyproject.toml",
    "services/mcp-server/src/formal_claim_mcp_server/__init__.py",
    "services/mcp-server/src/formal_claim_mcp_server/__main__.py",
    "services/mcp-server/src/formal_claim_mcp_server/jobs.py",
    "services/mcp-server/src/formal_claim_mcp_server/models.py",
    "services/mcp-server/src/formal_claim_mcp_server/server.py",
    "packages/graph-model/pyproject.toml",
    "packages/graph-model/README.md",
    "packages/graph-model/src/formal_claim_graph/__init__.py",
    "packages/graph-model/src/formal_claim_graph/_contracts.py",
    "packages/graph-model/src/formal_claim_graph/claim_queries.py",
    "packages/graph-model/src/formal_claim_graph/assurance_queries.py",
    "packages/graph-model/src/formal_claim_graph/trust_frontier.py",
    "packages/audit-rules/pyproject.toml",
    "packages/audit-rules/README.md",
    "packages/audit-rules/src/formal_claim_audit_rules/__init__.py",
    "packages/audit-rules/src/formal_claim_audit_rules/compat.py",
    "packages/audit-rules/src/formal_claim_audit_rules/contract_pack.py",
    "packages/audit-rules/src/formal_claim_audit_rules/engine.py",
    "packages/evidence-connectors/pyproject.toml",
    "packages/evidence-connectors/README.md",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/__init__.py",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/compat.py",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/document_ingest.py",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/evaluation_evidence.py",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/models.py",
    "packages/contracts-py/pyproject.toml",
    "packages/contracts-py/README.md",
    "packages/contracts-py/src/formal_claim_contracts/__init__.py",
    "packages/contracts-py/src/formal_claim_contracts/py.typed",
    "scripts/contracts/generate_bindings.py",
    "scripts/github/generate_backlog_scripts.py",
    "scripts/github/seed_backlog.py",
    "scripts/dev/bootstrap.py",
    "scripts/dev/check_repo.py",
    "scripts/dev/run_uv.py",
    "scripts/release/build_release_artifacts.py",
    "scripts/release/replay_scenarios.py",
    "scripts/release/smoke_release.py",
    "tests/schema/README.md",
    "tests/schema/test_schema_conformance.py",
    "tests/audit/test_graph_queries.py",
    "tests/audit/test_audit_rules.py",
    "tests/integration/test_artifact_store_journal.py",
    "tests/integration/test_audit_workflow.py",
    "tests/integration/test_claim_structuring_workflow.py",
    "tests/integration/test_claim_trace_repository_sqlite.py",
    "tests/integration/test_dual_formalization_workflow.py",
    "tests/integration/test_document_ingest_adapter.py",
    "tests/integration/test_engine_api.py",
    "tests/integration/test_fwp_proof_integration.py",
    "tests/integration/test_mcp_server_parity.py",
    "tests/integration/test_mcp_server_surface.py",
    "tests/integration/test_promotion_state_machine.py",
    "tests/integration/test_proof_protocol_seam.py",
    "tests/integration/test_prompt_provider_modules.py",
    "tests/integration/test_seed_backlog_sync.py",
    "tests/e2e/test_cli_operator_flows.py",
    "tests/schema/fixtures/invalid/claim-graph.invalid.json",
    "tests/schema/fixtures/invalid/assurance-graph.invalid.json",
    "tests/schema/fixtures/invalid/assurance-profile.invalid.json",
    "tests/schema/fixtures/legacy/claim-graph.legacy.json",
]

MANAGED_TEXT_FILES = [
    ".editorconfig",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".github/CODEOWNERS",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/backlog_work_item.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/governance_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    "README.md",
    "apps/cli/README.md",
    "apps/cli/pyproject.toml",
    "apps/cli/src/formal_claim_cli/__init__.py",
    "apps/cli/src/formal_claim_cli/__main__.py",
    "apps/cli/src/formal_claim_cli/cli.py",
    "apps/README.md",
    "docs/README.md",
    "docs/FORMAL_CLAIM_MONOREPO_BACKLOG.csv",
    "docs/product/runtime-boundary-contract.md",
    "docs/product/release-packaging.md",
    "docs/policies/github-governance.md",
    "examples/README.md",
    "examples/theorem-audit/runner-fixtures/README.md",
    "examples/theorem-audit/runner-fixtures/audit-request.json",
    "examples/theorem-audit/runner-fixtures/expected-audit.json",
    "examples/theorem-audit/runner-fixtures/expected-profile.json",
    "examples/theorem-audit/runner-fixtures/profile-request.json",
    "examples/theorem-audit/runner-fixtures/sessions/definitional/workspace-request.json",
    "examples/theorem-audit/runner-fixtures/sessions/locale-based/workspace-request.json",
    "examples/theorem-audit/runner-fixtures/sessions/sorry-containing/workspace-request.json",
    "examples/theorem-audit/runner-fixtures/sessions/suspicious/workspace-request.json",
    "packages/contracts/README.md",
    "packages/contracts-py/pyproject.toml",
    "packages/contracts-py/README.md",
    "packages/contracts-py/src/formal_claim_contracts/__init__.py",
    "integrations/README.md",
    "justfile",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "packages/README.md",
    "pyproject.toml",
    "scripts/dev/bootstrap.py",
    "scripts/dev/check_repo.py",
    "scripts/dev/common.py",
    "scripts/dev/run_uv.py",
    "scripts/release/build_release_artifacts.py",
    "scripts/release/replay_scenarios.py",
    "scripts/release/smoke_release.py",
    "scripts/contracts/generate_bindings.py",
    "scripts/github/README.md",
    "scripts/github/generate_backlog_scripts.py",
    "scripts/github/seed_backlog.py",
    "services/README.md",
    "services/engine/README.md",
    "services/engine/pyproject.toml",
    "services/engine/src/formal_claim_engine/__init__.py",
    "services/engine/src/formal_claim_engine/audit_workflow.py",
    "services/engine/src/formal_claim_engine/claim_structuring_workflow.py",
    "services/engine/src/formal_claim_engine/claim_trace_repository.py",
    "services/engine/src/formal_claim_engine/claim_trace_service.py",
    "services/engine/src/formal_claim_engine/claim_trace_types.py",
    "services/engine/src/formal_claim_engine/cli.py",
    "services/engine/src/formal_claim_engine/config.py",
    "services/engine/src/formal_claim_engine/document_ingest.py",
    "services/engine/src/formal_claim_engine/dual_formalization_workflow.py",
    "services/engine/src/formal_claim_engine/engine_api.py",
    "services/engine/src/formal_claim_engine/evaluation_evidence.py",
    "services/engine/src/formal_claim_engine/promotion_state_machine.py",
    "services/engine/src/formal_claim_engine/proof_control.py",
    "services/engine/src/formal_claim_engine/proof_protocol.py",
    "services/engine/src/formal_claim_engine/store.py",
    "services/engine/src/formal_claim_engine/audit_rules.py",
    "services/engine/src/formal_claim_engine/agents/provider_adapters.py",
    "services/engine/src/formal_claim_engine/agents/prompt_registry.py",
    "services/engine/src/formal_claim_engine/agents/response_schema_registry.py",
    "services/engine/src/formal_claim_engine/agents/prompts/planner.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/claim_graph_agent.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/formalizer.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/proof_verifier.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/auditor.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/research_agent.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/dev_agent.system.md",
    "services/engine/src/formal_claim_engine/agents/prompts/policy_engine.system.md",
    "services/mcp-server/README.md",
    "services/mcp-server/pyproject.toml",
    "services/mcp-server/src/formal_claim_mcp_server/__init__.py",
    "services/mcp-server/src/formal_claim_mcp_server/__main__.py",
    "services/mcp-server/src/formal_claim_mcp_server/jobs.py",
    "services/mcp-server/src/formal_claim_mcp_server/models.py",
    "services/mcp-server/src/formal_claim_mcp_server/server.py",
    "packages/graph-model/pyproject.toml",
    "packages/graph-model/README.md",
    "packages/graph-model/src/formal_claim_graph/__init__.py",
    "packages/graph-model/src/formal_claim_graph/_contracts.py",
    "packages/graph-model/src/formal_claim_graph/claim_queries.py",
    "packages/graph-model/src/formal_claim_graph/assurance_queries.py",
    "packages/graph-model/src/formal_claim_graph/trust_frontier.py",
    "packages/audit-rules/pyproject.toml",
    "packages/audit-rules/README.md",
    "packages/audit-rules/src/formal_claim_audit_rules/__init__.py",
    "packages/audit-rules/src/formal_claim_audit_rules/compat.py",
    "packages/audit-rules/src/formal_claim_audit_rules/contract_pack.py",
    "packages/audit-rules/src/formal_claim_audit_rules/engine.py",
    "packages/evidence-connectors/pyproject.toml",
    "packages/evidence-connectors/README.md",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/__init__.py",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/compat.py",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/document_ingest.py",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/evaluation_evidence.py",
    "packages/evidence-connectors/src/formal_claim_evidence_connectors/models.py",
    "tests/schema/README.md",
    "tests/schema/test_schema_conformance.py",
    "tests/audit/test_graph_queries.py",
    "tests/audit/test_audit_rules.py",
    "tests/integration/test_artifact_store_journal.py",
    "tests/integration/test_audit_workflow.py",
    "tests/integration/test_claim_structuring_workflow.py",
    "tests/integration/test_claim_trace_repository_sqlite.py",
    "tests/integration/test_dual_formalization_workflow.py",
    "tests/integration/test_document_ingest_adapter.py",
    "tests/integration/test_engine_api.py",
    "tests/integration/test_fwp_proof_integration.py",
    "tests/integration/test_mcp_server_parity.py",
    "tests/integration/test_mcp_server_surface.py",
    "tests/integration/test_promotion_state_machine.py",
    "tests/integration/test_proof_protocol_seam.py",
    "tests/integration/test_prompt_provider_modules.py",
    "tests/integration/test_seed_backlog_sync.py",
    "tests/e2e/test_cli_operator_flows.py",
    "tests/schema/fixtures/invalid/claim-graph.invalid.json",
    "tests/schema/fixtures/invalid/assurance-graph.invalid.json",
    "tests/schema/fixtures/invalid/assurance-profile.invalid.json",
    "tests/schema/fixtures/legacy/claim-graph.legacy.json",
    "tests/README.md",
    "uv.lock",
]
