# formal-claim-mcp-server

Canonical MCP facade for the Formal Claim Workbench.

Design boundary:

- `services/mcp-server` is a thin control-plane facade over `FormalClaimEngineAPI`
- canonical artifacts stay owned by `services/engine` and the engine artifact store
- MCP resources are read-only; all mutation goes through tools
- long-running workflow calls return `job_id` handles and are polled with `job.get`

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
- `bundle.export` *(compatibility-only; prefer `trace.export`)*
- `trace.export`
- `prefix.extract`
- `proof.run.start`
- `job.get`
- `job.cancel`
- `job.kill`
- `claim.trace_forward`
- `claim.trace_backward`
- `graph.detect_gaps`
- `claim.assess`
- `graph.export`

Read-only resource surface:

- `project://{project_id}`
- `claim-graph://{project_id}`
- `claim-graph://{project_id}/{revision_id}`
- `profile://{project_id}/{claim_id}`
- `profile://{project_id}/{claim_id}/{revision_id}`
- `audit-report://{project_id}/{claim_id}`
- `bundle://{project_id}` *(compatibility-only; prefer trace/transition-log/sidecar resources)*
- `trace://{project_id}`
- `transition-log://{project_id}`
- `sidecar://{project_id}`

Operational rules:

- tool responses use structured `ok/error` envelopes with machine-readable error codes
- queue and concurrency limits are controlled by `FORMAL_CLAIM_MCP_MAX_QUEUED_JOBS` and `FORMAL_CLAIM_MCP_MAX_CONCURRENT_JOBS`
- proof job tools remain thin pass-throughs to `FWP` and the external `proof-assistant` runtime
- local compatibility wrappers remain in `server.py`, but they are not the canonical MCP tool surface
