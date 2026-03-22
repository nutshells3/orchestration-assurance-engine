"""Local smoke for GitHub backlog issue sync planning and verification."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "scripts" / "github" / "seed_backlog.py").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from seed sync test.")


REPO_ROOT = resolve_repo_root()
SEED_SCRIPT = REPO_ROOT / "scripts" / "github" / "seed_backlog.py"


def load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_backlog_test", str(SEED_SCRIPT))
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load seed_backlog.py for testing.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_issue_snapshot(
    *,
    number: int,
    title: str,
    body: str,
    labels: list[str],
    milestone_title: str,
    state: str,
    state_reason: str | None = None,
) -> dict[str, object]:
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": label} for label in labels],
        "milestone": {"title": milestone_title, "number": 5},
        "state": state,
        "state_reason": state_reason,
    }


def main() -> None:
    seed = load_seed_module()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generated = root / "scripts" / "github" / "generated" / "issue-bodies"
        generated.mkdir(parents=True, exist_ok=True)

        body_path = generated / "M4-03.md"
        desired_body = (
            "<!-- backlog-id: M4-03 -->\n"
            "# [M4-03] Implement dual-formalization workflow with divergence capture\n\n"
            "## Current Baseline\n"
            "- Status: `done`\n"
        )
        body_path.write_text(desired_body, encoding="utf-8")

        manifest_path = root / "scripts" / "github" / "generated" / "backlog_seed_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{}", encoding="utf-8")

        milestones = {
            "M4 Engine Workflows": {"number": 5, "title": "M4 Engine Workflows"},
        }
        issue_store: dict[int, dict[str, object]] = {
            24: make_issue_snapshot(
                number=24,
                title="[M4-03] Implement dual-formalization workflow with divergence capture",
                body="<!-- backlog-id: M4-03 -->\nold body",
                labels=["engine"],
                milestone_title="M4 Engine Workflows",
                state="open",
            )
        }
        next_issue_number = 25

        def fake_fetch_existing_issues(_repo_root: Path, _repo: str):
            return [json.loads(json.dumps(snapshot)) for snapshot in issue_store.values()]

        def fake_fetch_existing_milestones(_repo_root: Path, _repo: str):
            return milestones

        def fake_github_api_json(
            _repo_root: Path,
            method: str,
            path: str,
            *,
            payload: dict[str, object] | None = None,
        ):
            nonlocal next_issue_number
            if method == "GET" and path.startswith("/repos/test-owner/test-repo/issues/"):
                issue_number = int(path.rsplit("/", 1)[-1])
                return json.loads(json.dumps(issue_store[issue_number]))
            if method == "PATCH" and path.startswith("/repos/test-owner/test-repo/issues/"):
                issue_number = int(path.rsplit("/", 1)[-1])
                snapshot = issue_store[issue_number]
                assert payload is not None
                if "title" in payload:
                    snapshot["title"] = payload["title"]
                if "body" in payload:
                    snapshot["body"] = payload["body"]
                if "labels" in payload:
                    snapshot["labels"] = [
                        {"name": str(label)} for label in list(payload["labels"])
                    ]
                if "milestone" in payload:
                    milestone_number = payload["milestone"]
                    matched = next(
                        (
                            milestone
                            for milestone in milestones.values()
                            if int(milestone["number"]) == int(milestone_number)
                        ),
                        None,
                    )
                    snapshot["milestone"] = matched
                if "state" in payload:
                    snapshot["state"] = payload["state"]
                if "state_reason" in payload:
                    snapshot["state_reason"] = payload["state_reason"]
                return json.loads(json.dumps(snapshot))
            if method == "POST" and path == "/repos/test-owner/test-repo/issues":
                assert payload is not None
                issue_number = next_issue_number
                next_issue_number += 1
                milestone = next(
                    (
                        milestone_data
                        for milestone_data in milestones.values()
                        if int(milestone_data["number"]) == int(payload["milestone"])
                    ),
                    None,
                )
                snapshot = make_issue_snapshot(
                    number=issue_number,
                    title=str(payload["title"]),
                    body=str(payload["body"]),
                    labels=[str(label) for label in list(payload["labels"])],
                    milestone_title=str((milestone or {}).get("title") or ""),
                    state="open",
                )
                issue_store[issue_number] = snapshot
                return json.loads(json.dumps(snapshot))
            raise AssertionError(f"Unexpected GitHub API call: {method} {path}")

        issues = [
            {
                "id": "M4-03",
                "title": "[M4-03] Implement dual-formalization workflow with divergence capture",
                "status": "done",
                "milestone": "M4 Engine Workflows",
                "labels": ["engine", "formalization", "workflow"],
                "body_path": "issue-bodies/M4-03.md",
            },
            {
                "id": "M4-04",
                "title": "[M4-04] Implement audit workflow that composes runner outputs and deterministic rules",
                "status": "planned",
                "milestone": "M4 Engine Workflows",
                "labels": ["audit", "engine", "workflow"],
                "body_path": "issue-bodies/M4-03.md",
            },
        ]

        with patch.object(seed, "fetch_existing_issues", side_effect=fake_fetch_existing_issues), patch.object(
            seed,
            "fetch_existing_milestones",
            side_effect=fake_fetch_existing_milestones,
        ), patch.object(seed, "github_api_json", side_effect=fake_github_api_json):
            seed.seed_issues(
                repo_root=root,
                manifest_path=manifest_path,
                repo="test-owner/test-repo",
                issues=issues,
                dry_run=False,
            )

        assert issue_store[24]["state"] == "closed", issue_store[24]
        assert issue_store[24]["state_reason"] == "completed", issue_store[24]
        assert issue_store[24]["body"] == desired_body, issue_store[24]
        assert sorted(label["name"] for label in issue_store[24]["labels"]) == [
            "engine",
            "formalization",
            "workflow",
        ], issue_store[24]

        created = issue_store[25]
        assert created["title"].startswith("[M4-04]"), created
        assert created["state"] == "open", created
        assert sorted(label["name"] for label in created["labels"]) == [
            "audit",
            "engine",
            "workflow",
        ], created


if __name__ == "__main__":
    main()
