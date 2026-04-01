# Formal Claim Workbench

This directory is the canonical monorepo root for the Formal Claim Workbench.

Current state:

- `formal-claim` now assumes the frozen proof stack `proof-assistant -> FWP -> formal-claim -> IDE/product shell`.
- backend-neutral proof transport lives in `FWP`; backend-specific runtime control lives in `proof-assistant`.
- this repo keeps only assurance semantics, thin transports, and operator/demo surfaces.
- `docs/FORMAL_CLAIM_MONOREPO_BACKLOG.csv` is the planning source of truth for waves, statuses, and issue seeding.
- `scripts/github/` generates and seeds backlog issues once the final GitHub repo boundary is fixed.

## Monorepo Commands

Use [`justfile`](C:\Users\madab\Downloads\Project\formal-claim\justfile) as the top-level entrypoint:

- `just bootstrap`
- `just contracts-generate`
- `just test`
- `just lint`
- `just release-build`
- `just release-smoke`

`bootstrap` prepares the Python and Node workspace tooling for this root. `contracts-generate` regenerates Python and TypeScript bindings from the canonical schemas with Python 3.12. `test` validates the repo scaffold, regenerates backlog and contract artifacts, and runs schema conformance checks. `lint` performs syntax and repository hygiene checks over the managed root files. `release-build` emits the desktop binary and repo-local wheels into `.tmp/dist/release`. `release-smoke` runs the operator-facing packaging/build checks, including FWP-backed proof-job stop-control regression.

## Managed Proof Runs

The desktop, CLI, and MCP surfaces expose bounded proof jobs through the FWP seam.

- CLI: `formal-claim proof job start|get|cancel|kill`
- CLI: `formal-claim proof theory list|read|write`
- MCP: `proof.run.start`, `job.get`, `job.cancel`, `job.kill`

Operational and architectural details live in:

- [managed-proof-runs.md](C:\Users\madab\Downloads\Project\formal-claim\docs\architecture\managed-proof-runs.md)
- [formal-claim-stack-positioning.md](C:\Users\madab\Downloads\Project\formal-claim\docs\architecture\formal-claim-stack-positioning.md)
- [fwp-seam-extraction-plan.md](C:\Users\madab\Downloads\Project\formal-claim\docs\architecture\fwp-seam-extraction-plan.md)
- [operator-proof-run-controls.md](C:\Users\madab\Downloads\Project\formal-claim\docs\policies\operator-proof-run-controls.md)
- [agent-runtime-contract.md](C:\Users\madab\Downloads\Project\formal-claim\docs\product\agent-runtime-contract.md)
- [release-packaging.md](C:\Users\madab\Downloads\Project\formal-claim\docs\product\release-packaging.md)
- [release-smoke-checklist.md](C:\Users\madab\Downloads\Project\formal-claim\docs\product\release-smoke-checklist.md)

## Source Document Import

Document ingest can now preserve stable source-document identity and normalized
citation anchors.

- CLI: `formal-claim document ingest --project-id <id> --file <path>`
- CLI: `formal-claim document upload --project-id <id> --name <file> --media-type <mime> < bytes-on-stdin`
- CLI: `formal-claim document source list --project-id <id>`
- CLI: `formal-claim document source show --project-id <id> --document-id <document_id>`

`document ingest --text` still exists for inline analysis, but local file import
is the product-facing entrypoint that records stable `source_ref`, excerpt
anchors, and source-mapping bundle revisions inside the engine-owned store.
Desktop now exposes the same path through a paste/upload/drag-drop intake panel.

## Root Layout

The canonical root now contains:

- `apps/`
- `services/`
- `packages/`
- `integrations/`
- `examples/`
- `tests/`
- `docs/`
- `scripts/`

The M0 wave freezes this boundary before any donor package migration or UI work begins.
