# Human CLI

`apps/cli` is the operator-facing front door for the Formal Claim Workbench.

Boundary:

- the CLI is a thin wrapper over `FormalClaimEngineAPI`
- it does not own workflow state, storage, or alternate domain models
- human-friendly commands live here; machine-first service CLIs remain in `services/engine` or the external `proof-assistant` repo

Current command groups:

- `project init|open|list`
- `document ingest|upload`
- `document source list|show`
- `reference list|show|links|backlinks`
- `claim structure`
- `formalize dual`
- `audit run`
- `profile recompute`
- `promotion transition`
- `artifact show`
- `export bundle`
- `proof job start|get|cancel|kill`
- `proof theory list|read|write`

All commands support `--format pretty|json`; result-bearing commands also accept `--output` to persist canonical JSON.
