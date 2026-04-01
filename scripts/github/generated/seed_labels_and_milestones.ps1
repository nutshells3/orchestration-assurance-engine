# Generated from docs/FORMAL_CLAIM_MONOREPO_BACKLOG.csv. Do not edit manually.
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Repo,

  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  throw "gh CLI is required but was not found on PATH."
}

$labelsJson = @'
[
  {
    "name": "infra",
    "color": "1f6feb",
    "description": "Backlog label for infra work."
  },
  {
    "name": "monorepo",
    "color": "1f6feb",
    "description": "Backlog label for monorepo work."
  },
  {
    "name": "devex",
    "color": "1f6feb",
    "description": "Backlog label for devex work."
  },
  {
    "name": "governance",
    "color": "1f6feb",
    "description": "Backlog label for governance work."
  },
  {
    "name": "github",
    "color": "1f6feb",
    "description": "Backlog label for github work."
  },
  {
    "name": "docs",
    "color": "a2eeef",
    "description": "Backlog label for docs work."
  },
  {
    "name": "contracts",
    "color": "0e8a16",
    "description": "Backlog label for contracts work."
  },
  {
    "name": "schema",
    "color": "0052cc",
    "description": "Backlog label for schema work."
  },
  {
    "name": "policy",
    "color": "0e8a16",
    "description": "Backlog label for policy work."
  },
  {
    "name": "codegen",
    "color": "0052cc",
    "description": "Backlog label for codegen work."
  },
  {
    "name": "python",
    "color": "0052cc",
    "description": "Backlog label for python work."
  },
  {
    "name": "typescript",
    "color": "0052cc",
    "description": "Backlog label for typescript work."
  },
  {
    "name": "tests",
    "color": "fbca04",
    "description": "Backlog label for tests work."
  },
  {
    "name": "fixtures",
    "color": "fbca04",
    "description": "Backlog label for fixtures work."
  },
  {
    "name": "migration",
    "color": "0052cc",
    "description": "Backlog label for migration work."
  },
  {
    "name": "engine",
    "color": "0052cc",
    "description": "Backlog label for engine work."
  },
  {
    "name": "isabelle",
    "color": "5319e7",
    "description": "Backlog label for isabelle work."
  },
  {
    "name": "backend",
    "color": "0052cc",
    "description": "Backlog label for backend work."
  },
  {
    "name": "mcp",
    "color": "0052cc",
    "description": "Backlog label for mcp work."
  },
  {
    "name": "claim-tracer",
    "color": "a2eeef",
    "description": "Backlog label for claim tracer work."
  },
  {
    "name": "artifacts",
    "color": "0052cc",
    "description": "Backlog label for artifacts work."
  },
  {
    "name": "storage",
    "color": "0052cc",
    "description": "Backlog label for storage work."
  },
  {
    "name": "graph",
    "color": "0052cc",
    "description": "Backlog label for graph work."
  },
  {
    "name": "domain",
    "color": "0052cc",
    "description": "Backlog label for domain work."
  },
  {
    "name": "library",
    "color": "0052cc",
    "description": "Backlog label for library work."
  },
  {
    "name": "audit",
    "color": "0e8a16",
    "description": "Backlog label for audit work."
  },
  {
    "name": "trust-frontier",
    "color": "0e8a16",
    "description": "Backlog label for trust frontier work."
  },
  {
    "name": "deterministic",
    "color": "0e8a16",
    "description": "Backlog label for deterministic work."
  },
  {
    "name": "vacuity",
    "color": "0e8a16",
    "description": "Backlog label for vacuity work."
  },
  {
    "name": "intent",
    "color": "0e8a16",
    "description": "Backlog label for intent work."
  },
  {
    "name": "downstream",
    "color": "0e8a16",
    "description": "Backlog label for downstream work."
  },
  {
    "name": "events",
    "color": "0052cc",
    "description": "Backlog label for events work."
  },
  {
    "name": "versioning",
    "color": "0052cc",
    "description": "Backlog label for versioning work."
  },
  {
    "name": "runner",
    "color": "5319e7",
    "description": "Backlog label for runner work."
  },
  {
    "name": "workspace",
    "color": "5319e7",
    "description": "Backlog label for workspace work."
  },
  {
    "name": "build",
    "color": "5319e7",
    "description": "Backlog label for build work."
  },
  {
    "name": "logs",
    "color": "5319e7",
    "description": "Backlog label for logs work."
  },
  {
    "name": "export",
    "color": "5319e7",
    "description": "Backlog label for export work."
  },
  {
    "name": "dump",
    "color": "5319e7",
    "description": "Backlog label for dump work."
  },
  {
    "name": "dependencies",
    "color": "5319e7",
    "description": "Backlog label for dependencies work."
  },
  {
    "name": "oracles",
    "color": "5319e7",
    "description": "Backlog label for oracles work."
  },
  {
    "name": "nitpick",
    "color": "5319e7",
    "description": "Backlog label for nitpick work."
  },
  {
    "name": "sledgehammer",
    "color": "5319e7",
    "description": "Backlog label for sledgehammer work."
  },
  {
    "name": "adversarial",
    "color": "cfd8dc",
    "description": "Backlog label for adversarial work."
  },
  {
    "name": "robustness",
    "color": "5319e7",
    "description": "Backlog label for robustness work."
  },
  {
    "name": "cli",
    "color": "0052cc",
    "description": "Backlog label for cli work."
  },
  {
    "name": "ops",
    "color": "0052cc",
    "description": "Backlog label for ops work."
  },
  {
    "name": "integration",
    "color": "fbca04",
    "description": "Backlog label for integration work."
  },
  {
    "name": "sqlite",
    "color": "0052cc",
    "description": "Backlog label for sqlite work."
  },
  {
    "name": "planner",
    "color": "c2e0c6",
    "description": "Backlog label for planner work."
  },
  {
    "name": "workflow",
    "color": "0052cc",
    "description": "Backlog label for workflow work."
  },
  {
    "name": "formalization",
    "color": "c2e0c6",
    "description": "Backlog label for formalization work."
  },
  {
    "name": "state-machine",
    "color": "0052cc",
    "description": "Backlog label for state machine work."
  },
  {
    "name": "promotion",
    "color": "0e8a16",
    "description": "Backlog label for promotion work."
  },
  {
    "name": "ingestion",
    "color": "a2eeef",
    "description": "Backlog label for ingestion work."
  },
  {
    "name": "documents",
    "color": "a2eeef",
    "description": "Backlog label for documents work."
  },
  {
    "name": "api",
    "color": "0052cc",
    "description": "Backlog label for api work."
  },
  {
    "name": "service",
    "color": "0052cc",
    "description": "Backlog label for service work."
  },
  {
    "name": "agents",
    "color": "c2e0c6",
    "description": "Backlog label for agents work."
  },
  {
    "name": "prompts",
    "color": "c2e0c6",
    "description": "Backlog label for prompts work."
  },
  {
    "name": "providers",
    "color": "c2e0c6",
    "description": "Backlog label for providers work."
  },
  {
    "name": "platform",
    "color": "0052cc",
    "description": "Backlog label for platform work."
  },
  {
    "name": "reliability",
    "color": "cfd8dc",
    "description": "Backlog label for reliability work."
  },
  {
    "name": "ux",
    "color": "d93f0b",
    "description": "Backlog label for ux work."
  },
  {
    "name": "e2e",
    "color": "fbca04",
    "description": "Backlog label for e2e work."
  },
  {
    "name": "desktop",
    "color": "d93f0b",
    "description": "Backlog label for desktop work."
  },
  {
    "name": "tauri",
    "color": "d93f0b",
    "description": "Backlog label for tauri work."
  },
  {
    "name": "frontend",
    "color": "d93f0b",
    "description": "Backlog label for frontend work."
  },
  {
    "name": "react-flow",
    "color": "d93f0b",
    "description": "Backlog label for react flow work."
  },
  {
    "name": "editor",
    "color": "d93f0b",
    "description": "Backlog label for editor work."
  },
  {
    "name": "run-control",
    "color": "cfd8dc",
    "description": "Backlog label for run control work."
  },
  {
    "name": "assurance",
    "color": "0e8a16",
    "description": "Backlog label for assurance work."
  },
  {
    "name": "timeline",
    "color": "1f6feb",
    "description": "Backlog label for timeline work."
  },
  {
    "name": "diff",
    "color": "a2eeef",
    "description": "Backlog label for diff work."
  },
  {
    "name": "runtime",
    "color": "cfd8dc",
    "description": "Backlog label for runtime work."
  },
  {
    "name": "guardrails",
    "color": "cfd8dc",
    "description": "Backlog label for guardrails work."
  },
  {
    "name": "evidence",
    "color": "a2eeef",
    "description": "Backlog label for evidence work."
  },
  {
    "name": "citations",
    "color": "a2eeef",
    "description": "Backlog label for citations work."
  },
  {
    "name": "experiments",
    "color": "a2eeef",
    "description": "Backlog label for experiments work."
  },
  {
    "name": "metrics",
    "color": "a2eeef",
    "description": "Backlog label for metrics work."
  },
  {
    "name": "traceability",
    "color": "0e8a16",
    "description": "Backlog label for traceability work."
  },
  {
    "name": "regression",
    "color": "fbca04",
    "description": "Backlog label for regression work."
  },
  {
    "name": "scenarios",
    "color": "fbca04",
    "description": "Backlog label for scenarios work."
  },
  {
    "name": "codex",
    "color": "c2e0c6",
    "description": "Backlog label for codex work."
  },
  {
    "name": "claude-code",
    "color": "c2e0c6",
    "description": "Backlog label for claude code work."
  },
  {
    "name": "skills",
    "color": "c2e0c6",
    "description": "Backlog label for skills work."
  },
  {
    "name": "release",
    "color": "1f6feb",
    "description": "Backlog label for release work."
  },
  {
    "name": "packaging",
    "color": "1f6feb",
    "description": "Backlog label for packaging work."
  }
]
'@

$milestonesJson = @'
[
  {
    "title": "M0 Foundation",
    "description": "Set up repository boundaries, governance, contracts, and fixture baselines."
  },
  {
    "title": "M1 Migration",
    "description": "Move the uploaded skeletons into the target service and package layout."
  },
  {
    "title": "M2 Core Domain",
    "description": "Implement canonical graph, audit, policy, contract, and revision logic."
  },
  {
    "title": "M3 Isabelle Runner",
    "description": "Build deterministic Isabelle execution, export, and audit services."
  },
  {
    "title": "M4 Engine Workflows",
    "description": "Compose claim structuring, formalization, audit, promotion, and ingest workflows."
  },
  {
    "title": "M5 MCP",
    "description": "Expose the stable MCP surface over the canonical engine and proof runner."
  },
  {
    "title": "M6 CLI",
    "description": "Ship the human-facing CLI for bootstrap, audit, and inspection workflows."
  },
  {
    "title": "M7 Desktop",
    "description": "Build the standalone desktop shell and graph-first inspection workflows."
  },
  {
    "title": "M8 Evidence",
    "description": "Add external evidence connectors, provenance capture, and review flows."
  },
  {
    "title": "M9 Hardening",
    "description": "Close testing, packaging, release, documentation, and operational hardening gaps."
  }
]
'@

$labels = $labelsJson | ConvertFrom-Json -Depth 8
$milestones = $milestonesJson | ConvertFrom-Json -Depth 8

Write-Host "Seeding labels for $Repo"
$existingLabels = gh api "repos/$Repo/labels?per_page=100" | ConvertFrom-Json
$labelLookup = @{}
foreach ($existingLabel in $existingLabels) {
  $labelLookup[$existingLabel.name] = $existingLabel
}

foreach ($label in $labels) {
  if ($labelLookup.ContainsKey($label.name)) {
    $endpoint = "repos/$Repo/labels/$([System.Uri]::EscapeDataString($label.name))"
    $args = @(
      "api",
      $endpoint,
      "--method", "PATCH",
      "-f", "new_name=$($label.name)",
      "-f", "color=$($label.color)",
      "-f", "description=$($label.description)"
    )
    $action = "update"
  } else {
    $args = @(
      "api",
      "repos/$Repo/labels",
      "--method", "POST",
      "-f", "name=$($label.name)",
      "-f", "color=$($label.color)",
      "-f", "description=$($label.description)"
    )
    $action = "create"
  }

  if ($DryRun) {
    Write-Host ("DRY RUN [$action label] gh " + ($args -join " "))
  } else {
    & gh @args | Out-Null
    Write-Host ("$action label: " + $label.name)
  }
}

Write-Host "Seeding milestones for $Repo"
$existingMilestones = gh api "repos/$Repo/milestones?state=all&per_page=100" | ConvertFrom-Json
$milestoneLookup = @{}
foreach ($existingMilestone in $existingMilestones) {
  $milestoneLookup[$existingMilestone.title] = $existingMilestone
}

foreach ($milestone in $milestones) {
  if ($milestoneLookup.ContainsKey($milestone.title)) {
    $number = $milestoneLookup[$milestone.title].number
    $args = @(
      "api",
      "repos/$Repo/milestones/$number",
      "--method", "PATCH",
      "-f", "title=$($milestone.title)",
      "-f", "description=$($milestone.description)",
      "-f", "state=open"
    )
    $action = "update"
  } else {
    $args = @(
      "api",
      "repos/$Repo/milestones",
      "--method", "POST",
      "-f", "title=$($milestone.title)",
      "-f", "description=$($milestone.description)"
    )
    $action = "create"
  }

  if ($DryRun) {
    Write-Host ("DRY RUN [$action milestone] gh " + ($args -join " "))
  } else {
    & gh @args | Out-Null
    Write-Host ("$action milestone: " + $milestone.title)
  }
}
