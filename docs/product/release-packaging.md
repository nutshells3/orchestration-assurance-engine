# Release Packaging

Use this repository as a repeatable local workbench, not an ad hoc prototype.

Local environment:

- `just bootstrap`
- `python scripts/dev/check_repo.py --mode test`

Release build:

- `python scripts/release/build_release_artifacts.py`
- output root: `.tmp/dist/release`
- manifest: `.tmp/dist/release/release-manifest.json`

Expected artifacts:

- desktop binary from `apps/desktop/src-tauri/target/release`
- CLI wheel
- engine wheel
- MCP server wheel

Release smoke:

- `python scripts/release/smoke_release.py`
- verifies lint, FWP-backed proof-job regression, scenario replay parity, and packaging output

Proof-control closure:

- build a proof job through `FWP`
- poll until job state and log paths are visible
- cancel a run and verify cleanup
- kill a run and verify cleanup
- confirm terminal job state preserves fingerprints and log paths

This release does not bundle the external `proof-assistant` server. Treat that host as an independently versioned deployment artifact.

