# Orchestration Assurance Engine

This repository is an assurance-oriented engine for turning source material and
claims into canonical claim graphs, formalization/audit results, assurance
profiles, promotion states, and exportable bundles.

It is not locked to one subject area. The same core pipeline is meant for
natural-language formalization across mathematical proof claims, legal and
regulatory reasoning, policy/compliance arguments, and other domains where
human text needs to be structured, formalized, verified, audited, and promoted
through explicit gates.

The public repository name is `orchestration-assurance-engine`, but the current
Python packages and CLI entrypoints still use the existing `formal-claim-*`
names.

In practice, that means this repo can already be positioned as a reusable
natural-language formalization engine rather than a single-purpose theorem tool
or legal prototype. With modest domain-specific changes to prompts, workflow
policy, schemas, and proof/backend adapters, the same architecture can be
reused across very different formalization settings.

That generality is an intentional tradeoff, not accidental abstraction. This
repo was built for orchestration-first use: a reusable coordination layer that
can sit between source material, model-driven formalization, proof/verification
backends, review policy, and downstream product surfaces. It is deliberately
more generic than a one-domain tool because that is the point of the system.

## What Works Today

- create, open, list, and persist projects
- ingest inline text, local files, or uploaded bytes into canonical claim graphs
- preserve source-document mappings, normalized references, and evaluation evidence
- run claim-structuring, dual-formalization, audit, profile recomputation, and promotion workflows
- export canonical project bundles and inspect stored revisions, review events, and profiles
- expose the same engine through a human CLI, an MCP server, and a small certification HTTP surface
- manage bounded proof jobs and inspect generated proof source files

## Cross-Domain Positioning

This codebase is intentionally shaped so the orchestration layer stays reusable
even when the domain changes.

- for mathematics, it can drive natural-language claims toward proof-oriented formalization and proof-backed audit
- for law and regulation, it can structure source documents, preserve citations, track claim/reference links, and apply review/promotion gates
- for policy, safety, compliance, and research workflows, it can turn unstructured text into governed claim graphs plus evidence-linked assessment artifacts
- the current examples already span theorem, paper, and legal source material instead of a single narrow demo domain

The important point is that the repo is already built around a generic
`source material -> claim graph -> formalization/audit -> assurance profile ->
promotion/export` pipeline. That makes it straightforward to adapt for other
natural-language formalization problems without replacing the whole system.

That is also why some parts of the design are intentionally not hyper-tuned to
one vertical. The project optimizes for orchestration leverage and reusable
workflow boundaries across domains, because it was created to be used as an
orchestration engine first.

With optional `safeslice` integration enabled, the stack can also export claim
decompositions into a benchmark-grounded statistical robustness analysis. In
engineering terms, that means theorem claims, legal claims, policy claims, and
other natural-language decomposition outputs can be checked for baseline
alignment, witness cliffs, and clarification bottlenecks against explicit
ground-truth task families rather than being treated as untestable prose.

## Current Boundaries

- `apps/cli/`: operator-facing CLI over `FormalClaimEngineAPI`
- `services/engine/`: canonical engine, storage, workflows, audit rules integration, certification API
- `services/mcp-server/`: thin MCP facade over the engine and proof job control plane
- `packages/contracts/`: canonical JSON Schemas
- `packages/contracts-py/`: generated Python bindings from the schemas
- `packages/graph-model/`: claim/assurance graph query helpers
- `packages/audit-rules/`: deterministic assurance-profile and contract-pack logic
- `packages/evidence-connectors/`: source-mapping and evaluation-evidence helpers
- `examples/`: theorem, paper, and legal fixtures used by scenario and smoke tests

This export does not currently include a desktop application, and it does not
embed a prover runtime. Real proof execution still depends on the external
`proof-assistant` / `FWP` stack.

## Requirements

- Python 3.11+
- Node.js 20+ with Corepack enabled
- `uv` and `pnpm` are handled by the bootstrap script
- provider credentials for the models configured in `settings/verification.toml`
- external proof backend access if you want real audit / proof-build runs

`just` is optional. Everything in the root `justfile` can also be run via the
Python scripts directly.

## Quick Start

Bootstrap the workspace:

```powershell
python scripts/dev/bootstrap.py
```

Run the repo checks:

```powershell
python scripts/dev/check_repo.py --mode lint
python scripts/dev/check_repo.py --mode test
```

If you prefer `just`:

```powershell
just bootstrap
just lint
just test
```

## Configuration

The main runtime configuration lives in `settings/verification.toml`.

Current configuration surfaces include:

- LLM provider/model routing by workflow role
- retry policies for LLM calls, proof builds, workflow phases, and certification transport
- proof backend transport settings and proof-job budgets
- proof-assistant host/port settings
- certification HTTP port
- optional `safeslice` integration settings for claim-decomposition export

The file comments document the env-var override convention:

```text
VERIFY_<SECTION>_<KEY>
```

In practice you will usually also need provider credentials such as:

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY`
- `FWP_AUTH_TOKEN`

## Operator CLI

The main human entrypoint is the CLI package in `apps/cli`.

Show help:

```powershell
python scripts/dev/run_uv.py run --directory apps/cli formal-claim --help
```

The current command groups are:

- `project init|open|list`
- `document ingest|upload`
- `document source list|show`
- `reference list|show|links|backlinks`
- `evidence list|show|links`
- `claim structure|analyze`
- `claim slice-task`
- `formalize dual`
- `audit run`
- `profile recompute`
- `promotion transition`
- `artifact show`
- `export bundle`
- `proof job start|get|cancel|kill`
- `proof theory list|read|write`

All result-bearing commands support `--format pretty|json`, and most also accept
`--output` to persist canonical JSON.

### Example: Create A Project And Ingest A Source Document

```powershell
python scripts/dev/run_uv.py run --directory apps/cli formal-claim project init --name legal-demo --domain legal --format json
python scripts/dev/run_uv.py run --directory apps/cli formal-claim document ingest --project-id <project_id> --file examples/legal-claim/source.md --format json
python scripts/dev/run_uv.py run --directory apps/cli formal-claim artifact show summary --project-id <project_id>
python scripts/dev/run_uv.py run --directory apps/cli formal-claim reference list --project-id <project_id> --format json
```

That flow exercises the source-mapping and external-reference path in the
engine.

### Example: Export An Optional SafeSlice Task

```powershell
python scripts/dev/run_uv.py run --directory apps/cli formal-claim claim slice-task --project-id <project_id> --format json
```

This command is gated by `[integration.safeslice]` in
`settings/verification.toml` and exports a `ClaimGraph -> safeslice TaskSpec`
mapping without changing the default audit/promotion workflow.

That matters because it gives the engine an optional way to say not only
"here is the decomposition" but also "here is an engineering-style statistical
certificate for whether that decomposition stays robust against a benchmarked
ground-truth task family". That interpretation extends beyond theorem workflows
to legal and other natural-language claim settings.

### Example: Structure And Audit A Formal Claim

```powershell
python scripts/dev/run_uv.py run --directory apps/cli formal-claim project init --name proof-demo --domain formal_proof --format json
python scripts/dev/run_uv.py run --directory apps/cli formal-claim claim structure --project-id <project_id> --text "Prove that the dispatch algorithm converges." --format json
python scripts/dev/run_uv.py run --directory apps/cli formal-claim audit run --project-id <project_id> --claim-id <claim_id> --format json
python scripts/dev/run_uv.py run --directory apps/cli formal-claim artifact show profile --project-id <project_id> --claim-id <claim_id> --format json
```

That path relies on the configured LLM providers and, for real proof-backed
verification, the external proof runtime.

### Example: Governed Proof Jobs

```powershell
python scripts/dev/run_uv.py run --directory apps/cli formal-claim proof job start --session-name demo --session-dir .\some-session --format json
python scripts/dev/run_uv.py run --directory apps/cli formal-claim proof job get --job-id <job_id> --format json
python scripts/dev/run_uv.py run --directory apps/cli formal-claim proof job cancel --job-id <job_id> --format json
```

The CLI also exposes `proof theory list|read|write` for generated theory files.

## MCP Server

The MCP service is a thin facade over the same engine.

Run it from source:

```powershell
python scripts/dev/run_uv.py run --directory services/mcp-server formal-claim-mcp-server
```

Canonical tool surface:

- `project.create`
- `project.open`
- `project.list`
- `document.ingest`
- `claim.structure`
- `formalize.dual`
- `audit.run`
- `profile.recompute`
- `promotion.transition`
- `bundle.export`
- `proof.run.start`
- `job.get`
- `job.cancel`
- `job.kill`
- `claim.trace_forward`
- `claim.trace_backward`
- `graph.detect_gaps`
- `claim.assess`
- `graph.export`

Read-only resources:

- `project://{project_id}`
- `claim-graph://{project_id}`
- `claim-graph://{project_id}/{revision_id}`
- `profile://{project_id}/{claim_id}`
- `profile://{project_id}/{claim_id}/{revision_id}`
- `audit-report://{project_id}/{claim_id}`
- `bundle://{project_id}`

## Engine Package And Certification API

The engine package also has a narrower internal CLI:

```powershell
python scripts/dev/run_uv.py run --directory services/engine formal-claim-engine --help
```

That CLI currently exposes:

- `run`
- `validate`
- `list`
- `show`

For integration callers, `services/engine` also includes:

- `formal_claim_engine.certified(...)`: full certification pipeline
- `formal_claim_engine.verify_only(...)`: proof-only verification path
- `formal_claim_engine.certification_http`: lightweight HTTP wrapper

Run the HTTP wrapper:

```powershell
python scripts/dev/run_uv.py run --directory services/engine python -m formal_claim_engine.certification_http --port 8321
```

HTTP endpoints:

- `POST /api/certify`
- `POST /api/verify`
- `GET /api/config`
- `GET /api/health`

## Contracts And Generated Bindings

The canonical schemas live in `packages/contracts/schemas/`.

Regenerate the Python bindings with:

```powershell
python scripts/dev/run_uv.py run --python 3.12 --group dev python scripts/contracts/generate_bindings.py
```

At the moment, the checked-in generator only emits the Python bindings under
`packages/contracts-py/`.

## Examples And Tests

Fixtures and demos live under `examples/`:

- `examples/theorem-audit/`
- `examples/paper-claim/`
- `examples/legal-claim/`

The main repo-wide verification path is:

```powershell
python scripts/dev/check_repo.py --mode test
```

That path exercises schema conformance, artifact generation, package builds, and
the integration/e2e smoke tests that cover the current CLI, engine, MCP, and
proof-control surfaces.
