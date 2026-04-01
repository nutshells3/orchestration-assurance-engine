#!/usr/bin/env python3
"""Cross-platform GitHub backlog seeding runner with preflight checks."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Dict, List, Sequence


BACKLOG_MARKER_RE = re.compile(r"<!--\s*backlog-id:\s*([A-Za-z0-9._-]+)\s*-->")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
VALID_STATUSES = {"planned", "partial", "in_progress", "done", "blocked"}
ISSUE_URL_RE = re.compile(r"/issues/(\d+)(?:\D|$)")
GITHUB_API_ROOT = "https://api.github.com"
_TOKEN_CACHE: dict[str, str] = {}


def resolve_gh_executable() -> str:
    candidate = shutil.which("gh")
    if candidate:
        return candidate

    fallback_paths = [
        Path(os.environ.get("ProgramFiles", "")) / "GitHub CLI" / "gh.exe",
        Path(os.environ.get("ProgramW6432", "")) / "GitHub CLI" / "gh.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "GitHub CLI" / "gh.exe",
    ]
    for path in fallback_paths:
        if path.exists():
            return str(path)

    raise RuntimeError("gh CLI is required but was not found on PATH.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed GitHub labels, milestones, and issues from the generated backlog manifest."
    )
    parser.add_argument("--repo", required=True, help="GitHub repository in OWNER/REPO form.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root containing the backlog CSV and generated files.",
    )
    parser.add_argument(
        "--manifest",
        default="scripts/github/generated/backlog_seed_manifest.json",
        help="Path to the generated backlog manifest, relative to --repo-root.",
    )
    parser.add_argument(
        "--mode",
        default="all",
        choices=["preflight", "metadata", "issues", "all"],
        help="Which seeding step to run.",
    )
    parser.add_argument(
        "--wave",
        action="append",
        default=[],
        help="Restrict issue seeding to one or more wave values from the manifest.",
    )
    parser.add_argument(
        "--status",
        action="append",
        default=[],
        choices=sorted(VALID_STATUSES),
        help="Restrict issue seeding to one or more backlog statuses.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print mutating gh commands instead of executing them.",
    )
    return parser.parse_args()


def run_gh(
    args: Sequence[str],
    cwd: Path,
    *,
    capture_output: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GH_FORCE_TTY"] = "0"
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [resolve_gh_executable(), *args],
        cwd=str(cwd),
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
        env=env,
    )


def resolve_gh_token(repo_root: Path) -> str:
    cache_key = str(repo_root)
    if cache_key in _TOKEN_CACHE:
        return _TOKEN_CACHE[cache_key]
    result = run_gh(["auth", "token"], repo_root)
    token = (result.stdout or "").strip()
    if not token:
        raise RuntimeError("gh auth token returned an empty token.")
    _TOKEN_CACHE[cache_key] = token
    return token


def github_api_json(
    repo_root: Path,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
) -> dict[str, object] | list[dict[str, object]]:
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{GITHUB_API_ROOT}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {resolve_gh_token(repo_root)}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "formal-claim-backlog-seeder",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # pragma: no cover - network error path
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API {method} {path} failed with {exc.code}: {details}"
        ) from exc
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if isinstance(parsed, (dict, list)):
        return parsed
    raise RuntimeError(f"Unexpected GitHub API payload for {method} {path}: {parsed!r}")


def parse_json_output(output: str) -> object:
    cleaned = ANSI_RE.sub("", output).strip()
    if not cleaned:
        return []
    return json.loads(cleaned)


def load_manifest(manifest_path: Path) -> Dict[str, object]:
    with manifest_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_dependency_cycle(issues: Sequence[Dict[str, object]]) -> List[str]:
    by_id = {issue["id"]: issue for issue in issues}
    state: Dict[str, str] = {}
    stack: List[str] = []

    def visit(node_id: str) -> List[str]:
        state[node_id] = "visiting"
        stack.append(node_id)
        for dependency in by_id[node_id].get("depends_on", []):
            if dependency not in by_id:
                continue
            dependency_state = state.get(dependency)
            if dependency_state is None:
                cycle = visit(dependency)
                if cycle:
                    return cycle
            elif dependency_state == "visiting":
                start = stack.index(dependency)
                return stack[start:] + [dependency]
        stack.pop()
        state[node_id] = "done"
        return []

    for issue in issues:
        issue_id = issue["id"]
        if state.get(issue_id) is None:
            cycle = visit(issue_id)
            if cycle:
                return cycle
    return []


def filter_issues(
    issues: Sequence[Dict[str, object]],
    waves: Sequence[str],
    statuses: Sequence[str],
) -> List[Dict[str, object]]:
    selected = list(issues)
    if waves:
        allowed_waves = set(waves)
        selected = [issue for issue in selected if issue["wave"] in allowed_waves]
    if statuses:
        allowed_statuses = set(statuses)
        selected = [
            issue for issue in selected if issue["status"] in allowed_statuses
        ]
    return selected


def run_preflight(
    repo_root: Path,
    manifest_path: Path,
    manifest: Dict[str, object],
    repo: str,
    selected_issues: Sequence[Dict[str, object]],
) -> None:
    resolve_gh_executable()

    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found: {manifest_path}")

    source_csv = repo_root / manifest["source_csv"]
    if not source_csv.exists():
        raise RuntimeError(f"Backlog CSV not found: {source_csv}")

    manifest_issues = manifest["issues"]
    issue_ids = [issue["id"] for issue in manifest_issues]
    duplicates = [item_id for item_id, count in Counter(issue_ids).items() if count > 1]
    if duplicates:
        raise RuntimeError(
            "Duplicate backlog IDs in manifest: " + ", ".join(sorted(duplicates))
        )

    known_ids = set(issue_ids)
    missing_dependencies = []
    for issue in manifest_issues:
        for dependency in issue.get("depends_on", []):
            if dependency not in known_ids:
                missing_dependencies.append(f"{issue['id']} -> {dependency}")
    if missing_dependencies:
        raise RuntimeError(
            "Unknown dependencies in manifest:\n- " + "\n- ".join(missing_dependencies)
        )

    cycle = find_dependency_cycle(manifest_issues)
    if cycle:
        raise RuntimeError("Dependency cycle detected: " + " -> ".join(cycle))

    missing_bodies = []
    for issue in selected_issues:
        body_path = manifest_path.parent / issue["body_path"]
        if not body_path.exists():
            missing_bodies.append(str(body_path))
    if missing_bodies:
        raise RuntimeError(
            "Missing generated issue bodies:\n- " + "\n- ".join(missing_bodies)
        )

    run_gh(["auth", "status"], repo_root)
    run_gh(["repo", "view", repo, "--json", "nameWithOwner"], repo_root)


def fetch_existing_labels(repo_root: Path, repo: str) -> Dict[str, Dict[str, object]]:
    result = run_gh(["api", f"repos/{repo}/labels?per_page=100"], repo_root)
    labels = parse_json_output(result.stdout)
    return {label["name"]: label for label in labels}


def fetch_existing_milestones(repo_root: Path, repo: str) -> Dict[str, Dict[str, object]]:
    result = run_gh(
        ["api", f"repos/{repo}/milestones?state=all&per_page=100"], repo_root
    )
    milestones = parse_json_output(result.stdout)
    return {milestone["title"]: milestone for milestone in milestones}


def desired_milestone_states(
    issues: Sequence[Dict[str, object]],
) -> Dict[str, str]:
    grouped: Dict[str, List[str]] = {}
    for issue in issues:
        milestone = str(issue.get("milestone") or "").strip()
        if not milestone:
            continue
        grouped.setdefault(milestone, []).append(str(issue.get("status") or "planned"))

    states: Dict[str, str] = {}
    for milestone, statuses in grouped.items():
        if statuses and all(status == "done" for status in statuses):
            states[milestone] = "closed"
        else:
            states[milestone] = "open"
    return states


def fetch_existing_issues(repo_root: Path, repo: str) -> List[Dict[str, object]]:
    result = run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            "1000",
            "--json",
            "number,title,body,labels,milestone,state",
        ],
        repo_root,
    )
    return parse_json_output(result.stdout)


def extract_issue_number(output: str) -> int:
    match = ISSUE_URL_RE.search((output or "").strip())
    if not match:
        raise RuntimeError(f"Unable to parse issue number from gh output: {output!r}")
    return int(match.group(1))


def patch_issue_milestone(
    repo_root: Path,
    repo: str,
    issue_number: int,
    milestone_number: int,
    dry_run: bool,
) -> None:
    args = [
        "api",
        f"repos/{repo}/issues/{issue_number}",
        "--method",
        "PATCH",
        "-f",
        f"milestone={milestone_number}",
    ]
    if dry_run:
        print("DRY RUN:", "gh", *args)
    else:
        run_gh(args, repo_root)


def patch_issue_state(
    repo_root: Path,
    repo: str,
    issue_number: int,
    desired_state: str,
    dry_run: bool,
) -> None:
    args = [
        "api",
        f"repos/{repo}/issues/{issue_number}",
        "--method",
        "PATCH",
        "-f",
        f"state={desired_state}",
    ]
    if desired_state == "closed":
        args.extend(["-f", "state_reason=completed"])
    if dry_run:
        print("DRY RUN:", "gh", *args)
    else:
        run_gh(args, repo_root)


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fetch_issue_snapshot(repo_root: Path, repo: str, issue_number: int) -> dict[str, object]:
    payload = github_api_json(repo_root, "GET", f"/repos/{repo}/issues/{issue_number}")
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected GitHub issue payload, received {type(payload)!r}")
    return payload


def patch_issue_payload(
    repo_root: Path,
    repo: str,
    issue_number: int,
    payload: dict[str, object],
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        print(
            "DRY RUN: PATCH",
            f"/repos/{repo}/issues/{issue_number}",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        return
    github_api_json(
        repo_root,
        "PATCH",
        f"/repos/{repo}/issues/{issue_number}",
        payload=payload,
    )


def create_issue_payload(
    repo_root: Path,
    repo: str,
    payload: dict[str, object],
    *,
    dry_run: bool,
) -> int | None:
    if dry_run:
        print(
            "DRY RUN: POST",
            f"/repos/{repo}/issues",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
        return None
    created = github_api_json(
        repo_root,
        "POST",
        f"/repos/{repo}/issues",
        payload=payload,
    )
    if not isinstance(created, dict) or "number" not in created:
        raise RuntimeError("GitHub issue create response did not include an issue number.")
    return int(created["number"])


def verify_issue_snapshot(
    repo_root: Path,
    repo: str,
    issue_number: int,
    *,
    expected_title: str,
    expected_body: str,
    expected_labels: Sequence[str],
    expected_milestone: str,
    expected_state: str,
) -> None:
    snapshot = fetch_issue_snapshot(repo_root, repo, issue_number)
    actual_title = str(snapshot.get("title") or "")
    actual_body = normalize_newlines(str(snapshot.get("body") or ""))
    actual_labels = sorted(
        str(label.get("name") or "")
        for label in list(snapshot.get("labels") or [])
        if isinstance(label, dict)
    )
    milestone = snapshot.get("milestone") or {}
    actual_milestone = ""
    if isinstance(milestone, dict):
        actual_milestone = str(milestone.get("title") or "").strip()
    actual_state = str(snapshot.get("state") or "open").strip().lower()
    actual_state_reason = str(snapshot.get("state_reason") or "").strip().lower()
    expected_state_reason = "completed" if expected_state == "closed" else ""
    mismatches = []
    if actual_title != expected_title:
        mismatches.append(f"title expected {expected_title!r}, found {actual_title!r}")
    if actual_body != normalize_newlines(expected_body):
        mismatches.append("body did not match the generated issue body")
    if actual_labels != sorted(expected_labels):
        mismatches.append(
            f"labels expected {sorted(expected_labels)!r}, found {actual_labels!r}"
        )
    if actual_milestone != expected_milestone:
        mismatches.append(
            f"milestone expected {expected_milestone!r}, found {actual_milestone!r}"
        )
    if actual_state != expected_state:
        mismatches.append(
            f"state expected {expected_state!r}, found {actual_state!r}"
        )
    if actual_state_reason != expected_state_reason:
        mismatches.append(
            f"state_reason expected {expected_state_reason!r}, found {actual_state_reason!r}"
        )
    if mismatches:
        raise RuntimeError(
            f"GitHub issue #{issue_number} failed verification:\n- "
            + "\n- ".join(mismatches)
        )


def seed_metadata(
    repo_root: Path,
    repo: str,
    labels: Sequence[Dict[str, str]],
    milestones: Sequence[Dict[str, str]],
    milestone_states: Dict[str, str],
    dry_run: bool,
) -> None:
    existing_labels = fetch_existing_labels(repo_root, repo)
    for label in labels:
        if label["name"] in existing_labels:
            args = [
                "api",
                f"repos/{repo}/labels/{label['name']}",
                "--method",
                "PATCH",
                "-f",
                f"new_name={label['name']}",
                "-f",
                f"color={label['color']}",
                "-f",
                f"description={label['description']}",
            ]
            action = "update label"
        else:
            args = [
                "api",
                f"repos/{repo}/labels",
                "--method",
                "POST",
                "-f",
                f"name={label['name']}",
                "-f",
                f"color={label['color']}",
                "-f",
                f"description={label['description']}",
            ]
            action = "create label"
        if dry_run:
            print("DRY RUN:", "gh", *args)
        else:
            run_gh(args, repo_root)
            print(f"{action}: {label['name']}")

    existing_milestones = fetch_existing_milestones(repo_root, repo)
    for milestone in milestones:
        desired_state = milestone_states.get(milestone["title"], "open")
        if milestone["title"] in existing_milestones:
            number = str(existing_milestones[milestone["title"]]["number"])
            args = [
                "api",
                f"repos/{repo}/milestones/{number}",
                "--method",
                "PATCH",
                "-f",
                f"title={milestone['title']}",
                "-f",
                f"description={milestone['description']}",
                "-f",
                f"state={desired_state}",
            ]
            action = f"{desired_state} milestone"
        else:
            args = [
                "api",
                f"repos/{repo}/milestones",
                "--method",
                "POST",
                "-f",
                f"title={milestone['title']}",
                "-f",
                f"description={milestone['description']}",
                "-f",
                f"state={desired_state}",
            ]
            action = f"create milestone ({desired_state})"
        if dry_run:
            print("DRY RUN:", "gh", *args)
        else:
            run_gh(args, repo_root)
            print(f"{action}: {milestone['title']}")


def seed_issues(
    repo_root: Path,
    manifest_path: Path,
    repo: str,
    issues: Sequence[Dict[str, object]],
    dry_run: bool,
) -> None:
    existing_issues = fetch_existing_issues(repo_root, repo)
    existing_milestones = fetch_existing_milestones(repo_root, repo)
    backlog_lookup: Dict[str, int] = {}
    title_lookup: Dict[str, int] = {}

    for issue in existing_issues:
        title_lookup[issue["title"]] = int(issue["number"])
        match = BACKLOG_MARKER_RE.search(issue.get("body") or "")
        if match:
            backlog_id = match.group(1)
            if backlog_id in backlog_lookup:
                raise RuntimeError(
                    f"Duplicate backlog marker already present in repo for {backlog_id}."
                )
            backlog_lookup[backlog_id] = int(issue["number"])

    for issue in issues:
        backlog_id = issue["id"]
        body_path = manifest_path.parent / issue["body_path"]
        issue_body = body_path.read_text(encoding="utf-8")
        desired_issue_state = "closed" if issue.get("status") == "done" else "open"
        desired_labels = sorted(set(issue.get("labels", [])))
        desired_milestone = str(issue.get("milestone") or "").strip()
        milestone_number = None
        if desired_milestone:
            if desired_milestone not in existing_milestones:
                raise RuntimeError(f"{desired_milestone!r} not found")
            milestone_number = int(existing_milestones[desired_milestone]["number"])
        if backlog_id in backlog_lookup:
            number = backlog_lookup[backlog_id]
            payload: dict[str, object] = {
                "title": issue["title"],
                "body": issue_body,
                "labels": desired_labels,
                "milestone": milestone_number,
                "state": desired_issue_state,
            }
            if desired_issue_state == "closed":
                payload["state_reason"] = "completed"
            patch_issue_payload(
                repo_root,
                repo,
                number,
                payload,
                dry_run=dry_run,
            )
            if not dry_run:
                verify_issue_snapshot(
                    repo_root,
                    repo,
                    number,
                    expected_title=str(issue["title"]),
                    expected_body=issue_body,
                    expected_labels=desired_labels,
                    expected_milestone=desired_milestone,
                    expected_state=desired_issue_state,
                )
                print(f"sync existing backlog issue #{number}: {backlog_id}")
            continue

        if issue["title"] in title_lookup:
            raise RuntimeError(
                f"Title collision without matching backlog marker for {backlog_id}: {issue['title']}"
            )

        create_payload: dict[str, object] = {
            "title": issue["title"],
            "body": issue_body,
            "labels": desired_labels,
            "milestone": milestone_number,
        }
        number = create_issue_payload(
            repo_root,
            repo,
            create_payload,
            dry_run=dry_run,
        )
        if dry_run:
            if desired_issue_state == "closed":
                print(
                    "DRY RUN: PATCH",
                    f"/repos/{repo}/issues/<new>",
                    json.dumps(
                        {"state": "closed", "state_reason": "completed"},
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            continue

        assert number is not None
        if desired_issue_state == "closed":
            patch_issue_payload(
                repo_root,
                repo,
                number,
                {"state": "closed", "state_reason": "completed"},
                dry_run=False,
            )
        verify_issue_snapshot(
            repo_root,
            repo,
            number,
            expected_title=str(issue["title"]),
            expected_body=issue_body,
            expected_labels=desired_labels,
            expected_milestone=desired_milestone,
            expected_state=desired_issue_state,
        )
        print(f"create backlog issue #{number}: {backlog_id}")


def select_metadata(
    manifest: Dict[str, object],
    selected_issues: Sequence[Dict[str, object]],
) -> Dict[str, List[Dict[str, str]]]:
    if not selected_issues:
        return {"labels": [], "milestones": []}

    selected_milestones = {issue["milestone"] for issue in selected_issues}
    selected_labels = {
        label for issue in selected_issues for label in issue.get("labels", [])
    }
    labels = [
        label
        for label in manifest["labels"]
        if label["name"] in selected_labels
    ]
    milestones = [
        milestone
        for milestone in manifest["milestones"]
        if milestone["title"] in selected_milestones
    ]
    return {"labels": labels, "milestones": milestones}


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    manifest_path = (repo_root / args.manifest).resolve()
    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found: {manifest_path}")
    manifest = load_manifest(manifest_path)
    selected_issues = filter_issues(manifest["issues"], args.wave, args.status)

    run_preflight(repo_root, manifest_path, manifest, args.repo, selected_issues)

    print(
        f"Preflight OK: {len(manifest['issues'])} backlog items, "
        f"{len(selected_issues)} selected for issue seeding."
    )

    if args.mode == "preflight":
        return

    metadata_scope = selected_issues if (args.wave or args.status) else manifest["issues"]
    metadata = select_metadata(manifest, metadata_scope)
    milestone_states = desired_milestone_states(manifest["issues"])

    if args.mode in {"issues", "all"} and not selected_issues:
        print("No backlog issues matched the selected wave/status filters.")

    if args.mode in {"metadata", "all"}:
        seed_metadata(
            repo_root,
            args.repo,
            metadata["labels"] if args.wave or args.status else manifest["labels"],
            metadata["milestones"]
            if args.wave or args.status
            else manifest["milestones"],
            milestone_states,
            args.dry_run,
        )

    if args.mode in {"issues", "all"}:
        seed_issues(repo_root, manifest_path, args.repo, selected_issues, args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stdout or "")
        sys.stderr.write(exc.stderr or "")
        sys.exit(exc.returncode)
    except Exception as exc:  # pragma: no cover - CLI error path
        sys.stderr.write(f"error: {exc}\n")
        sys.exit(1)
