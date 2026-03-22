# GitHub Backlog Seeding

This directory turns [FORMAL_CLAIM_MONOREPO_BACKLOG.csv](docs/FORMAL_CLAIM_MONOREPO_BACKLOG.csv) into GitHub seeding artifacts.

The backlog CSV is the operational source of truth for:

- `status`: `planned | partial | in_progress | done | blocked`
- `wave`: execution wave such as `foundation` or `migration`
- `execution_mode`: `bootstrap | normalize | extract | absorb | replace | implement`
- `prototype_basis`: donor assets or prototype reality that the issue should acknowledge

## Generate artifacts

```powershell
python scripts/github/generate_backlog_scripts.py
```

Generated files are written to [scripts/github/generated](scripts/github/generated):

- `backlog_seed_manifest.json`
- `seed_labels_and_milestones.ps1`
- `create_backlog_issues.ps1`
- `issue-bodies/*.md`

Issue bodies include a hidden backlog marker in the first line:

```html
<!-- backlog-id: M0-01 -->
```

That marker is the canonical issue identity for re-seeding. Title matches are treated only as collision checks.

## Preferred runner

Use the cross-platform Python runner. It performs preflight checks before any mutation:

- `gh auth status`
- target repo exists
- backlog CSV exists
- generated issue bodies exist
- no duplicate backlog IDs
- no dependency graph cycle

Issue synchronization is API-backed and self-verifying. Each issue update now patches
title, body, labels, milestone, and open/closed state through the GitHub REST API,
then immediately re-reads the issue and fails if the remote body or status still
drifts from the generated backlog artifact.

Preflight only:

```powershell
python scripts/github/seed_backlog.py --repo OWNER/REPO --mode preflight
```

Seed labels and milestones:

```powershell
python scripts/github/seed_backlog.py --repo OWNER/REPO --mode metadata --dry-run
```

Seed only the foundation wave issues:

```powershell
python scripts/github/seed_backlog.py --repo OWNER/REPO --mode issues --wave foundation --dry-run
```

Seed everything:

```powershell
python scripts/github/seed_backlog.py --repo OWNER/REPO --mode all --dry-run
```

Remove `--dry-run` once the printed commands look correct.

## PowerShell fallback

The generated PowerShell scripts remain available as a fallback:

- [seed_labels_and_milestones.ps1](scripts/github/generated/seed_labels_and_milestones.ps1)
- [create_backlog_issues.ps1](scripts/github/generated/create_backlog_issues.ps1)

They use the same hidden backlog marker strategy as the Python runner.
