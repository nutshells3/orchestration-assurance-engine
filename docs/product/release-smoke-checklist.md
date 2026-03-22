# Release Smoke Checklist

Run `just release-build` and `just release-smoke` before cutting an internal tag.

Required checks:

- repo lint passes
- release artifact manifest is generated at `.tmp/dist/release/release-manifest.json`
- desktop frontend build passes
- desktop Tauri cargo check passes
- desktop release binary builds
- engine, mcp-server, and CLI wheels build
- FWP proof-job regression passes
- backlog generator and seed sync still pass in dry-run mode when GitHub credentials are available

Proof-control checks:

- start a bounded proof job
- poll until the job is visible with runtime and log paths
- cancel a long-running job and confirm terminal reason is `cancelled` or `abort.escalation_pending`
- kill a long-running job and confirm terminal reason is `kill_requested`
- verify the proof host reports no lingering governed run after terminal state
- verify stdout/stderr paths and session fingerprint are still readable after completion

Known limitations to record in the release note:

- release artifacts do not bundle the external proof-assistant server
- proof job control remains a thin FWP/proof-assistant pass-through
- managed proof jobs are control-plane jobs only; audit/promotion semantics remain outside the desktop shell
