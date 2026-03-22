# GitHub Governance

Canonical GitHub repository: [nutshells3/proof-claim](https://github.com/nutshells3/proof-claim)

## Source Of Truth

- Backlog planning source: [`docs/FORMAL_CLAIM_MONOREPO_BACKLOG.csv`](docs/FORMAL_CLAIM_MONOREPO_BACKLOG.csv)
- Backlog issue generation: [`scripts/github/generate_backlog_scripts.py`](scripts/github/generate_backlog_scripts.py)
- Cross-platform seeding runner: [`scripts/github/seed_backlog.py`](scripts/github/seed_backlog.py)
- Ownership routing: [`CODEOWNERS`](.github/CODEOWNERS)

The backlog CSV is authoritative for:

- wave
- status
- execution mode
- milestone
- labels
- prototype basis

## Seeding Flow

Run these commands from the monorepo root after authenticating GitHub CLI:

```powershell
gh auth login
python scripts/github/generate_backlog_scripts.py
python scripts/github/seed_backlog.py --repo nutshells3/proof-claim --mode preflight
python scripts/github/seed_backlog.py --repo nutshells3/proof-claim --mode all --dry-run
python scripts/github/seed_backlog.py --repo nutshells3/proof-claim --mode all
```

Current blocker in this local environment:

- `gh` is installed, but `gh auth status` is not authenticated yet, so live seeding is blocked until login completes.

## Labels And Milestones

Labels and milestones are seeded from the backlog manifest generated under [`scripts/github/generated`](scripts/github/generated).

Milestones:

- `M0 Foundation`
- `M1 Migration`
- `M2 Core Domain`
- `M3 Isabelle Runner`
- `M4 Engine Workflows`
- `M5 MCP`
- `M6 CLI`
- `M7 Desktop`
- `M8 Evidence`
- `M9 Hardening`

Label taxonomy is defined mechanically from the backlog CSV labels column. Do not hand-edit label meaning in GitHub without also updating the backlog source.

## Ownership

The backlog owner lane determines the intended review lane. The current concrete GitHub repo principal is `@nutshells3`, so [`CODEOWNERS`](.github/CODEOWNERS) routes all paths to that account while preserving lane semantics in comments.

Current canonical ownership intent:

- contracts and policy assets route through the contracts or policy lane
- proof backend and Isabelle integration assets route through the proof lane
- engine, graph, and audit packages route through backend lanes
- MCP and CLI surfaces route through platform or DevEx lanes
- desktop assets route through the frontend lane

When additional collaborators or teams exist, replace the `@nutshells3` entries with lane-specific owners without changing the path boundaries.
