# Runtime Boundary Contract

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
- `bundle.export`
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
