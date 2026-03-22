# Formal Claim Workbench Monorepo Backlog

This document is a standalone execution backlog for turning the current uploaded skeletons into a monorepo-based standalone workbench.

It assumes the following migration stance:

- Keep the canonical JSON schemas and make them the only normative contract.
- Keep the useful role decomposition from `formal_claim_pipeline`, but split deterministic backend work from LLM-authored work.
- Keep the useful document-ingest and trace ideas from `claim_tracer_mcp`, but remove its duplicate model/store layer and re-expose those capabilities through a thin MCP facade over the canonical engine.
- Keep engineering and architecture review as the two primary coding-agent environments.
- Drop Cursor-specific configuration and rules from the initial implementation.

## What to keep from the uploaded skeleton

Use these pieces as seeds, not as final package boundaries.

1. `formal_claim_pipeline/models.py` is useful as a first-pass Python contract mirror, but it must be replaced by generated bindings from the canonical schemas.
2. `formal_claim_pipeline/isabelle_wrapper.py` is a valid spike, but it is too heuristic to remain the final truth source.
3. `formal_claim_pipeline/agents/*` contains useful role prompts and workflow intent, but promotion and assurance logic must move into deterministic rules.
4. `claim_tracer_mcp/server.py` is useful for its tool-surface ideas, but its own `models.py` and `store.py` should be retired in favor of canonical contracts and engine-backed repositories.
5. The current single-package layout should be treated as a migration source, not as the long-term architecture.

## Target repository layout

```text
formal-claim-workbench/
  apps/
    desktop/
    cli/
  services/
    engine/
    isabelle-runner/
    mcp-server/
  packages/
    contracts/
    contracts-py/
    contracts-ts/
    graph-model/
    audit-rules/
    evidence-connectors/
  integrations/
    isabelle/
    assurance/
  examples/
    toy-claim/
    theorem-audit/
    paper-claim/
    legal-claim/
  tests/
    schema/
    audit/
    integration/
    e2e/
  docs/
    architecture/
    product/
    policies/
  .engineering/
  workflow-config/
```

## Migration map from current code

| Current file/package | Destination |
|---|---|
| `formal_claim_pipeline/schemas/*` | `packages/contracts/schemas/*` |
| `formal_claim_pipeline/models.py` | replaced by `packages/contracts-py` generated models |
| `formal_claim_pipeline/isabelle_wrapper.py` | `services/isabelle-runner/src/...` |
| `formal_claim_pipeline/store.py` | `services/engine/src/.../repositories` |
| `formal_claim_pipeline/orchestrator.py` | `services/engine/src/.../workflows/pipeline.py` |
| `formal_claim_pipeline/agents/*` | `services/engine/src/.../agents/*` |
| `claim_tracer_mcp/server.py` | `services/mcp-server/src/.../server.py` |
| `claim_tracer_mcp/engine.py` | split across `services/engine` and `packages/graph-model` |
| `claim_tracer_mcp/models.py` | retired; use canonical contracts or adapter DTOs |
| `claim_tracer_mcp/store.py` | retired; use engine repositories |

## Current structural problems in the uploaded skeleton

1. Two independent model layers already exist: the canonical formal-claim models and the claim-tracer models.
2. Storage is file-backed and duplicated, which will create schema drift and migration pain.
3. Isabelle build interpretation is still heuristic; export/dump/dependency surfaces are not normalized.
4. Promotion logic is partially deterministic and partially prompt-driven; the trust boundary is unclear.
5. MCP is already useful conceptually, but it is not yet a thin facade over a single canonical engine.
6. The desktop shell does not exist yet, so state ownership must be fixed before UI work begins.
7. There is no revision/event journal yet, so silent claim rewrites will be hard to audit.
8. Cursor-specific scaffolding can be removed without loss because MCP already gives you the reusable tool bus.

## Milestone order

- **M0 Foundation** — make contracts and repo boundaries real.
- **M1 Migration** — extract the uploaded Python packages into the target service/package layout.
- **M2 Core Domain** — build graph, audit, versioning, and Contract Pack logic.
- **M3 Isabelle Runner** — make proof backend deterministic and theorem-local.
- **M4 Engine Workflows** — compose claims, formalization, audit, and promotion into explicit workflows.
- **M5 MCP** — expose only the stable tool surface.
- **M6 CLI** — give humans and CI a usable front door.
- **M7 Desktop** — build the standalone workbench shell.
- **M8 Evidence** — connect documents and experiments without confusing them for proofs.
- **M9 Hardening** — tests, docs, release, and agent-skill polish.

## Issue backlog

## M0 Foundation
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M0-01` | `repo-root` / `/` | Initialize polyglot monorepo tooling (pnpm + uv + just + pre-commit) | `- `| M | Engineering | Root contains apps/, services/, packages/, integrations/, examples/, tests/; `just bootstrap`, `just test`, `just lint` succeed on a clean machine. |
| `M0-02` | `repo-root` / `/.github / docs/` | Set repository governance: CODEOWNERS, labels, milestones, issue templates, PR template | `M0-01 `| S | Tech Lead+Engineering | GitHub templates exist; CODEOWNERS routes contracts to policy owner and proof backend to Isabelle owner; backlog labels/milestones documented. |
| `M0-03` | `packages/contracts` / `packages/contracts` | Import canonical JSON Schemas and freeze schema version policy | `M0-01 `| S | Tech Lead+Architecture | Schemas live only in packages/contracts; README defines semver policy; duplicate copies are removed from service packages. |
| `M0-04` | `packages/contracts-py + contracts-ts` / `packages/contracts-py, packages/contracts-ts` | Generate Python and TypeScript bindings from canonical schemas | `M0-03 `| M | Engineering | `packages/contracts-py` and `packages/contracts-ts` build successfully; generated types round-trip valid fixtures; no hand-written duplicate model layer remains. |
| `M0-05` | `tests/schema + examples` / `tests/schema, examples/` | Add golden fixtures and schema conformance tests | `M0-04 `| S | Engineering | Schema tests pass in CI; fixtures include at least one valid and one invalid file per schema; failing fixture diffs are readable. |

### M0-01 — Initialize polyglot monorepo tooling (pnpm + uv + just + pre-commit)
Package / path: `repo-root` / `/`  
Type: `chore`  
Owner lane: `DevEx`  
Suggested executor: `Engineering`  
Depends on: `- `  
Labels: `infra,monorepo,devex`

Why:
Create root workspace layout, task runner, lockfiles, formatter/linter entrypoints, and shared env loading.

Acceptance criteria:
Root contains apps/, services/, packages/, integrations/, examples/, tests/; `just bootstrap`, `just test`, `just lint` succeed on a clean machine.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M0-02 — Set repository governance: CODEOWNERS, labels, milestones, issue templates, PR template
Package / path: `repo-root` / `/.github / docs/`  
Type: `chore`  
Owner lane: `Tech Lead`  
Suggested executor: `Tech Lead+Engineering`  
Depends on: `M0-01 `  
Labels: `governance,github,docs`

Why:
Define ownership boundaries for contracts, proof backend, engine, MCP, and desktop; add GitHub templates matching this backlog.

Acceptance criteria:
GitHub templates exist; CODEOWNERS routes contracts to policy owner and proof backend to Isabelle owner; backlog labels/milestones documented.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M0-03 — Import canonical JSON Schemas and freeze schema version policy
Package / path: `packages/contracts` / `packages/contracts`  
Type: `feat`  
Owner lane: `Contracts`  
Suggested executor: `Tech Lead+Architecture`  
Depends on: `M0-01 `  
Labels: `contracts,schema,policy`

Why:
Move claim-graph / assurance-graph / assurance-profile schemas into the canonical package and define versioning, migration, and deprecation rules.

Acceptance criteria:
Schemas live only in packages/contracts; README defines semver policy; duplicate copies are removed from service packages.

Execution note:
Best handled as an architecture or policy-writing task in Architecture Review, then handed back to Engineering for mechanical follow-through if needed.

### M0-04 — Generate Python and TypeScript bindings from canonical schemas
Package / path: `packages/contracts-py + contracts-ts` / `packages/contracts-py, packages/contracts-ts`  
Type: `feat`  
Owner lane: `Contracts`  
Suggested executor: `Engineering`  
Depends on: `M0-03 `  
Labels: `contracts,codegen,python,typescript`

Why:
Produce typed bindings so every service and app uses generated contract types rather than ad hoc mirrors.

Acceptance criteria:
`packages/contracts-py` and `packages/contracts-ts` build successfully; generated types round-trip valid fixtures; no hand-written duplicate model layer remains.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M0-05 — Add golden fixtures and schema conformance tests
Package / path: `tests/schema + examples` / `tests/schema, examples/`  
Type: `test`  
Owner lane: `QA`  
Suggested executor: `Engineering`  
Depends on: `M0-04 `  
Labels: `tests,schema,fixtures`

Why:
Create valid and invalid fixtures for all three schemas, including theorem-local hotspot profiles and legacy migration samples.

Acceptance criteria:
Schema tests pass in CI; fixtures include at least one valid and one invalid file per schema; failing fixture diffs are readable.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---

## M1 Migration
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M1-01` | `services/engine` / `services/engine` | Extract `formal_claim_pipeline` into `services/engine` package skeleton | `M0-04 `| M | Engineering | Engine package imports without referencing legacy top-level package names; old package either becomes thin compatibility shim or is removed. |
| `M1-02` | `services/isabelle-runner` / `services/isabelle-runner` | Extract `isabelle_wrapper.py` into deterministic Isabelle runner service | `M1-01 `| M | Engineering | Runner has its own package, CLI entrypoint, config, and JSON response models; engine depends on it via typed interface only. |
| `M1-03` | `services/mcp-server` / `services/mcp-server` | Replace standalone `claim_tracer_mcp` internals with thin MCP facade over canonical engine | `M1-01 M0-04 `| M | Engineering | MCP tools no longer import legacy `claim_tracer_mcp.models` or `store`; all tool I/O uses canonical contracts or explicit adapter DTOs. |
| `M1-04` | `services/engine + tests/integration` / `services/engine, tests/integration` | Add migration adapters for legacy file-backed artifacts into canonical store | `M1-01 M0-05 `| S | Engineering | A CLI or script imports legacy claim graphs and profiles into the new store; import report lists upgraded IDs and validation results. |

### M1-01 — Extract `formal_claim_pipeline` into `services/engine` package skeleton
Package / path: `services/engine` / `services/engine`  
Type: `refactor`  
Owner lane: `Backend`  
Suggested executor: `Engineering`  
Depends on: `M0-04 `  
Labels: `migration,engine,python`

Why:
Move the current single-package pipeline into a service-oriented engine layout without preserving the old import surface as the main API.

Acceptance criteria:
Engine package imports without referencing legacy top-level package names; old package either becomes thin compatibility shim or is removed.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M1-02 — Extract `isabelle_wrapper.py` into deterministic Isabelle runner service
Package / path: `services/isabelle-runner` / `services/isabelle-runner`  
Type: `refactor`  
Owner lane: `Proof`  
Suggested executor: `Engineering`  
Depends on: `M1-01 `  
Labels: `migration,isabelle,backend`

Why:
Split Isabelle process management out of the engine so builds, exports, and audits become deterministic service calls.

Acceptance criteria:
Runner has its own package, CLI entrypoint, config, and JSON response models; engine depends on it via typed interface only.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M1-03 — Replace standalone `claim_tracer_mcp` internals with thin MCP facade over canonical engine
Package / path: `services/mcp-server` / `services/mcp-server`  
Type: `refactor`  
Owner lane: `Platform`  
Suggested executor: `Engineering`  
Depends on: `M1-01 M0-04 `  
Labels: `migration,mcp,claim-tracer`

Why:
Keep the useful tool surface from the uploaded MCP skeleton, but remove its duplicate models/store/graph semantics and delegate to engine services.

Acceptance criteria:
MCP tools no longer import legacy `claim_tracer_mcp.models` or `store`; all tool I/O uses canonical contracts or explicit adapter DTOs.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M1-04 — Add migration adapters for legacy file-backed artifacts into canonical store
Package / path: `services/engine + tests/integration` / `services/engine, tests/integration`  
Type: `feat`  
Owner lane: `Backend`  
Suggested executor: `Engineering`  
Depends on: `M1-01 M0-05 `  
Labels: `migration,artifacts,storage`

Why:
Support importing legacy JSON artifacts from the current skeleton so existing examples are not stranded.

Acceptance criteria:
A CLI or script imports legacy claim graphs and profiles into the new store; import report lists upgraded IDs and validation results.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---

## M2 Core Domain
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M2-01` | `packages/graph-model` / `packages/graph-model` | Implement canonical graph query library for claims, relations, and assurance links | `M0-04 `| M | Engineering | Library exposes forward/backward slice, dependency closure, leaf/root queries, and diffable projections with unit tests. |
| `M2-02` | `packages/graph-model` / `packages/graph-model` | Implement theorem-local trust frontier and hotspot extraction | `M2-01 M3-04 `| M | Engineering | Given runner outputs, library returns theorem-local counts for axioms, locales, premises, oracles, reviewed exceptions, and hotspot artifacts. |
| `M2-03` | `packages/audit-rules` / `packages/audit-rules` | Implement deterministic assurance-profile computation rules | `M0-04 M2-01 `| M | Tech Lead+Engineering | Rule engine computes vector fields and gate decisions from typed inputs; LLM is optional for rationale text only. |
| `M2-04` | `packages/audit-rules` / `packages/audit-rules` | Add vacuity, countermodel, and intent-alignment status combinators | `M2-03 `| S | Engineering | Combinators map raw runner/adversary outputs into profile-ready statuses; tests cover all gate transitions. |
| `M2-05` | `packages/audit-rules + services/engine` / `packages/audit-rules, services/engine` | Implement Contract Pack emitter and downstream policy projection | `M2-03 `| S | Engineering | Emitter produces deterministic JSON bundle with allowed assumptions, blocked actions, and referenced artifact IDs. |
| `M2-06` | `services/engine` / `services/engine` | Add artifact versioning, migrations, and review-event journal | `M1-04 `| M | Engineering | Each graph/profile save creates revision metadata; migrations are replayable; review events are queryable by target claim. |

### M2-01 — Implement canonical graph query library for claims, relations, and assurance links
Package / path: `packages/graph-model` / `packages/graph-model`  
Type: `feat`  
Owner lane: `Backend`  
Suggested executor: `Engineering`  
Depends on: `M0-04 `  
Labels: `graph,domain,library`

Why:
Provide traversal, slicing, closure, hotspot, and impact analysis over canonical graphs.

Acceptance criteria:
Library exposes forward/backward slice, dependency closure, leaf/root queries, and diffable projections with unit tests.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M2-02 — Implement theorem-local trust frontier and hotspot extraction
Package / path: `packages/graph-model` / `packages/graph-model`  
Type: `feat`  
Owner lane: `Proof`  
Suggested executor: `Engineering`  
Depends on: `M2-01 M3-04 `  
Labels: `graph,audit,trust-frontier`

Why:
Compute per-target frontier views rather than file-global risk summaries.

Acceptance criteria:
Given runner outputs, library returns theorem-local counts for axioms, locales, premises, oracles, reviewed exceptions, and hotspot artifacts.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M2-03 — Implement deterministic assurance-profile computation rules
Package / path: `packages/audit-rules` / `packages/audit-rules`  
Type: `feat`  
Owner lane: `Policy`  
Suggested executor: `Tech Lead+Engineering`  
Depends on: `M0-04 M2-01 `  
Labels: `audit,policy,deterministic`

Why:
Move hard promotion logic and score aggregation out of prompts into deterministic code.

Acceptance criteria:
Rule engine computes vector fields and gate decisions from typed inputs; LLM is optional for rationale text only.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M2-04 — Add vacuity, countermodel, and intent-alignment status combinators
Package / path: `packages/audit-rules` / `packages/audit-rules`  
Type: `feat`  
Owner lane: `Policy`  
Suggested executor: `Engineering`  
Depends on: `M2-03 `  
Labels: `audit,vacuity,intent`

Why:
Encode the exact promotion blockers for countermodels, vacuity failures, unresolved obligations, and failed backtranslation checks.

Acceptance criteria:
Combinators map raw runner/adversary outputs into profile-ready statuses; tests cover all gate transitions.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M2-05 — Implement Contract Pack emitter and downstream policy projection
Package / path: `packages/audit-rules + services/engine` / `packages/audit-rules, services/engine`  
Type: `feat`  
Owner lane: `Policy`  
Suggested executor: `Engineering`  
Depends on: `M2-03 `  
Labels: `contracts,downstream,policy`

Why:
Emit constrained Contract Packs for research/dev consumers from the approved assurance state.

Acceptance criteria:
Emitter produces deterministic JSON bundle with allowed assumptions, blocked actions, and referenced artifact IDs.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M2-06 — Add artifact versioning, migrations, and review-event journal
Package / path: `services/engine` / `services/engine`  
Type: `feat`  
Owner lane: `Backend`  
Suggested executor: `Engineering`  
Depends on: `M1-04 `  
Labels: `storage,events,versioning`

Why:
Replace ad hoc file snapshots with explicit artifact revisions and human/agent review events.

Acceptance criteria:
Each graph/profile save creates revision metadata; migrations are replayable; review events are queryable by target claim.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---

## M3 Isabelle Runner
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M3-01` | `services/isabelle-runner` / `services/isabelle-runner` | Implement workspace/session scaffolder for temporary Isabelle runs | `M1-02 `| S | Engineering | Runner can materialize a session workspace from a typed request and clean it up deterministically. |
| `M3-02` | `services/isabelle-runner` / `services/isabelle-runner` | Implement build runner with structured log capture and session fingerprinting | `M3-01 `| M | Engineering | Runner returns typed build JSON with timeout classification, session fingerprint, artifact paths, and captured diagnostics. |
| `M3-03` | `services/isabelle-runner` / `services/isabelle-runner` | Parse `isabelle export` and `isabelle dump` into typed audit payloads | `M3-02 `| L | Engineering | Export/dump parsers produce typed data structures for theories, sessions, entities, and dependency surfaces; golden tests included. |
| `M3-04` | `services/isabelle-runner` / `services/isabelle-runner` | Implement theorem-local dependency, oracle, and reviewed-exception extraction | `M3-03 `| L | Tech Lead+Engineering | Runner returns theorem-level dependency closure including theorem deps, oracles, reviewed global axioms, and imported theory hotspots. |
| `M3-05` | `services/isabelle-runner` / `services/isabelle-runner` | Integrate Nitpick and optional Sledgehammer probes | `M3-02 `| M | Engineering | Runner can execute Nitpick probe on generated target and report pass/fail/inconclusive; optional Sledgehammer output is captured separately. |
| `M3-06` | `services/isabelle-runner` / `services/isabelle-runner` | Implement premise-deletion and conclusion-perturbation harness | `M3-05 `| M | Engineering | Harness can generate variant jobs and summarize stable/fragile results per target theorem. |
| `M3-07` | `apps/cli + services/isabelle-runner` / `apps/cli, services/isabelle-runner` | Expose runner subcommands: build, export, dump, audit, profile | `M3-03 M2-03 `| S | Engineering | CLI commands emit JSON to stdout and non-zero exit codes on failures; help text documents request/response files. |
| `M3-08` | `tests/integration + examples/theorem-audit` / `tests/integration, examples/theorem-audit` | Create Isabelle integration fixtures and reproducible runner tests | `M3-07 `| M | Engineering | CI or gated local profile runs full runner tests against fixtures; failures show readable diff artifacts. |

### M3-01 — Implement workspace/session scaffolder for temporary Isabelle runs
Package / path: `services/isabelle-runner` / `services/isabelle-runner`  
Type: `feat`  
Owner lane: `Proof`  
Suggested executor: `Engineering`  
Depends on: `M1-02 `  
Labels: `isabelle,runner,workspace`

Why:
Create isolated workspaces, session roots, import lists, and generated theory placement for formalizer outputs.

Acceptance criteria:
Runner can materialize a session workspace from a typed request and clean it up deterministically.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M3-02 — Implement build runner with structured log capture and session fingerprinting
Package / path: `services/isabelle-runner` / `services/isabelle-runner`  
Type: `feat`  
Owner lane: `Proof`  
Suggested executor: `Engineering`  
Depends on: `M3-01 `  
Labels: `isabelle,build,logs`

Why:
Upgrade current heuristic build wrapper into structured execution with stdout/stderr capture, exit semantics, and reproducible fingerprints.

Acceptance criteria:
Runner returns typed build JSON with timeout classification, session fingerprint, artifact paths, and captured diagnostics.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M3-03 — Parse `isabelle export` and `isabelle dump` into typed audit payloads
Package / path: `services/isabelle-runner` / `services/isabelle-runner`  
Type: `feat`  
Owner lane: `Proof`  
Suggested executor: `Engineering`  
Depends on: `M3-02 `  
Labels: `isabelle,export,dump`

Why:
Stop treating export output as opaque text blobs; normalize exports into typed JSON consumable by graph and audit packages.

Acceptance criteria:
Export/dump parsers produce typed data structures for theories, sessions, entities, and dependency surfaces; golden tests included.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M3-04 — Implement theorem-local dependency, oracle, and reviewed-exception extraction
Package / path: `services/isabelle-runner` / `services/isabelle-runner`  
Type: `feat`  
Owner lane: `Proof`  
Suggested executor: `Tech Lead+Engineering`  
Depends on: `M3-03 `  
Labels: `isabelle,dependencies,oracles`

Why:
Use official export/dependency surfaces to compute target-local trust data instead of file-level heuristics.

Acceptance criteria:
Runner returns theorem-level dependency closure including theorem deps, oracles, reviewed global axioms, and imported theory hotspots.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M3-05 — Integrate Nitpick and optional Sledgehammer probes
Package / path: `services/isabelle-runner` / `services/isabelle-runner`  
Type: `feat`  
Owner lane: `Proof`  
Suggested executor: `Engineering`  
Depends on: `M3-02 `  
Labels: `isabelle,nitpick,sledgehammer`

Why:
Add probe tasks for satisfiability, countermodels, and proof-replay hints as structured subcommands.

Acceptance criteria:
Runner can execute Nitpick probe on generated target and report pass/fail/inconclusive; optional Sledgehammer output is captured separately.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M3-06 — Implement premise-deletion and conclusion-perturbation harness
Package / path: `services/isabelle-runner` / `services/isabelle-runner`  
Type: `feat`  
Owner lane: `Proof`  
Suggested executor: `Engineering`  
Depends on: `M3-05 `  
Labels: `isabelle,adversarial,robustness`

Why:
Support adversarial checks that remove premises or perturb conclusions to expose vacuity and over-strong statements.

Acceptance criteria:
Harness can generate variant jobs and summarize stable/fragile results per target theorem.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M3-07 — Expose runner subcommands: build, export, dump, audit, profile
Package / path: `apps/cli + services/isabelle-runner` / `apps/cli, services/isabelle-runner`  
Type: `feat`  
Owner lane: `DevEx`  
Suggested executor: `Engineering`  
Depends on: `M3-03 M2-03 `  
Labels: `cli,isabelle,ops`

Why:
Provide a stable machine-usable CLI surface for local debugging and CI.

Acceptance criteria:
CLI commands emit JSON to stdout and non-zero exit codes on failures; help text documents request/response files.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M3-08 — Create Isabelle integration fixtures and reproducible runner tests
Package / path: `tests/integration + examples/theorem-audit` / `tests/integration, examples/theorem-audit`  
Type: `test`  
Owner lane: `QA`  
Suggested executor: `Engineering`  
Depends on: `M3-07 `  
Labels: `tests,isabelle,integration`

Why:
Add toy sessions covering definitional, locale-based, sorry-containing, and intentionally suspicious examples.

Acceptance criteria:
CI or gated local profile runs full runner tests against fixtures; failures show readable diff artifacts.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---

## M4 Engine Workflows
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M4-01` | `services/engine` / `services/engine` | Implement canonical repository layer over SQLite with file export mirrors | `M0-05 M2-06 `| M | Engineering | Engine can create/load/update projects, claims, assurance artifacts, and revisions; exports recreate canonical JSON files. |
| `M4-02` | `services/engine` / `services/engine` | Implement claim-structuring workflow and planner admission pipeline | `M1-01 M4-01 `| M | Engineering | Workflow produces canonical ClaimGraph revision or structured failure; retries are logged; no unchecked JSON from agents enters storage. |
| `M4-03` | `services/engine` / `services/engine` | Implement dual-formalization workflow with divergence capture | `M4-02 M3-02 `| M | Engineering | Workflow stores both formalizer attempts, links them to the target claim, and computes divergence metadata even when one attempt fails. |
| `M4-04` | `services/engine` / `services/engine` | Implement audit workflow that composes runner outputs and deterministic rules | `M4-03 M2-03 M3-06 `| M | Engineering | Workflow emits AssuranceProfile without requiring an LLM for gating; optional narrative rationale can be added afterward. |
| `M4-05` | `services/engine` / `services/engine` | Implement promotion state machine and review checkpoints | `M4-04 `| S | Tech Lead+Engineering | State machine rejects invalid transitions; review checkpoints and override rationales are recorded in the event journal. |
| `M4-06` | `services/engine + packages/evidence-connectors` / `services/engine, packages/evidence-connectors` | Implement document-ingest adapter from claim-tracer concepts into canonical Claim Graph | `M2-01 M1-03 `| M | Engineering | Academic/legal/general document ingest produces ClaimGraph revisions plus mapping report from source-domain roles to canonical claims. |
| `M4-07` | `services/engine` / `services/engine` | Add engine API for project, artifact, audit, and promotion operations | `M4-04 M4-05 `| S | Engineering | Typed service layer exposes create/open project, run claim structuring, run audit, recompute profile, approve promotion, and export bundle. |
| `M4-08` | `services/engine` / `services/engine` | Refactor prompts and agent adapters into pluggable provider modules | `M4-02 `| M | Architecture+Engineering | Agents can be swapped or disabled per workflow; prompt files are versioned and testable; provider config no longer leaks into domain code. |

### M4-01 — Implement canonical repository layer over SQLite with file export mirrors
Package / path: `services/engine` / `services/engine`  
Type: `feat`  
Owner lane: `Backend`  
Suggested executor: `Engineering`  
Depends on: `M0-05 M2-06 `  
Labels: `engine,storage,sqlite`

Why:
Use SQLite for authoritative persistence while preserving JSON export/import for examples and diff review.

Acceptance criteria:
Engine can create/load/update projects, claims, assurance artifacts, and revisions; exports recreate canonical JSON files.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M4-02 — Implement claim-structuring workflow and planner admission pipeline
Package / path: `services/engine` / `services/engine`  
Type: `feat`  
Owner lane: `Backend`  
Suggested executor: `Engineering`  
Depends on: `M1-01 M4-01 `  
Labels: `engine,planner,workflow`

Why:
Turn current planner + claim graph agent logic into explicit workflow states with retry policy and validation checkpoints.

Acceptance criteria:
Workflow produces canonical ClaimGraph revision or structured failure; retries are logged; no unchecked JSON from agents enters storage.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M4-03 — Implement dual-formalization workflow with divergence capture
Package / path: `services/engine` / `services/engine`  
Type: `feat`  
Owner lane: `Backend`  
Suggested executor: `Engineering`  
Depends on: `M4-02 M3-02 `  
Labels: `engine,formalization,workflow`

Why:
Run two independent formalizers and preserve divergence notes, backtranslations, and artifact lineage.

Acceptance criteria:
Workflow stores both formalizer attempts, links them to the target claim, and computes divergence metadata even when one attempt fails.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M4-04 — Implement audit workflow that composes runner outputs and deterministic rules
Package / path: `services/engine` / `services/engine`  
Type: `feat`  
Owner lane: `Backend`  
Suggested executor: `Engineering`  
Depends on: `M4-03 M2-03 M3-06 `  
Labels: `engine,audit,workflow`

Why:
Create the canonical path from proof artifacts to theorem-local AssuranceProfile generation.

Acceptance criteria:
Workflow emits AssuranceProfile without requiring an LLM for gating; optional narrative rationale can be added afterward.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M4-05 — Implement promotion state machine and review checkpoints
Package / path: `services/engine` / `services/engine`  
Type: `feat`  
Owner lane: `Policy`  
Suggested executor: `Tech Lead+Engineering`  
Depends on: `M4-04 `  
Labels: `engine,state-machine,promotion`

Why:
Separate candidate promotion, human approval, and certified gate transitions so no single agent self-certifies its own work.

Acceptance criteria:
State machine rejects invalid transitions; review checkpoints and override rationales are recorded in the event journal.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M4-06 — Implement document-ingest adapter from claim-tracer concepts into canonical Claim Graph
Package / path: `services/engine + packages/evidence-connectors` / `services/engine, packages/evidence-connectors`  
Type: `feat`  
Owner lane: `Research`  
Suggested executor: `Engineering`  
Depends on: `M2-01 M1-03 `  
Labels: `ingestion,claim-tracer,documents`

Why:
Preserve the useful document analysis path from the MCP skeleton, but normalize its roles and relations into canonical schema terms.

Acceptance criteria:
Academic/legal/general document ingest produces ClaimGraph revisions plus mapping report from source-domain roles to canonical claims.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M4-07 — Add engine API for project, artifact, audit, and promotion operations
Package / path: `services/engine` / `services/engine`  
Type: `feat`  
Owner lane: `Platform`  
Suggested executor: `Engineering`  
Depends on: `M4-04 M4-05 `  
Labels: `engine,api,service`

Why:
Define the internal API that MCP and desktop will call instead of importing workflow modules directly.

Acceptance criteria:
Typed service layer exposes create/open project, run claim structuring, run audit, recompute profile, approve promotion, and export bundle.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M4-08 — Refactor prompts and agent adapters into pluggable provider modules
Package / path: `services/engine` / `services/engine`  
Type: `refactor`  
Owner lane: `AI Runtime`  
Suggested executor: `Architecture+Engineering`  
Depends on: `M4-02 `  
Labels: `agents,prompts,providers`

Why:
Preserve the useful role prompts from the skeleton, but isolate them behind provider-agnostic adapters and prompt registries.

Acceptance criteria:
Agents can be swapped or disabled per workflow; prompt files are versioned and testable; provider config no longer leaks into domain code.

Execution note:
Use Architecture Review first for design/review or prompt/policy shaping, then Engineering for implementation and test closure; keep the human owner in the loop for final sign-off.


---

## M5 MCP
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M5-01` | `services/mcp-server` / `services/mcp-server` | Define narrow typed MCP tool surface over engine and runner | `M4-07 M3-07 `| M | Engineering | MCP server provides typed tools for project CRUD, graph retrieval, claim tracing, build/audit/profile operations, and export bundle. |
| `M5-02` | `services/mcp-server` / `services/mcp-server` | Port useful `claim_tracer_mcp` tools into canonical MCP endpoints | `M5-01 M4-06 `| M | Engineering | Tool names and JSON responses are documented; legacy user stories from the uploaded MCP README work against the new server. |
| `M5-03` | `services/mcp-server` / `services/mcp-server` | Add proof-oriented MCP tools for Isabelle audit and assurance-profile recomputation | `M5-01 M4-04 `| S | Engineering | MCP includes tools such as validate_contract, run_isabelle_build, run_isabelle_audit, recompute_profile, emit_contract_pack. |
| `M5-04` | `services/mcp-server` / `services/mcp-server` | Implement structured errors, audit logs, and concurrency limits for MCP | `M5-01 `| S | Engineering | Every tool returns machine-readable error codes; server logs request IDs; configurable queue/concurrency guard exists. |

### M5-01 — Define narrow typed MCP tool surface over engine and runner
Package / path: `services/mcp-server` / `services/mcp-server`  
Type: `feat`  
Owner lane: `Platform`  
Suggested executor: `Engineering`  
Depends on: `M4-07 M3-07 `  
Labels: `mcp,api,platform`

Why:
Expose only stable project/artifact/audit tools rather than every internal workflow detail.

Acceptance criteria:
MCP server provides typed tools for project CRUD, graph retrieval, claim tracing, build/audit/profile operations, and export bundle.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M5-02 — Port useful `claim_tracer_mcp` tools into canonical MCP endpoints
Package / path: `services/mcp-server` / `services/mcp-server`  
Type: `feat`  
Owner lane: `Research`  
Suggested executor: `Engineering`  
Depends on: `M5-01 M4-06 `  
Labels: `mcp,claim-tracer,ingestion`

Why:
Keep ingest, trace_forward, trace_backward, detect_gaps, assess, and export_graph behavior where it still fits the canonical model.

Acceptance criteria:
Tool names and JSON responses are documented; legacy user stories from the uploaded MCP README work against the new server.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M5-03 — Add proof-oriented MCP tools for Isabelle audit and assurance-profile recomputation
Package / path: `services/mcp-server` / `services/mcp-server`  
Type: `feat`  
Owner lane: `Proof`  
Suggested executor: `Engineering`  
Depends on: `M5-01 M4-04 `  
Labels: `mcp,isabelle,audit`

Why:
Expose the proof workflow that engineering and architecture review will actually call while coding.

Acceptance criteria:
MCP includes tools such as validate_contract, run_isabelle_build, run_isabelle_audit, recompute_profile, emit_contract_pack.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M5-04 — Implement structured errors, audit logs, and concurrency limits for MCP
Package / path: `services/mcp-server` / `services/mcp-server`  
Type: `feat`  
Owner lane: `Platform`  
Suggested executor: `Engineering`  
Depends on: `M5-01 `  
Labels: `mcp,ops,reliability`

Why:
Make MCP safe for agent use by preventing silent failures and runaway concurrent proof jobs.

Acceptance criteria:
Every tool returns machine-readable error codes; server logs request IDs; configurable queue/concurrency guard exists.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---

## M6 CLI
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M6-01` | `apps/cli` / `apps/cli` | Build human-facing CLI for project bootstrap, audit runs, and artifact inspection | `M5-03 M4-07 `| S | Engineering | CLI supports init/open/import/run-claim-structuring/run-audit/recompute-profile/show/export commands with JSON and pretty output modes. |
| `M6-02` | `apps/cli + tests/e2e` / `apps/cli, tests/e2e` | Add CLI smoke tests and scripted operator scenarios | `M6-01 `| S | Engineering | E2E tests cover toy theorem audit and paper/legal ingest flows; CI can run a reduced smoke profile. |

### M6-01 — Build human-facing CLI for project bootstrap, audit runs, and artifact inspection
Package / path: `apps/cli` / `apps/cli`  
Type: `feat`  
Owner lane: `DevEx`  
Suggested executor: `Engineering`  
Depends on: `M5-03 M4-07 `  
Labels: `cli,ux,devex`

Why:
Replace the current single-package CLI with a thin front door for local operators and CI.

Acceptance criteria:
CLI supports init/open/import/run-claim-structuring/run-audit/recompute-profile/show/export commands with JSON and pretty output modes.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M6-02 — Add CLI smoke tests and scripted operator scenarios
Package / path: `apps/cli + tests/e2e` / `apps/cli, tests/e2e`  
Type: `test`  
Owner lane: `QA`  
Suggested executor: `Engineering`  
Depends on: `M6-01 `  
Labels: `cli,tests,e2e`

Why:
Capture the canonical batch workflows for theorem audit and document analysis.

Acceptance criteria:
E2E tests cover toy theorem audit and paper/legal ingest flows; CI can run a reduced smoke profile.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---

## M7 Desktop
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M7-01` | `apps/desktop` / `apps/desktop` | Initialize Tauri desktop shell with project browser and local process orchestration | `M0-01 M4-07 `| M | Engineering | Desktop app opens local projects, starts/stops local services, and displays project metadata without manual terminal setup. |
| `M7-02` | `apps/desktop` / `apps/desktop` | Implement Claim Graph and Assurance Graph navigation with React Flow | `M7-01 M2-01 `| M | Engineering | User can inspect graphs by target claim, filter by relation/status, and open linked artifacts from selected nodes. |
| `M7-03` | `apps/desktop + integrations/isabelle` / `apps/desktop, integrations/isabelle` | Implement light theory editor and managed external Isabelle launch | `M7-01 M3-07 `| M | Engineering | User can inspect generated `.thy`, launch external Isabelle session, and sync changed artifact paths/fingerprints back into the app. |
| `M7-04` | `apps/desktop` / `apps/desktop` | Implement Assurance Profile inspector, gate UI, and promotion workflow | `M7-02 M4-05 `| M | Engineering | Operator can review profile vectors, inspect blockers, add rationale, and execute allowed promotions or downgrades. |
| `M7-05` | `apps/desktop` / `apps/desktop` | Add artifact timeline, diff viewer, and review-event panel | `M7-04 M2-06 `| S | Engineering | User can compare revisions of graph/profile/theory text and see who or what changed them and why. |

### M7-01 — Initialize Tauri desktop shell with project browser and local process orchestration
Package / path: `apps/desktop` / `apps/desktop`  
Type: `feat`  
Owner lane: `Frontend`  
Suggested executor: `Engineering`  
Depends on: `M0-01 M4-07 `  
Labels: `desktop,tauri,frontend`

Why:
Create the standalone shell, project open/create flows, and engine process lifecycle management.

Acceptance criteria:
Desktop app opens local projects, starts/stops local services, and displays project metadata without manual terminal setup.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M7-02 — Implement Claim Graph and Assurance Graph navigation with React Flow
Package / path: `apps/desktop` / `apps/desktop`  
Type: `feat`  
Owner lane: `Frontend`  
Suggested executor: `Engineering`  
Depends on: `M7-01 M2-01 `  
Labels: `desktop,graph,react-flow`

Why:
Provide node/edge navigation, filtering, hotspot highlighting, and revision-aware diff views.

Acceptance criteria:
User can inspect graphs by target claim, filter by relation/status, and open linked artifacts from selected nodes.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M7-03 — Implement light theory editor and managed external Isabelle launch
Package / path: `apps/desktop + integrations/isabelle` / `apps/desktop, integrations/isabelle`  
Type: `feat`  
Owner lane: `Frontend+Proof`  
Suggested executor: `Engineering`  
Depends on: `M7-01 M3-07 `  
Labels: `desktop,isabelle,editor`

Why:
Support Monaco-based light editing while delegating deep proof interaction to official Isabelle interfaces.

Acceptance criteria:
User can inspect generated `.thy`, launch external Isabelle session, and sync changed artifact paths/fingerprints back into the app.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M7-04 — Implement Assurance Profile inspector, gate UI, and promotion workflow
Package / path: `apps/desktop` / `apps/desktop`  
Type: `feat`  
Owner lane: `Frontend`  
Suggested executor: `Engineering`  
Depends on: `M7-02 M4-05 `  
Labels: `desktop,assurance,policy`

Why:
Surface theorem-local trust frontier, blocking issues, and allowed downstream actions in one operator-facing pane.

Acceptance criteria:
Operator can review profile vectors, inspect blockers, add rationale, and execute allowed promotions or downgrades.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M7-05 — Add artifact timeline, diff viewer, and review-event panel
Package / path: `apps/desktop` / `apps/desktop`  
Type: `feat`  
Owner lane: `Frontend`  
Suggested executor: `Engineering`  
Depends on: `M7-04 M2-06 `  
Labels: `desktop,timeline,diff`

Why:
Make agent/human changes inspectable over time to prevent silent claim laundering.

Acceptance criteria:
User can compare revisions of graph/profile/theory text and see who or what changed them and why.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---

## M8 Evidence
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M8-01` | `packages/evidence-connectors` / `packages/evidence-connectors` | Implement local document import and citation-anchor normalization | `M4-06 `| S | Engineering | Local file import preserves source_ref, excerpt, and offsets or fallback anchors; UI/API can retrieve them. |
| `M8-02` | `packages/evidence-connectors` / `packages/evidence-connectors` | Implement experiment/evaluation evidence adapter | `M8-01 `| M | Engineering | Adapter stores experiment metadata, result summaries, and linkage to claims without entering the proof gate path. |
| `M8-03` | `packages/evidence-connectors + integrations/assurance` / `packages/evidence-connectors, integrations/assurance` | Implement external reference registry and assurance-link browser | `M8-01 M7-02 `| S | Engineering | Every assurance link resolves to an artifact preview or metadata card in CLI/API/UI. |

### M8-01 — Implement local document import and citation-anchor normalization
Package / path: `packages/evidence-connectors` / `packages/evidence-connectors`  
Type: `feat`  
Owner lane: `Research`  
Suggested executor: `Engineering`  
Depends on: `M4-06 `  
Labels: `evidence,documents,citations`

Why:
Store source excerpts and spans in a normalized way so claim provenance and assurance links remain inspectable.

Acceptance criteria:
Local file import preserves source_ref, excerpt, and offsets or fallback anchors; UI/API can retrieve them.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M8-02 — Implement experiment/evaluation evidence adapter
Package / path: `packages/evidence-connectors` / `packages/evidence-connectors`  
Type: `feat`  
Owner lane: `Research`  
Suggested executor: `Engineering`  
Depends on: `M8-01 `  
Labels: `evidence,experiments,metrics`

Why:
Support empirical artifacts such as benchmark runs, tables, or notebooks without pretending they are formal proofs.

Acceptance criteria:
Adapter stores experiment metadata, result summaries, and linkage to claims without entering the proof gate path.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M8-03 — Implement external reference registry and assurance-link browser
Package / path: `packages/evidence-connectors + integrations/assurance` / `packages/evidence-connectors, integrations/assurance`  
Type: `feat`  
Owner lane: `Research`  
Suggested executor: `Engineering`  
Depends on: `M8-01 M7-02 `  
Labels: `evidence,assurance,traceability`

Why:
Create browseable links from claims to source documents, experiments, and review notes.

Acceptance criteria:
Every assurance link resolves to an artifact preview or metadata card in CLI/API/UI.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---

## M9 Hardening
| ID | Package / Path | Title | Depends on | Est. | Suggested executor | Done when |
|---|---|---|---|---|---|---|
| `M9-01` | `tests/audit` / `tests/audit` | Add audit-rule property tests and regression corpus | `M2-04 `| S | Engineering | Regression corpus includes vacuity, countermodel, oracle, and missing-premise cases; property tests guard state-machine invariants. |
| `M9-02` | `tests/e2e` / `tests/e2e` | Build full end-to-end scenarios for theorem, paper, and legal workflows | `M6-02 M7-04 M8-03 `| M | Engineering | At least three scenario suites run locally; failures preserve artifacts for debugging. |
| `M9-03` | `docs/architecture + docs/policies` / `docs/architecture, docs/policies` | Write operator docs for promotion policy, review semantics, and failure handling | `M4-05 M7-04 `| S | Architecture+Tech Lead | Docs explain each assurance vector, each blocker, and the review procedure for exceptions. |
| `M9-04` | `docs/product` / `docs/product` | Author project instructions and skill packs for engineering and architecture review | `M5-03 `| S | Architecture+Tech Lead | workflow documentation reference the same package boundaries, commands, and MCP tools; no Cursor config remains. |
| `M9-05` | `repo-root + apps/desktop` / `/, apps/desktop` | Implement release packaging, reproducible local dev environment, and smoke release checklist | `M7-05 M9-02 `| M | Engineering | Tagged release can build desktop binaries and service wheels; release checklist includes smoke tests and known limitations. |

### M9-01 — Add audit-rule property tests and regression corpus
Package / path: `tests/audit` / `tests/audit`  
Type: `test`  
Owner lane: `QA`  
Suggested executor: `Engineering`  
Depends on: `M2-04 `  
Labels: `tests,audit,regression`

Why:
Prevent accidental weakening of promotion rules or score semantics.

Acceptance criteria:
Regression corpus includes vacuity, countermodel, oracle, and missing-premise cases; property tests guard state-machine invariants.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M9-02 — Build full end-to-end scenarios for theorem, paper, and legal workflows
Package / path: `tests/e2e` / `tests/e2e`  
Type: `test`  
Owner lane: `QA`  
Suggested executor: `Engineering`  
Depends on: `M6-02 M7-04 M8-03 `  
Labels: `tests,e2e,scenarios`

Why:
Exercise the complete stack from project creation to audit verdict and export bundle.

Acceptance criteria:
At least three scenario suites run locally; failures preserve artifacts for debugging.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.

### M9-03 — Write operator docs for promotion policy, review semantics, and failure handling
Package / path: `docs/architecture + docs/policies` / `docs/architecture, docs/policies`  
Type: `docs`  
Owner lane: `Policy`  
Suggested executor: `Architecture+Tech Lead`  
Depends on: `M4-05 M7-04 `  
Labels: `docs,policy,ops`

Why:
Document what each gate means, who may override what, and how suspicious artifacts are quarantined.

Acceptance criteria:
Docs explain each assurance vector, each blocker, and the review procedure for exceptions.

Execution note:
Best handled as an architecture or policy-writing task in Architecture Review, then handed back to Engineering for mechanical follow-through if needed.

### M9-04 — Author project instructions and skill packs for engineering and architecture review
Package / path: `docs/product` / `docs/product`  
Type: `docs`  
Owner lane: `AI Runtime`  
Suggested executor: `Architecture+Tech Lead`  
Depends on: `M5-03 `  
Labels: `engineering,architecture-review,skills,docs`

Why:
Replace generic assistant usage with repository-specific instructions, acceptance-check loops, and package-local skills.

Acceptance criteria:
workflow documentation reference the same package boundaries, commands, and MCP tools; no Cursor config remains.

Execution note:
Best handled as an architecture or policy-writing task in Architecture Review, then handed back to Engineering for mechanical follow-through if needed.

### M9-05 — Implement release packaging, reproducible local dev environment, and smoke release checklist
Package / path: `repo-root + apps/desktop` / `/, apps/desktop`  
Type: `chore`  
Owner lane: `DevEx`  
Suggested executor: `Engineering`  
Depends on: `M7-05 M9-02 `  
Labels: `release,packaging,ops`

Why:
Prepare the workbench for repeatable local installation and internal dogfooding.

Acceptance criteria:
Tagged release can build desktop binaries and service wheels; release checklist includes smoke tests and known limitations.

Execution note:
Best run as a bounded implementation task in Engineering with the package path, acceptance criteria, and required test command explicitly stated.


---
## Parallel execution lanes

Once M0-03 through M0-05 are done, you can run these lanes mostly in parallel.

- **Lane A — Contracts and domain**: M2-01, M2-03, M2-06
- **Lane B — Isabelle backend**: M3-01 through M3-05
- **Lane C — Engine workflows**: M4-01 through M4-04
- **Lane D — MCP surface**: M5-01 through M5-04, after M4-07 exists
- **Lane E — Desktop shell**: M7-01 through M7-05, after M4-07 and basic M3 CLI commands exist
- **Lane F — Evidence and docs**: M8-01 through M9-04

## Recommended agent operating model

Use **Engineering** as the implementation worker for issues that have:
- a single package boundary,
- an explicit acceptance test,
- and a clear command that can be run locally.

Use **Architecture Review** for:
- architecture-affecting refactors,
- prompt/policy review,
- migration design,
- and red-team review of audit semantics.

A practical loop looks like this:

1. Human opens one milestone branch.
2. Human picks 1 to 3 issues that live in disjoint packages.
3. Architecture Review reviews the issue framing if the issue changes contracts, policy, or architecture.
4. Engineering implements the issue in a bounded branch using the MCP tools and local test commands.
5. Human or Architecture Review reviews the diff against the acceptance criteria.
6. Merge only when the package-local tests and one upstream integration test pass.

## First three weeks, realistically

If you want the fastest path to a runnable internal prototype, do not start with the desktop app.

Week 1:
- M0-01 through M0-05
- M1-01 through M1-03

Week 2:
- M3-01 through M3-03
- M2-01 through M2-04
- M4-01 and M4-02

Week 3:
- M4-03 through M4-05
- M5-01 through M5-03
- M6-01

At the end of week 3 you should have:
- canonical contracts,
- deterministic runner commands,
- a working engine,
- a thin MCP facade,
- and a CLI capable of creating a project, formalizing one claim, running Isabelle, and emitting an Assurance Profile.

That is the correct point to begin serious desktop-shell work.
