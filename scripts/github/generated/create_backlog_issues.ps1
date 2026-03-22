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

$issuesJson = @'
[
  {
    "id": "M0-01",
    "title": "[M0-01] Initialize polyglot monorepo tooling (pnpm + uv + just + pre-commit)",
    "milestone": "M0 Foundation",
    "wave": "foundation",
    "status": "done",
    "execution_mode": "bootstrap",
    "prototype_basis": "Canonical monorepo root, toolchain entrypoints, lockfiles, and just bootstrap/test/lint scaffolding now exist at the repo root.",
    "labels": [
      "infra",
      "monorepo",
      "devex"
    ],
    "depends_on": [],
    "body_path": "issue-bodies/M0-01.md"
  },
  {
    "id": "M0-02",
    "title": "[M0-02] Set repository governance: CODEOWNERS, labels, milestones, issue templates, PR template",
    "milestone": "M0 Foundation",
    "wave": "foundation",
    "status": "done",
    "execution_mode": "absorb",
    "prototype_basis": "Labels, milestones, issue-seeding automation, CODEOWNERS, issue templates, PR template, and GitHub governance docs now exist in the canonical root.",
    "labels": [
      "governance",
      "github",
      "docs"
    ],
    "depends_on": [
      "M0-01"
    ],
    "body_path": "issue-bodies/M0-02.md"
  },
  {
    "id": "M0-03",
    "title": "[M0-03] Import canonical JSON Schemas and freeze schema version policy",
    "milestone": "M0 Foundation",
    "wave": "foundation",
    "status": "done",
    "execution_mode": "normalize",
    "prototype_basis": "Prototype schema copies already exist under `formal-claim-pipeline/formal_claim_pipeline/schemas/`.",
    "labels": [
      "contracts",
      "schema",
      "policy"
    ],
    "depends_on": [
      "M0-01"
    ],
    "body_path": "issue-bodies/M0-03.md"
  },
  {
    "id": "M0-04",
    "title": "[M0-04] Generate Python and TypeScript bindings from canonical schemas",
    "milestone": "M0 Foundation",
    "wave": "foundation",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "contracts",
      "codegen",
      "python",
      "typescript"
    ],
    "depends_on": [
      "M0-03"
    ],
    "body_path": "issue-bodies/M0-04.md"
  },
  {
    "id": "M0-05",
    "title": "[M0-05] Add golden fixtures and schema conformance tests",
    "milestone": "M0 Foundation",
    "wave": "foundation",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "tests",
      "schema",
      "fixtures"
    ],
    "depends_on": [
      "M0-04"
    ],
    "body_path": "issue-bodies/M0-05.md"
  },
  {
    "id": "M1-01",
    "title": "[M1-01] Extract `formal_claim_pipeline` into `services/engine` package skeleton",
    "milestone": "M1 Migration",
    "wave": "migration",
    "status": "done",
    "execution_mode": "extract",
    "prototype_basis": "The donor `formal_claim_pipeline` package already exists under `formal-claim-pipeline/formal_claim_pipeline/`.",
    "labels": [
      "migration",
      "engine",
      "python"
    ],
    "depends_on": [
      "M0-04"
    ],
    "body_path": "issue-bodies/M1-01.md"
  },
  {
    "id": "M1-02",
    "title": "[M1-02] Extract `isabelle_wrapper.py` into deterministic Isabelle runner service",
    "milestone": "M1 Migration",
    "wave": "migration",
    "status": "done",
    "execution_mode": "extract",
    "prototype_basis": "The heuristic donor wrapper already exists at `formal-claim-pipeline/formal_claim_pipeline/isabelle_wrapper.py`.",
    "labels": [
      "migration",
      "isabelle",
      "backend"
    ],
    "depends_on": [
      "M1-01"
    ],
    "body_path": "issue-bodies/M1-02.md"
  },
  {
    "id": "M1-03",
    "title": "[M1-03] Replace standalone `claim_tracer_mcp` internals with thin MCP facade over canonical engine",
    "milestone": "M1 Migration",
    "wave": "migration",
    "status": "done",
    "execution_mode": "absorb",
    "prototype_basis": "The donor MCP surface already exists under `claim-tracer-dist/claim_tracer_mcp/`.",
    "labels": [
      "migration",
      "mcp",
      "claim-tracer"
    ],
    "depends_on": [
      "M1-01",
      "M0-04"
    ],
    "body_path": "issue-bodies/M1-03.md"
  },
  {
    "id": "M1-04",
    "title": "[M1-04] Add migration adapters for legacy file-backed artifacts into canonical store",
    "milestone": "M1 Migration",
    "wave": "migration",
    "status": "done",
    "execution_mode": "replace",
    "prototype_basis": "",
    "labels": [
      "migration",
      "artifacts",
      "storage"
    ],
    "depends_on": [
      "M1-01",
      "M0-05"
    ],
    "body_path": "issue-bodies/M1-04.md"
  },
  {
    "id": "M2-01",
    "title": "[M2-01] Implement canonical graph query library for claims, relations, and assurance links",
    "milestone": "M2 Core Domain",
    "wave": "core-runner-base",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "graph",
      "domain",
      "library"
    ],
    "depends_on": [
      "M0-04"
    ],
    "body_path": "issue-bodies/M2-01.md"
  },
  {
    "id": "M2-02",
    "title": "[M2-02] Implement theorem-local trust frontier and hotspot extraction",
    "milestone": "M2 Core Domain",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "graph",
      "audit",
      "trust-frontier"
    ],
    "depends_on": [
      "M2-01",
      "M3-04"
    ],
    "body_path": "issue-bodies/M2-02.md"
  },
  {
    "id": "M2-03",
    "title": "[M2-03] Implement deterministic assurance-profile computation rules",
    "milestone": "M2 Core Domain",
    "wave": "core-runner-base",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "audit",
      "policy",
      "deterministic"
    ],
    "depends_on": [
      "M0-04",
      "M2-01"
    ],
    "body_path": "issue-bodies/M2-03.md"
  },
  {
    "id": "M2-04",
    "title": "[M2-04] Add vacuity, countermodel, and intent-alignment status combinators",
    "milestone": "M2 Core Domain",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "audit",
      "vacuity",
      "intent"
    ],
    "depends_on": [
      "M2-03"
    ],
    "body_path": "issue-bodies/M2-04.md"
  },
  {
    "id": "M2-05",
    "title": "[M2-05] Implement Contract Pack emitter and downstream policy projection",
    "milestone": "M2 Core Domain",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "contracts",
      "downstream",
      "policy"
    ],
    "depends_on": [
      "M2-03"
    ],
    "body_path": "issue-bodies/M2-05.md"
  },
  {
    "id": "M2-06",
    "title": "[M2-06] Add artifact versioning, migrations, and review-event journal",
    "milestone": "M2 Core Domain",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "storage",
      "events",
      "versioning"
    ],
    "depends_on": [
      "M1-04"
    ],
    "body_path": "issue-bodies/M2-06.md"
  },
  {
    "id": "M3-01",
    "title": "[M3-01] Implement workspace/session scaffolder for temporary Isabelle runs",
    "milestone": "M3 Isabelle Runner",
    "wave": "core-runner-base",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "isabelle",
      "runner",
      "workspace"
    ],
    "depends_on": [
      "M1-02"
    ],
    "body_path": "issue-bodies/M3-01.md"
  },
  {
    "id": "M3-02",
    "title": "[M3-02] Implement build runner with structured log capture and session fingerprinting",
    "milestone": "M3 Isabelle Runner",
    "wave": "core-runner-base",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "isabelle",
      "build",
      "logs"
    ],
    "depends_on": [
      "M3-01"
    ],
    "body_path": "issue-bodies/M3-02.md"
  },
  {
    "id": "M3-03",
    "title": "[M3-03] Parse `isabelle export` and `isabelle dump` into typed audit payloads",
    "milestone": "M3 Isabelle Runner",
    "wave": "core-runner-base",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "isabelle",
      "export",
      "dump"
    ],
    "depends_on": [
      "M3-02"
    ],
    "body_path": "issue-bodies/M3-03.md"
  },
  {
    "id": "M3-04",
    "title": "[M3-04] Implement theorem-local dependency, oracle, and reviewed-exception extraction",
    "milestone": "M3 Isabelle Runner",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "isabelle",
      "dependencies",
      "oracles"
    ],
    "depends_on": [
      "M3-03"
    ],
    "body_path": "issue-bodies/M3-04.md"
  },
  {
    "id": "M3-05",
    "title": "[M3-05] Integrate Nitpick and optional Sledgehammer probes",
    "milestone": "M3 Isabelle Runner",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "isabelle",
      "nitpick",
      "sledgehammer"
    ],
    "depends_on": [
      "M3-02"
    ],
    "body_path": "issue-bodies/M3-05.md"
  },
  {
    "id": "M3-06",
    "title": "[M3-06] Implement premise-deletion and conclusion-perturbation harness",
    "milestone": "M3 Isabelle Runner",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "isabelle",
      "adversarial",
      "robustness"
    ],
    "depends_on": [
      "M3-05"
    ],
    "body_path": "issue-bodies/M3-06.md"
  },
  {
    "id": "M3-07",
    "title": "[M3-07] Expose runner subcommands: build, export, dump, audit, profile",
    "milestone": "M3 Isabelle Runner",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "cli",
      "isabelle",
      "ops"
    ],
    "depends_on": [
      "M3-03",
      "M2-03"
    ],
    "body_path": "issue-bodies/M3-07.md"
  },
  {
    "id": "M3-08",
    "title": "[M3-08] Create Isabelle integration fixtures and reproducible runner tests",
    "milestone": "M3 Isabelle Runner",
    "wave": "core-runner-advanced",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "tests",
      "isabelle",
      "integration"
    ],
    "depends_on": [
      "M3-07"
    ],
    "body_path": "issue-bodies/M3-08.md"
  },
  {
    "id": "M4-01",
    "title": "[M4-01] Implement canonical repository layer over SQLite with file export mirrors",
    "milestone": "M4 Engine Workflows",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "engine",
      "storage",
      "sqlite"
    ],
    "depends_on": [
      "M0-05",
      "M2-06"
    ],
    "body_path": "issue-bodies/M4-01.md"
  },
  {
    "id": "M4-02",
    "title": "[M4-02] Implement claim-structuring workflow and planner admission pipeline",
    "milestone": "M4 Engine Workflows",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "engine",
      "planner",
      "workflow"
    ],
    "depends_on": [
      "M1-01",
      "M4-01"
    ],
    "body_path": "issue-bodies/M4-02.md"
  },
  {
    "id": "M4-03",
    "title": "[M4-03] Implement dual-formalization workflow with divergence capture",
    "milestone": "M4 Engine Workflows",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "engine",
      "formalization",
      "workflow"
    ],
    "depends_on": [
      "M4-02",
      "M3-02"
    ],
    "body_path": "issue-bodies/M4-03.md"
  },
  {
    "id": "M4-04",
    "title": "[M4-04] Implement audit workflow that composes runner outputs and deterministic rules",
    "milestone": "M4 Engine Workflows",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "engine",
      "audit",
      "workflow"
    ],
    "depends_on": [
      "M4-03",
      "M2-03",
      "M3-06"
    ],
    "body_path": "issue-bodies/M4-04.md"
  },
  {
    "id": "M4-05",
    "title": "[M4-05] Implement promotion state machine and review checkpoints",
    "milestone": "M4 Engine Workflows",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "engine",
      "state-machine",
      "promotion"
    ],
    "depends_on": [
      "M4-04"
    ],
    "body_path": "issue-bodies/M4-05.md"
  },
  {
    "id": "M4-06",
    "title": "[M4-06] Implement document-ingest adapter from claim-tracer concepts into canonical Claim Graph",
    "milestone": "M4 Engine Workflows",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "ingestion",
      "claim-tracer",
      "documents"
    ],
    "depends_on": [
      "M2-01",
      "M1-03"
    ],
    "body_path": "issue-bodies/M4-06.md"
  },
  {
    "id": "M4-07",
    "title": "[M4-07] Add engine API for project, artifact, audit, and promotion operations",
    "milestone": "M4 Engine Workflows",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "engine",
      "api",
      "service"
    ],
    "depends_on": [
      "M4-04",
      "M4-05"
    ],
    "body_path": "issue-bodies/M4-07.md"
  },
  {
    "id": "M4-08",
    "title": "[M4-08] Refactor prompts and agent adapters into pluggable provider modules",
    "milestone": "M4 Engine Workflows",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "agents",
      "prompts",
      "providers"
    ],
    "depends_on": [
      "M4-02"
    ],
    "body_path": "issue-bodies/M4-08.md"
  },
  {
    "id": "M5-01",
    "title": "[M5-01] Define narrow typed MCP tool/resource facade over FormalClaimEngineAPI",
    "milestone": "M5 MCP",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "`services/mcp-server` already existed from the donor MCP surface, but it had to be re-scoped into an engine-owned thin facade rather than a second workflow owner.",
    "labels": [
      "mcp",
      "api",
      "platform"
    ],
    "depends_on": [
      "M4-07",
      "M3-07"
    ],
    "body_path": "issue-bodies/M5-01.md"
  },
  {
    "id": "M5-02",
    "title": "[M5-02] Port selected `claim_tracer_mcp` ingest and query flows into canonical MCP endpoints",
    "milestone": "M5 MCP",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "Useful donor behaviors already existed in `claim_tracer_mcp`, but they needed to be absorbed into canonical dotted MCP endpoints without reviving legacy store/model ownership.",
    "labels": [
      "mcp",
      "claim-tracer",
      "ingestion"
    ],
    "depends_on": [
      "M5-01",
      "M4-06"
    ],
    "body_path": "issue-bodies/M5-02.md"
  },
  {
    "id": "M5-03",
    "title": "[M5-03] Expose proof/audit MCP workflow tools for engine-backed audit and profile recomputation",
    "milestone": "M5 MCP",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "Runner-backed audit and profile recomputation already existed behind engine workflows; MCP only needed to expose them as thin agent-facing control-plane calls.",
    "labels": [
      "mcp",
      "isabelle",
      "audit"
    ],
    "depends_on": [
      "M5-01",
      "M4-04"
    ],
    "body_path": "issue-bodies/M5-03.md"
  },
  {
    "id": "M5-04",
    "title": "[M5-04] Implement structured errors, request logging, queue limits, and MCP parity smokes",
    "milestone": "M5 MCP",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "Expected operational controls were missing from the donor MCP server and had to be added around the new canonical facade.",
    "labels": [
      "mcp",
      "ops",
      "reliability"
    ],
    "depends_on": [
      "M5-01"
    ],
    "body_path": "issue-bodies/M5-04.md"
  },
  {
    "id": "M6-01",
    "title": "[M6-01] Build human-facing CLI for project bootstrap, audit runs, and artifact inspection",
    "milestone": "M6 CLI",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "`apps/cli` existed only as a placeholder README; the canonical operator shell had to be created as a thin package over `FormalClaimEngineAPI`.",
    "labels": [
      "cli",
      "ux",
      "devex"
    ],
    "depends_on": [
      "M5-03",
      "M4-07"
    ],
    "body_path": "issue-bodies/M6-01.md"
  },
  {
    "id": "M6-02",
    "title": "[M6-02] Add CLI smoke tests and scripted operator scenarios",
    "milestone": "M6 CLI",
    "wave": "workflow-mcp-cli",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "No end-to-end operator coverage existed for the new CLI surface before this wave.",
    "labels": [
      "cli",
      "tests",
      "e2e"
    ],
    "depends_on": [
      "M6-01"
    ],
    "body_path": "issue-bodies/M6-02.md"
  },
  {
    "id": "M7-01",
    "title": "[M7-01] Initialize Tauri desktop shell with project browser and local process orchestration",
    "milestone": "M7 Desktop",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "desktop",
      "tauri",
      "frontend"
    ],
    "depends_on": [
      "M0-01",
      "M4-07"
    ],
    "body_path": "issue-bodies/M7-01.md"
  },
  {
    "id": "M7-02",
    "title": "[M7-02] Implement Claim Graph and Assurance Graph navigation with React Flow",
    "milestone": "M7 Desktop",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "desktop",
      "graph",
      "react-flow"
    ],
    "depends_on": [
      "M7-01",
      "M2-01"
    ],
    "body_path": "issue-bodies/M7-02.md"
  },
  {
    "id": "M7-03",
    "title": "[M7-03] Implement light theory editor and managed external Isabelle launch with budgeted run control",
    "milestone": "M7 Desktop",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "desktop",
      "isabelle",
      "editor",
      "run-control"
    ],
    "depends_on": [
      "M7-01",
      "M3-07",
      "M5-01"
    ],
    "body_path": "issue-bodies/M7-03.md"
  },
  {
    "id": "M7-04",
    "title": "[M7-04] Implement Assurance Profile inspector, gate UI, and promotion workflow",
    "milestone": "M7 Desktop",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "desktop",
      "assurance",
      "policy"
    ],
    "depends_on": [
      "M7-02",
      "M4-05"
    ],
    "body_path": "issue-bodies/M7-04.md"
  },
  {
    "id": "M7-05",
    "title": "[M7-05] Add artifact timeline, diff viewer, and review-event panel",
    "milestone": "M7 Desktop",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "desktop",
      "timeline",
      "diff"
    ],
    "depends_on": [
      "M7-04",
      "M2-06"
    ],
    "body_path": "issue-bodies/M7-05.md"
  },
  {
    "id": "M7-06",
    "title": "[M7-06] Add bounded backend run governor and stop controls for managed proof jobs",
    "milestone": "M7 Desktop",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "runtime",
      "isabelle",
      "mcp",
      "desktop",
      "guardrails"
    ],
    "depends_on": [
      "M7-03",
      "M5-01",
      "M5-02"
    ],
    "body_path": "issue-bodies/M7-06.md"
  },
  {
    "id": "M8-01",
    "title": "[M8-01] Implement local document import and citation-anchor normalization",
    "milestone": "M8 Evidence",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "evidence",
      "documents",
      "citations"
    ],
    "depends_on": [
      "M4-06"
    ],
    "body_path": "issue-bodies/M8-01.md"
  },
  {
    "id": "M8-02",
    "title": "[M8-02] Implement experiment/evaluation evidence adapter",
    "milestone": "M8 Evidence",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "evidence",
      "experiments",
      "metrics"
    ],
    "depends_on": [
      "M8-01"
    ],
    "body_path": "issue-bodies/M8-02.md"
  },
  {
    "id": "M8-03",
    "title": "[M8-03] Implement external reference registry and assurance-link browser",
    "milestone": "M8 Evidence",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "evidence",
      "assurance",
      "traceability"
    ],
    "depends_on": [
      "M8-01",
      "M7-02"
    ],
    "body_path": "issue-bodies/M8-03.md"
  },
  {
    "id": "M9-01",
    "title": "[M9-01] Add audit-rule property tests, regression corpus, and runaway-search stop cases",
    "milestone": "M9 Hardening",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "tests",
      "audit",
      "regression",
      "runtime"
    ],
    "depends_on": [
      "M2-04",
      "M7-06"
    ],
    "body_path": "issue-bodies/M9-01.md"
  },
  {
    "id": "M9-02",
    "title": "[M9-02] Build full end-to-end scenarios for theorem, paper, and legal workflows",
    "milestone": "M9 Hardening",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "tests",
      "e2e",
      "scenarios"
    ],
    "depends_on": [
      "M6-02",
      "M7-04",
      "M8-03"
    ],
    "body_path": "issue-bodies/M9-02.md"
  },
  {
    "id": "M9-03",
    "title": "[M9-03] Write operator docs for promotion policy, review semantics, failure handling, and backend stop controls",
    "milestone": "M9 Hardening",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "docs",
      "policy",
      "ops",
      "runtime"
    ],
    "depends_on": [
      "M4-05",
      "M7-04",
      "M7-06"
    ],
    "body_path": "issue-bodies/M9-03.md"
  },
  {
    "id": "M9-04",
    "title": "[M9-04] Author project instructions and skill packs for engineering and architecture review",
    "milestone": "M9 Hardening",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "engineering",
      "architecture-review",
      "skills",
      "docs"
    ],
    "depends_on": [
      "M5-03"
    ],
    "body_path": "issue-bodies/M9-04.md"
  },
  {
    "id": "M9-05",
    "title": "[M9-05] Implement release packaging, reproducible local dev environment, and smoke release checklist",
    "milestone": "M9 Hardening",
    "wave": "productization",
    "status": "done",
    "execution_mode": "implement",
    "prototype_basis": "",
    "labels": [
      "release",
      "packaging",
      "ops",
      "runtime"
    ],
    "depends_on": [
      "M7-05",
      "M9-02",
      "M7-06"
    ],
    "body_path": "issue-bodies/M9-05.md"
  }
]
'@

$issues = $issuesJson | ConvertFrom-Json -Depth 8
$existingIssues = gh issue list --repo $Repo --state all --limit 1000 --json number,title,body | ConvertFrom-Json
$existingTitleMap = @{}
$existingBacklogMap = @{}
$markerPattern = '<!--\s*backlog-id:\s*([A-Za-z0-9._-]+)\s*-->'
foreach ($existingIssue in $existingIssues) {
  $existingTitleMap[$existingIssue.title] = $existingIssue.number
  $match = [regex]::Match($existingIssue.body, $markerPattern)
  if ($match.Success) {
    $backlogId = $match.Groups[1].Value
    if ($existingBacklogMap.ContainsKey($backlogId)) {
      throw "Duplicate backlog marker found for $backlogId in repo $Repo."
    }
    $existingBacklogMap[$backlogId] = $existingIssue.number
  }
}

foreach ($issue in $issues) {
  if ($existingBacklogMap.ContainsKey($issue.id)) {
    Write-Host ("skip existing backlog issue #" + $existingBacklogMap[$issue.id] + ": " + $issue.id)
    continue
  }
  if ($existingTitleMap.ContainsKey($issue.title)) {
    throw "Title collision without matching backlog marker for $($issue.id): $($issue.title)"
  }

  $bodyPath = Join-Path $PSScriptRoot $issue.body_path
  if (-not (Test-Path $bodyPath)) {
    throw "Issue body file not found: $bodyPath"
  }

  $args = @(
    "issue", "create",
    "--repo", $Repo,
    "--title", $issue.title,
    "--body-file", $bodyPath
  )

  if ($issue.milestone) {
    $args += @("--milestone", $issue.milestone)
  }

  foreach ($label in $issue.labels) {
    $args += @("--label", $label)
  }

  if ($DryRun) {
    Write-Host ("DRY RUN [create issue] gh " + ($args -join " "))
  } else {
    & gh @args | Out-Host
  }
}
