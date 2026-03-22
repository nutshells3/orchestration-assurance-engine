"""Integration smoke for SQLite-backed claim-trace repository mirrors."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def resolve_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "services" / "engine" / "src").exists():
            return parent
    raise RuntimeError("Could not locate monorepo root from integration test.")


REPO_ROOT = resolve_repo_root()
ENGINE_SRC = REPO_ROOT / "services" / "engine" / "src"

if str(ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(ENGINE_SRC))

from formal_claim_engine.claim_trace_repository import ClaimTraceRepository  # noqa: E402
from formal_claim_engine.claim_trace_types import Domain, TraceProjectRecord  # noqa: E402


def main() -> None:
    graph_data = json.loads(
        (REPO_ROOT / "examples" / "theorem-audit" / "claim-graph.json").read_text(
            encoding="utf-8"
        )
    )

    with tempfile.TemporaryDirectory() as tmp:
        repository = ClaimTraceRepository(tmp)
        project = TraceProjectRecord(
            name="sqlite-mirror-smoke",
            domain=Domain.formal_proof,
            description="Repository sqlite smoke.",
        )

        graph_data["graph_id"] = f"tracer.{project.id}"
        graph_data["project_id"] = project.id
        repository.save(project, graph_data)

        project_mirror = repository.projects_dir / f"{project.id}.json"
        graph_mirror = repository.artifact_store._path("claim_graphs", graph_data["graph_id"])
        assert repository.db_path.exists()
        assert repository.artifact_store.db_path.exists()
        assert project_mirror.exists()
        assert graph_mirror.exists()

        project_mirror.unlink()
        graph_mirror.unlink()

        reloaded = ClaimTraceRepository(tmp)
        loaded_project, loaded_graph = reloaded.load(project.id)
        assert loaded_project is not None
        assert loaded_project.id == project.id
        assert loaded_project.claim_graph_id == graph_data["graph_id"]
        assert loaded_graph is not None
        assert loaded_graph["project_id"] == project.id
        assert project_mirror.exists()
        assert graph_mirror.exists()

        listed = reloaded.list_projects()
        assert listed == [
            {
                "id": project.id,
                "name": "sqlite-mirror-smoke",
                "domain": "formal_proof",
                "claims": len(graph_data["claims"]),
            }
        ]


if __name__ == "__main__":
    main()
