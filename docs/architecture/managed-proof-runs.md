# Managed Proof Runs

`services/engine/src/formal_claim_engine/proof_control.py` owns the local
control-plane record for bounded proof runs started from this repo.

Execution itself lives below the repo boundary:

- `formal-claim` stores job metadata and renders read models
- `FWP` transports run control
- `proof-assistant` owns backend adapters, subprocess lifecycle, and prover
  execution

The control boundary is:

1. `ProofControlPlane.start_job()` submits `run.start` through the FWP client.
2. `FWP` routes the governed run to a local or remote `proof-assistant` host.
3. `proof-assistant` launches the backend process, enforces budgets, and owns
   cancel or kill escalation.
4. `ProofControlPlane` persists `<data_dir>/proof_jobs/<job_id>.json` with the
   last observed control-plane state.
5. `services/mcp-server` exposes the same job through `proof.run.start`,
   `job.get`, `job.cancel`, and `job.kill`.
6. `apps/cli` wraps the same proof-control plane through
   `formal-claim proof job ...`.
7. `apps/desktop` stays presentational: it edits `.thy` files, starts,
   polls, stops runs, and renders returned logs, artifact refs, and
   fingerprints.

## Run Governance

- `wall_ms`: absolute upper bound for a governed run.
- `idle_ms`: maximum silent period before the proof host marks the run idle.
- `cancel_grace_ms`: grace window after `cancel` before escalation to hard kill.
- polling cadence on the `formal-claim` side is a client concern, not the source of truth.

Timeout and stop reasons are recorded in `termination_reason`:

- `completed`
- `cancelled`
- `abort.escalation_pending`
- `kill_requested`
- `wall_timeout`
- `idle_timeout`
- backend-defined failure signal

## Ownership

- Canonical proof semantics still belong to engine and the lower FWP or
  `proof-assistant` layers.
- MCP is a control-plane adapter, not a second workflow owner.
- Desktop is a thin shell over CLI/MCP transport and local file access.
- No frontend heuristic decides whether a run is safe, promotable, or valid.

## State Contract

Each governed run persists:

- request metadata
- workspace ref and target identity
- command summary
- artifact refs or exported artifact paths
- theory file paths seen in the session directory
- last observed runtime and idle counters
- session fingerprint
- final proof result envelope

The persisted state is the source of truth for polling on the `formal-claim`
side. Backend subprocess internals remain the responsibility of
`proof-assistant`; clients must not infer terminal state from UI timers or
transport lifetime.
