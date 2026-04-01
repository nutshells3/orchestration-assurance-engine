"""Create a sanitized export copy for publishing the repository with fresh history.

The source workspace is left untouched. The export copy:

- drops local agent-shell artifacts such as ``.claude`` / ``.codex``
- removes tracked instruction files used only for local AI-assisted development
- rewrites selected docs and dev scripts so the exported tree remains coherent
- can optionally initialize a fresh git repository ready for a new remote
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT.parent / "orchestration-assurance-engine-export"

EXCLUDED_PREFIXES = (
    ".claude/",
    ".codex/",
)

EXCLUDED_PATHS = {
    "AGENTS.md",
    "CLAUDE.md",
    "settings/agent-control.json",
    "settings/README.md",
    "scripts/dev/sync_agent_settings.py",
    "scripts/release/prepare_public_export.py",
    "bash.exe.stackdump",
}

RENAME_MAP = {
    "docs/product/agent-runtime-contract.md": "docs/product/runtime-boundary-contract.md",
}

TEXT_EXTENSIONS = {
    ".cjs",
    ".cmd",
    ".css",
    ".csv",
    ".editorconfig",
    ".gitignore",
    ".html",
    ".json",
    ".lock",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

TEXT_FILENAMES = {
    ".editorconfig",
    ".gitignore",
    "justfile",
    ".pre-commit-config.yaml",
    "CODEOWNERS",
}

DOC_PREFIXES = (
    "docs/",
    "scripts/github/",
)

DOC_FILES = {
    "README.md",
}

ROOT_WINDOWS = str(ROOT)
ROOT_POSIX = ROOT.as_posix()

DOC_REPLACEMENTS = [
    ("docs/product + .codex + .claude", "docs/product"),
    ("docs/product, .codex, .claude", "docs/product"),
    ("settings/agent-control.json", "settings/verification.toml"),
    ("python scripts/dev/sync_agent_settings.py", "python scripts/dev/check_repo.py --mode lint"),
    ("Codex config and Claude instructions", "workflow documentation"),
    ("Codex and Claude Code", "engineering and architecture review"),
    ("Claude Code+Codex", "Architecture+Engineering"),
    ("Claude Code+Human", "Architecture+Tech Lead"),
    ("Human+Claude Code", "Tech Lead+Architecture"),
    ("Human+Codex", "Tech Lead+Engineering"),
    ("Claude Code", "Architecture Review"),
    ("claude code", "architecture review"),
    ("Codex", "Engineering"),
    ("claude-code", "architecture-review"),
    ("codex", "engineering"),
    (".claude", "workflow-config"),
    (".codex", "workflow-config"),
    ("CLAUDE.md", "workflow-notes.md"),
    ("AGENTS.md", "workflow-notes.md"),
    ("agent-runtime-contract", "runtime-boundary-contract"),
    ("Agent Runtime Contract", "Runtime Boundary Contract"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a sanitized public export without modifying the source repo."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination directory (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the output directory if it already exists.",
    )
    parser.add_argument(
        "--init-git",
        action="store_true",
        help="Initialize a fresh git repository in the export directory.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Create an initial commit after copying files. Implies --init-git.",
    )
    parser.add_argument(
        "--remote-url",
        help="Optional remote URL to add as origin when --init-git is used.",
    )
    return parser.parse_args()


def run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd or ROOT),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def list_repo_files() -> list[str]:
    tracked = run_git(["ls-files", "-z"]).stdout.split("\x00")
    untracked = run_git(["ls-files", "--others", "--exclude-standard", "-z"]).stdout.split("\x00")
    files: set[str] = set()
    for rel in tracked + untracked:
        if not rel:
            continue
        normalized = rel.replace("\\", "/")
        path = ROOT / normalized
        if path.is_file():
            files.add(normalized)
    return sorted(files)


def should_exclude(rel_path: str) -> bool:
    if rel_path in EXCLUDED_PATHS:
        return True
    return any(rel_path.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def export_rel_path(rel_path: str) -> str:
    return RENAME_MAP.get(rel_path, rel_path)


def is_text_file(rel_path: str) -> bool:
    name = Path(rel_path).name
    suffix = Path(rel_path).suffix.lower()
    return name in TEXT_FILENAMES or suffix in TEXT_EXTENSIONS


def strip_repo_prefix(text: str) -> str:
    text = text.replace(ROOT_WINDOWS + "\\", "")
    text = text.replace(ROOT_POSIX + "/", "")
    return text


def normalize_markdown_links(text: str) -> str:
    def rewrite(match: re.Match[str]) -> str:
        target = match.group(1).replace("\\", "/")
        return f"]({target})"

    return re.sub(r"\]\(([^)]+)\)", rewrite, text)


def sanitize_document_text(text: str) -> str:
    for old, new in DOC_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def sanitize_gitignore(text: str) -> str:
    kept = [line for line in text.splitlines() if line not in {".codex/", ".claude/"}]
    return "\n".join(kept) + "\n"


def sanitize_justfile(text: str) -> str:
    text = re.sub(
        r"\n+sync-agent-settings:\r?\n  python scripts/dev/sync_agent_settings\.py\r?\n",
        "\n",
        text,
        flags=re.MULTILINE,
    )
    return text


def sanitize_common_py(text: str) -> str:
    text = re.sub(r'^\s*"\.codex[^"]*",\n', "", text, flags=re.MULTILINE)
    text = re.sub(r'^\s*"\.claude[^"]*",\n', "", text, flags=re.MULTILINE)
    text = re.sub(r'^\s*"AGENTS\.md",\n', "", text, flags=re.MULTILINE)
    text = re.sub(r'^\s*"CLAUDE\.md",\n', "", text, flags=re.MULTILINE)
    text = re.sub(r'^\s*"settings/agent-control\.json",\n', "", text, flags=re.MULTILINE)
    text = re.sub(r'^\s*"settings/README\.md",\n', "", text, flags=re.MULTILINE)
    text = re.sub(r'^\s*"scripts/dev/sync_agent_settings\.py",\n', "", text, flags=re.MULTILINE)
    text = text.replace(
        '"docs/product/agent-runtime-contract.md"',
        '"docs/product/runtime-boundary-contract.md"',
    )
    return text


def sanitize_check_repo_py(text: str) -> str:
    text = text.replace(
        'AGENT_CONTRACT = ROOT / "docs" / "product" / "agent-runtime-contract.md"\n',
        "",
    )
    text = re.sub(
        r'AGENT_SETTINGS = ROOT / "settings" / "agent-control\.json"\n'
        r'AGENT_SETTINGS_REL = "settings/agent-control\.json"\n'
        r'AGENT_SETTINGS_SYNC = ROOT / "scripts" / "dev" / "sync_agent_settings\.py"\n'
        r'REQUIRED_AGENT_CONTRACT_TOKENS = \[\n.*?\n\]\n',
        "",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"\ndef validate_agent_runtime_files\(\):\n.*?\n\ndef sync_agent_runtime_files\(\):\n.*?\n\ndef regenerate_backlog_artifacts\(\):",
        "\n\ndef regenerate_backlog_artifacts():",
        text,
        flags=re.S,
    )
    text = text.replace("    validate_agent_runtime_files()\n", "")
    text = text.replace("    sync_agent_runtime_files()\n", "")
    return text


def runtime_boundary_contract_text() -> str:
    return """# Runtime Boundary Contract

This repository has one owner for semantics and several thin transport layers.

Package boundaries:

- `services/engine` owns canonical workflows, artifact persistence, promotion policy, and read models.
- `FWP` is the only proof transport seam `formal-claim` may speak across.
- `proof-assistant` owns managed proof jobs, run governance, start/poll/cancel/kill, and backend-specific prover execution.
- `services/mcp-server` is a thin MCP facade over engine workflows and FWP-backed proof job controls.
- `apps/cli` is a human transport wrapper over engine workflows and FWP-backed proof job controls.
- `apps/desktop` is a thin shell for browsing artifacts, editing generated theory files, and invoking governed runs without adding proof semantics.
- `packages/evidence-connectors` extracts source mappings and evaluation evidence only; it never owns canonical ClaimGraph revisions.
- `packages/graph-model`, `packages/audit-rules`, and `packages/contracts*` own reusable core/query/contract logic, not transport policy.

Required commands:

- `python scripts/dev/check_repo.py --mode lint`
- `python scripts/dev/check_repo.py --mode test`
- `python scripts/release/replay_scenarios.py`
- `python scripts/release/build_release_artifacts.py`
- `python scripts/release/smoke_release.py`
- `python scripts/github/generate_backlog_scripts.py`
- `python scripts/github/seed_backlog.py --repo nutshells3/proof-claim --mode all`

Canonical MCP tools:

- `project.create`
- `project.open`
- `project.list`
- `document.ingest`
- `claim.structure`
- `formalize.dual`
- `audit.run`
- `profile.recompute`
- `promotion.transition`
- `trace.export`
- `prefix.extract`
- `bundle.export` *(compatibility-only; prefer `trace.export`)*
- `proof.run.start`
- `job.get`
- `job.cancel`
- `job.kill`

Rules:

1. Do not re-implement workflow state, promotion policy, graph semantics, or audit policy outside `services/engine`.
2. Do not reintroduce a local backend runtime inside `formal-claim`; proof execution must stay behind `FWP` and `proof-assistant`.
3. Do not make desktop or MCP the owner of claim importance, hotspot rules, stale resolution, or promotion eligibility.
4. Route long-running proof work through the FWP seam and keep cancel/kill/cleanup observable.
5. Treat scenario replay and release smoke as acceptance gates for productization work.
"""


def sanitize_release_packaging(text: str) -> str:
    text = re.sub(
        r"\nIf the repository is moved, update repo-local agent configs.*\n?$",
        "\n",
        text,
        flags=re.S,
    )
    return text


def sanitize_text(source_rel: str, dest_rel: str, text: str) -> str:
    text = strip_repo_prefix(text)
    text = normalize_markdown_links(text)

    if source_rel == "docs/product/agent-runtime-contract.md":
        return runtime_boundary_contract_text()
    if source_rel == ".gitignore":
        return sanitize_gitignore(text)
    if source_rel == "justfile":
        return sanitize_justfile(text)
    if source_rel == "scripts/dev/common.py":
        return sanitize_common_py(text)
    if source_rel == "scripts/dev/check_repo.py":
        return sanitize_check_repo_py(text)
    if source_rel == "docs/product/release-packaging.md":
        text = sanitize_release_packaging(text)

    if source_rel.startswith(DOC_PREFIXES) or source_rel in DOC_FILES:
        text = sanitize_document_text(text)

    if dest_rel.endswith(".md") and not text.endswith("\n"):
        text += "\n"
    return text


def copy_file(source_rel: str, output_root: Path) -> None:
    source_path = ROOT / source_rel
    dest_rel = export_rel_path(source_rel)
    dest_path = output_root / dest_rel
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if is_text_file(source_rel):
        text = source_path.read_text(encoding="utf-8", errors="surrogateescape")
        dest_path.write_text(
            sanitize_text(source_rel, dest_rel, text),
            encoding="utf-8",
            errors="surrogateescape",
        )
    else:
        shutil.copy2(source_path, dest_path)


def ensure_output_dir(output_root: Path, force: bool) -> None:
    if output_root.exists():
        if not force:
            raise RuntimeError(
                f"Output directory already exists: {output_root}\n"
                "Pass --force to replace it."
            )
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)


def init_git_repo(output_root: Path, remote_url: str | None, create_commit: bool) -> None:
    run_git(["init", "-b", "main"], cwd=output_root)
    if remote_url:
        run_git(["remote", "add", "origin", remote_url], cwd=output_root)
    run_git(["add", "."], cwd=output_root)
    if create_commit:
        run_git(["commit", "-m", "Initial sanitized import"], cwd=output_root)


def main() -> int:
    args = parse_args()
    output_root = args.output.resolve()

    try:
        ensure_output_dir(output_root, force=args.force)
        copied = 0
        skipped = 0

        for rel_path in list_repo_files():
            if should_exclude(rel_path):
                skipped += 1
                continue
            copy_file(rel_path, output_root)
            copied += 1

        if args.init_git or args.commit:
            init_git_repo(output_root, args.remote_url, create_commit=args.commit)

        print(f"Export created: {output_root}")
        print(f"Copied files: {copied}")
        print(f"Skipped files: {skipped}")
        if args.init_git or args.commit:
            print("Fresh git repository initialized in the export directory.")
        if args.remote_url:
            print(f"Origin remote: {args.remote_url}")
        return 0
    except Exception as exc:  # pragma: no cover - export tool error path
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
