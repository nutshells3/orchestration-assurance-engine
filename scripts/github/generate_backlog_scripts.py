#!/usr/bin/env python3
"""Generate GitHub backlog seeding artifacts from the formal-claim backlog CSV."""

from __future__ import annotations

import argparse
import csv
import json
import re
import textwrap
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


DEFAULT_LABEL_COLOR = "cfd8dc"
VALID_STATUSES = {"planned", "partial", "in_progress", "done", "blocked"}
VALID_EXECUTION_MODES = {
    "bootstrap",
    "normalize",
    "extract",
    "absorb",
    "replace",
    "implement",
}
VALID_WAVES = {
    "foundation",
    "migration",
    "core-runner-base",
    "core-runner-advanced",
    "workflow-mcp-cli",
    "productization",
}
BACKLOG_MARKER_TEMPLATE = "<!-- backlog-id: {backlog_id} -->"

LABEL_COLOR_GROUPS = [
    (
        "1f6feb",
        {
            "infra",
            "monorepo",
            "devex",
            "governance",
            "github",
            "release",
            "packaging",
            "timeline",
        },
    ),
    (
        "0e8a16",
        {
            "assurance",
            "audit",
            "contracts",
            "deterministic",
            "downstream",
            "intent",
            "policy",
            "promotion",
            "traceability",
            "trust-frontier",
            "vacuity",
        },
    ),
    (
        "5319e7",
        {
            "build",
            "dependencies",
            "dump",
            "export",
            "isabelle",
            "logs",
            "nitpick",
            "oracles",
            "robustness",
            "runner",
            "sledgehammer",
            "workspace",
        },
    ),
    (
        "d93f0b",
        {
            "desktop",
            "editor",
            "frontend",
            "react-flow",
            "tauri",
            "ux",
        },
    ),
    (
        "0052cc",
        {
            "api",
            "artifacts",
            "backend",
            "cli",
            "codegen",
            "domain",
            "engine",
            "events",
            "graph",
            "library",
            "mcp",
            "migration",
            "ops",
            "platform",
            "python",
            "schema",
            "service",
            "sqlite",
            "state-machine",
            "storage",
            "typescript",
            "versioning",
            "workflow",
        },
    ),
    (
        "a2eeef",
        {
            "citations",
            "claim-tracer",
            "diff",
            "docs",
            "documents",
            "evidence",
            "experiments",
            "ingestion",
            "metrics",
        },
    ),
    (
        "fbca04",
        {
            "e2e",
            "fixtures",
            "integration",
            "regression",
            "scenarios",
            "tests",
        },
    ),
    (
        "c2e0c6",
        {
            "agents",
            "claude-code",
            "codex",
            "formalization",
            "planner",
            "prompts",
            "providers",
            "skills",
        },
    ),
]

MILESTONE_DESCRIPTIONS = {
    "M0 Foundation": "Set up repository boundaries, governance, contracts, and fixture baselines.",
    "M1 Migration": "Move the uploaded skeletons into the target service and package layout.",
    "M2 Core Domain": "Implement canonical graph, audit, policy, contract, and revision logic.",
    "M3 Isabelle Runner": "Build deterministic Isabelle execution, export, and audit services.",
    "M4 Engine Workflows": "Compose claim structuring, formalization, audit, promotion, and ingest workflows.",
    "M5 MCP": "Expose the stable MCP surface over the canonical engine and proof runner.",
    "M6 CLI": "Ship the human-facing CLI for bootstrap, audit, and inspection workflows.",
    "M7 Desktop": "Build the standalone desktop shell and graph-first inspection workflows.",
    "M8 Evidence": "Add external evidence connectors, provenance capture, and review flows.",
    "M9 Hardening": "Close testing, packaging, release, documentation, and operational hardening gaps.",
}

WAVE_GUIDANCE = {
    "foundation": "Freeze repo boundaries, contracts, and fixtures before extending agents or UI surfaces.",
    "migration": "Absorb donor packages into canonical service boundaries and remove duplicate model/store layers.",
    "core-runner-base": "Build graph and policy cores together with the deterministic build/export pipeline first.",
    "core-runner-advanced": "Layer theorem-local trust, robustness, and advanced audit logic on top of stable runner outputs.",
    "workflow-mcp-cli": "Expose and compose stable internal workflows only after the contracts and runner surfaces have settled.",
    "productization": "Leave desktop, external evidence, and hardening work until the core services and contracts are stable.",
}

STATUS_GUIDANCE = {
    "planned": "No canonical implementation work has started yet for this backlog item.",
    "partial": "Prototype assets already exist, but the canonical package or service boundary is not complete yet.",
    "in_progress": "Canonical implementation work is already active; continue from the current state instead of restarting.",
    "done": "This backlog item is recorded as completed and should only be reopened deliberately.",
    "blocked": "This backlog item is currently blocked and should not be advanced without resolving the blocker first.",
}

EXECUTION_MODE_GUIDANCE = {
    "bootstrap": "Bootstrap the missing canonical boundary and tooling that later waves depend on.",
    "normalize": "Move and tighten existing prototype assets into the canonical contract package and remove duplicate copies.",
    "extract": "Extract donor code into the target package boundary; keep any old import surface only as a temporary shim if needed.",
    "absorb": "Absorb the useful prototype surface into the canonical service boundary and retire duplicate semantics.",
    "replace": "Replace the heuristic or legacy path with the deterministic canonical one once parity exists.",
    "implement": "Implement the target capability on top of the frozen canonical contracts and migrated services.",
}


@dataclass(frozen=True)
class BacklogItem:
    id: str
    milestone: str
    wave: str
    status: str
    execution_mode: str
    package: str
    path: str
    title: str
    type: str
    owner: str
    executor: str
    estimate: str
    depends_on: Sequence[str]
    labels: Sequence[str]
    prototype_basis: str
    summary: str
    acceptance: str

    @property
    def issue_title(self) -> str:
        return f"[{self.id}] {self.title}"

    @property
    def marker(self) -> str:
        return BACKLOG_MARKER_TEMPLATE.format(backlog_id=self.id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate GitHub backlog seeding artifacts from the backlog CSV."
    )
    parser.add_argument(
        "--csv",
        default="docs/FORMAL_CLAIM_MONOREPO_BACKLOG.csv",
        help="Path to the backlog CSV.",
    )
    parser.add_argument(
        "--output-dir",
        default="scripts/github/generated",
        help="Directory where generated artifacts will be written.",
    )
    return parser.parse_args()


def ordered_unique(items: Iterable[str]) -> List[str]:
    return list(OrderedDict.fromkeys(items))


def split_csv_labels(raw: str) -> List[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def split_dependencies(raw: str) -> List[str]:
    return [part for part in re.split(r"[\s,]+", raw.strip()) if part]


def load_backlog(csv_path: Path) -> List[BacklogItem]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "id",
            "milestone",
            "wave",
            "status",
            "execution_mode",
            "package",
            "path",
            "title",
            "type",
            "owner",
            "executor",
            "estimate",
            "depends_on",
            "labels",
            "prototype_basis",
            "summary",
            "acceptance",
        }
        missing = sorted(required.difference(reader.fieldnames or []))
        if missing:
            raise ValueError(
                f"Backlog CSV is missing required columns: {', '.join(missing)}"
            )

        items: List[BacklogItem] = []
        for row in reader:
            items.append(
                BacklogItem(
                    id=row["id"].strip(),
                    milestone=row["milestone"].strip(),
                    wave=row["wave"].strip(),
                    status=row["status"].strip(),
                    execution_mode=row["execution_mode"].strip(),
                    package=row["package"].strip(),
                    path=row["path"].strip(),
                    title=row["title"].strip(),
                    type=row["type"].strip(),
                    owner=row["owner"].strip(),
                    executor=row["executor"].strip(),
                    estimate=row["estimate"].strip(),
                    depends_on=split_dependencies(row["depends_on"]),
                    labels=split_csv_labels(row["labels"]),
                    prototype_basis=row["prototype_basis"].strip(),
                    summary=row["summary"].strip(),
                    acceptance=row["acceptance"].strip(),
                )
            )
    return items


def find_dependency_cycle(items: Sequence[BacklogItem]) -> List[str]:
    by_id = {item.id: item for item in items}
    state: Dict[str, str] = {}
    stack: List[str] = []

    def visit(node_id: str) -> List[str]:
        state[node_id] = "visiting"
        stack.append(node_id)
        for dependency in by_id[node_id].depends_on:
            if dependency not in by_id:
                continue
            dependency_state = state.get(dependency)
            if dependency_state is None:
                cycle = visit(dependency)
                if cycle:
                    return cycle
            elif dependency_state == "visiting":
                start = stack.index(dependency)
                return stack[start:] + [dependency]
        stack.pop()
        state[node_id] = "done"
        return []

    for item in items:
        if state.get(item.id) is None:
            cycle = visit(item.id)
            if cycle:
                return cycle
    return []


def validate_backlog(items: Sequence[BacklogItem]) -> None:
    errors: List[str] = []
    ids = [item.id for item in items]
    counts = Counter(ids)
    duplicates = [item_id for item_id, count in counts.items() if count > 1]
    if duplicates:
        errors.append(f"Duplicate backlog IDs: {', '.join(sorted(duplicates))}")

    by_id = {item.id: item for item in items}
    for item in items:
        if item.status not in VALID_STATUSES:
            errors.append(f"{item.id}: invalid status '{item.status}'")
        if item.execution_mode not in VALID_EXECUTION_MODES:
            errors.append(
                f"{item.id}: invalid execution_mode '{item.execution_mode}'"
            )
        if item.wave not in VALID_WAVES:
            errors.append(f"{item.id}: invalid wave '{item.wave}'")
        if not item.labels:
            errors.append(f"{item.id}: at least one label is required")
        for dependency in item.depends_on:
            if dependency not in by_id:
                errors.append(f"{item.id}: unknown dependency '{dependency}'")

    cycle = find_dependency_cycle(items)
    if cycle:
        errors.append(
            "Dependency cycle detected: " + " -> ".join(cycle)
        )

    if errors:
        raise ValueError("Backlog validation failed:\n- " + "\n- ".join(errors))


def label_color(label: str) -> str:
    for color, members in LABEL_COLOR_GROUPS:
        if label in members:
            return color
    return DEFAULT_LABEL_COLOR


def humanize_label(label: str) -> str:
    return label.replace("-", " ")


def label_description(label: str) -> str:
    return f"Backlog label for {humanize_label(label)} work."


def milestone_description(milestone: str) -> str:
    return MILESTONE_DESCRIPTIONS.get(
        milestone, f"Backlog milestone for {milestone.lower()} work."
    )


def format_list(values: Sequence[str]) -> str:
    if not values:
        return "None"
    return ", ".join(f"`{value}`" for value in values)


def format_optional(value: str) -> str:
    return value if value else "None recorded."


def build_execution_stance(item: BacklogItem) -> str:
    parts = [
        STATUS_GUIDANCE[item.status],
        EXECUTION_MODE_GUIDANCE[item.execution_mode],
        WAVE_GUIDANCE[item.wave],
    ]
    if item.prototype_basis:
        parts.append(f"Current prototype basis: {item.prototype_basis}")
    return " ".join(parts)


def build_issue_body(item: BacklogItem, source_csv: Path) -> str:
    prototype_line = f"- Prototype basis: {format_optional(item.prototype_basis)}"
    return textwrap.dedent(
        f"""\
        {item.marker}
        # {item.issue_title}

        Generated from `{source_csv.as_posix()}`. Treat the backlog CSV and companion backlog doc as the current planning source for this task.

        ## Current Baseline
        - Status: `{item.status}`
        - Wave: `{item.wave}`
        - Execution mode: `{item.execution_mode}`
        {prototype_line}

        ## Implementation Stance
        {build_execution_stance(item)}

        ## Summary
        {item.summary}

        ## Acceptance Criteria
        {item.acceptance}

        ## Backlog Metadata
        - Milestone: `{item.milestone}`
        - Package: `{item.package}`
        - Path: `{item.path}`
        - Type: `{item.type}`
        - Owner lane: `{item.owner}`
        - Suggested executor: `{item.executor}`
        - Estimate: `{item.estimate}`
        - Depends on: {format_list(item.depends_on)}
        - Labels: {format_list(item.labels)}
        """
    ).strip() + "\n"


def render_seed_script(
    labels: Sequence[Dict[str, str]],
    milestones: Sequence[Dict[str, str]],
    source_csv: Path,
) -> str:
    labels_json = json.dumps(labels, indent=2)
    milestones_json = json.dumps(milestones, indent=2)
    source = source_csv.as_posix()
    return (
        f"# Generated from {source}. Do not edit manually.\n"
        "[CmdletBinding()]\n"
        "param(\n"
        "  [Parameter(Mandatory = $true)]\n"
        "  [string]$Repo,\n\n"
        "  [switch]$DryRun\n"
        ")\n\n"
        '$ErrorActionPreference = "Stop"\n\n'
        "if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {\n"
        '  throw "gh CLI is required but was not found on PATH."\n'
        "}\n\n"
        "$labelsJson = @'\n"
        f"{labels_json}\n"
        "'@\n\n"
        "$milestonesJson = @'\n"
        f"{milestones_json}\n"
        "'@\n\n"
        "$labels = $labelsJson | ConvertFrom-Json -Depth 8\n"
        "$milestones = $milestonesJson | ConvertFrom-Json -Depth 8\n\n"
        'Write-Host "Seeding labels for $Repo"\n'
        '$existingLabels = gh api "repos/$Repo/labels?per_page=100" | ConvertFrom-Json\n'
        "$labelLookup = @{}\n"
        "foreach ($existingLabel in $existingLabels) {\n"
        "  $labelLookup[$existingLabel.name] = $existingLabel\n"
        "}\n\n"
        "foreach ($label in $labels) {\n"
        "  if ($labelLookup.ContainsKey($label.name)) {\n"
        '    $endpoint = "repos/$Repo/labels/$([System.Uri]::EscapeDataString($label.name))"\n'
        "    $args = @(\n"
        '      "api",\n'
        "      $endpoint,\n"
        '      "--method", "PATCH",\n'
        '      "-f", "new_name=$($label.name)",\n'
        '      "-f", "color=$($label.color)",\n'
        '      "-f", "description=$($label.description)"\n'
        "    )\n"
        '    $action = "update"\n'
        "  } else {\n"
        "    $args = @(\n"
        '      "api",\n'
        '      "repos/$Repo/labels",\n'
        '      "--method", "POST",\n'
        '      "-f", "name=$($label.name)",\n'
        '      "-f", "color=$($label.color)",\n'
        '      "-f", "description=$($label.description)"\n'
        "    )\n"
        '    $action = "create"\n'
        "  }\n\n"
        "  if ($DryRun) {\n"
        '    Write-Host ("DRY RUN [$action label] gh " + ($args -join " "))\n'
        "  } else {\n"
        "    & gh @args | Out-Null\n"
        '    Write-Host ("$action label: " + $label.name)\n'
        "  }\n"
        "}\n\n"
        'Write-Host "Seeding milestones for $Repo"\n'
        '$existingMilestones = gh api "repos/$Repo/milestones?state=all&per_page=100" | ConvertFrom-Json\n'
        "$milestoneLookup = @{}\n"
        "foreach ($existingMilestone in $existingMilestones) {\n"
        "  $milestoneLookup[$existingMilestone.title] = $existingMilestone\n"
        "}\n\n"
        "foreach ($milestone in $milestones) {\n"
        "  if ($milestoneLookup.ContainsKey($milestone.title)) {\n"
        "    $number = $milestoneLookup[$milestone.title].number\n"
        "    $args = @(\n"
        '      "api",\n'
        '      "repos/$Repo/milestones/$number",\n'
        '      "--method", "PATCH",\n'
        '      "-f", "title=$($milestone.title)",\n'
        '      "-f", "description=$($milestone.description)",\n'
        '      "-f", "state=open"\n'
        "    )\n"
        '    $action = "update"\n'
        "  } else {\n"
        "    $args = @(\n"
        '      "api",\n'
        '      "repos/$Repo/milestones",\n'
        '      "--method", "POST",\n'
        '      "-f", "title=$($milestone.title)",\n'
        '      "-f", "description=$($milestone.description)"\n'
        "    )\n"
        '    $action = "create"\n'
        "  }\n\n"
        "  if ($DryRun) {\n"
        '    Write-Host ("DRY RUN [$action milestone] gh " + ($args -join " "))\n'
        "  } else {\n"
        "    & gh @args | Out-Null\n"
        '    Write-Host ("$action milestone: " + $milestone.title)\n'
        "  }\n"
        "}\n"
    )


def render_issue_script(issues: Sequence[Dict[str, object]], source_csv: Path) -> str:
    issues_json = json.dumps(issues, indent=2)
    source = source_csv.as_posix()
    return (
        f"# Generated from {source}. Do not edit manually.\n"
        "[CmdletBinding()]\n"
        "param(\n"
        "  [Parameter(Mandatory = $true)]\n"
        "  [string]$Repo,\n\n"
        "  [switch]$DryRun\n"
        ")\n\n"
        '$ErrorActionPreference = "Stop"\n\n'
        "if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {\n"
        '  throw "gh CLI is required but was not found on PATH."\n'
        "}\n\n"
        "$issuesJson = @'\n"
        f"{issues_json}\n"
        "'@\n\n"
        "$issues = $issuesJson | ConvertFrom-Json -Depth 8\n"
        '$existingIssues = gh issue list --repo $Repo --state all --limit 1000 --json number,title,body | ConvertFrom-Json\n'
        "$existingTitleMap = @{}\n"
        "$existingBacklogMap = @{}\n"
        "$markerPattern = '<!--\\s*backlog-id:\\s*([A-Za-z0-9._-]+)\\s*-->'\n"
        "foreach ($existingIssue in $existingIssues) {\n"
        "  $existingTitleMap[$existingIssue.title] = $existingIssue.number\n"
        "  $match = [regex]::Match($existingIssue.body, $markerPattern)\n"
        "  if ($match.Success) {\n"
        "    $backlogId = $match.Groups[1].Value\n"
        "    if ($existingBacklogMap.ContainsKey($backlogId)) {\n"
        '      throw "Duplicate backlog marker found for $backlogId in repo $Repo."\n'
        "    }\n"
        "    $existingBacklogMap[$backlogId] = $existingIssue.number\n"
        "  }\n"
        "}\n\n"
        "foreach ($issue in $issues) {\n"
        "  if ($existingBacklogMap.ContainsKey($issue.id)) {\n"
        '    Write-Host ("skip existing backlog issue #" + $existingBacklogMap[$issue.id] + ": " + $issue.id)\n'
        "    continue\n"
        "  }\n"
        "  if ($existingTitleMap.ContainsKey($issue.title)) {\n"
        '    throw "Title collision without matching backlog marker for $($issue.id): $($issue.title)"\n'
        "  }\n\n"
        "  $bodyPath = Join-Path $PSScriptRoot $issue.body_path\n"
        "  if (-not (Test-Path $bodyPath)) {\n"
        '    throw "Issue body file not found: $bodyPath"\n'
        "  }\n\n"
        "  $args = @(\n"
        '    "issue", "create",\n'
        '    "--repo", $Repo,\n'
        '    "--title", $issue.title,\n'
        '    "--body-file", $bodyPath\n'
        "  )\n\n"
        "  if ($issue.milestone) {\n"
        '    $args += @("--milestone", $issue.milestone)\n'
        "  }\n\n"
        "  foreach ($label in $issue.labels) {\n"
        '    $args += @("--label", $label)\n'
        "  }\n\n"
        "  if ($DryRun) {\n"
        '    Write-Host ("DRY RUN [create issue] gh " + ($args -join " "))\n'
        "  } else {\n"
        "    & gh @args | Out-Host\n"
        "  }\n"
        "}\n"
    )


def build_label_rows(items: Sequence[BacklogItem]) -> List[Dict[str, str]]:
    labels = ordered_unique(label for item in items for label in item.labels)
    return [
        {
            "name": label,
            "color": label_color(label),
            "description": label_description(label),
        }
        for label in labels
    ]


def build_milestone_rows(items: Sequence[BacklogItem]) -> List[Dict[str, str]]:
    milestones = ordered_unique(item.milestone for item in items)
    return [
        {
            "title": milestone,
            "description": milestone_description(milestone),
        }
        for milestone in milestones
    ]


def build_issue_rows(items: Sequence[BacklogItem]) -> List[Dict[str, object]]:
    return [
        {
            "id": item.id,
            "title": item.issue_title,
            "milestone": item.milestone,
            "wave": item.wave,
            "status": item.status,
            "execution_mode": item.execution_mode,
            "prototype_basis": item.prototype_basis,
            "labels": list(item.labels),
            "depends_on": list(item.depends_on),
            "body_path": f"issue-bodies/{item.id}.md",
        }
        for item in items
    ]


def build_manifest(
    items: Sequence[BacklogItem],
    labels: Sequence[Dict[str, str]],
    milestones: Sequence[Dict[str, str]],
    issues: Sequence[Dict[str, object]],
    source_csv: Path,
) -> Dict[str, object]:
    status_counts = Counter(item.status for item in items)
    wave_counts = Counter(item.wave for item in items)
    return {
        "source_csv": source_csv.as_posix(),
        "labels": list(labels),
        "milestones": list(milestones),
        "issues": list(issues),
        "summary": {
            "issue_count": len(items),
            "status_counts": dict(status_counts),
            "wave_counts": dict(wave_counts),
            "partial_ids": [item.id for item in items if item.status == "partial"],
        },
    }


def main() -> None:
    args = parse_args()
    repo_root = Path.cwd()
    csv_path = (repo_root / args.csv).resolve()
    output_dir = (repo_root / args.output_dir).resolve()
    issue_bodies_dir = output_dir / "issue-bodies"

    items = load_backlog(csv_path)
    validate_backlog(items)

    label_rows = build_label_rows(items)
    milestone_rows = build_milestone_rows(items)
    issue_rows = build_issue_rows(items)
    manifest = build_manifest(
        items,
        label_rows,
        milestone_rows,
        issue_rows,
        csv_path.relative_to(repo_root),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    issue_bodies_dir.mkdir(parents=True, exist_ok=True)

    source_csv = csv_path.relative_to(repo_root)
    for item in items:
        body = build_issue_body(item, source_csv)
        (issue_bodies_dir / f"{item.id}.md").write_text(body, encoding="utf-8")

    seed_script = render_seed_script(label_rows, milestone_rows, source_csv)
    issue_script = render_issue_script(issue_rows, source_csv)

    (output_dir / "seed_labels_and_milestones.ps1").write_text(
        seed_script, encoding="utf-8"
    )
    (output_dir / "create_backlog_issues.ps1").write_text(
        issue_script, encoding="utf-8"
    )
    (output_dir / "backlog_seed_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    status_summary = ", ".join(
        f"{key}={value}" for key, value in sorted(manifest["summary"]["status_counts"].items())
    )
    wave_summary = ", ".join(
        f"{key}={value}" for key, value in sorted(manifest["summary"]["wave_counts"].items())
    )

    print(f"Loaded {len(items)} backlog items from {csv_path}")
    print(f"Status counts: {status_summary}")
    print(f"Wave counts: {wave_summary}")
    print(f"Wrote label/milestone seed script to {output_dir / 'seed_labels_and_milestones.ps1'}")
    print(f"Wrote issue creation script to {output_dir / 'create_backlog_issues.ps1'}")
    print(f"Wrote backlog manifest to {output_dir / 'backlog_seed_manifest.json'}")
    print(f"Wrote {len(issue_rows)} issue body files to {issue_bodies_dir}")


if __name__ == "__main__":
    main()
