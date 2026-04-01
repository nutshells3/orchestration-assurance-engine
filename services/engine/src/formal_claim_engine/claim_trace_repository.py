"""SQLite-backed claim tracing repository with JSON mirror exports."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .claim_trace_types import TraceProjectRecord
from .models import ClaimGraph
from .store import ArtifactStore, canonical_artifact_id


def _safe_project_path(root: Path, project_id: str) -> Path:
    safe = project_id.replace("/", "_").replace("..", "_")
    return root / f"{safe}.json"


class ClaimTraceRepository:
    """Persist project metadata in SQLite while mirroring canonical JSON files."""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.projects_dir = self.root / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_store = ArtifactStore(self.root / "artifacts")
        self.db_path = self.root / "claim_trace_repository.sqlite3"
        self._initialize_database()
        self._hydrate_database_from_mirrors()
        self._project_cache: dict[str, TraceProjectRecord] = {}
        self._graph_cache: dict[str, dict[str, Any] | None] = {}

    def _connect(self):
        connection = sqlite3.connect(self.db_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return closing(connection)

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    claim_graph_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    project_json TEXT NOT NULL
                )
                """
            )

    def _table_has_rows(self) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1 FROM projects LIMIT 1").fetchone()
        return row is not None

    def _write_project_mirror(self, project: TraceProjectRecord) -> Path:
        path = _safe_project_path(self.projects_dir, project.id)
        path.write_text(
            project.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        return path

    def _upsert_project_record(self, project: TraceProjectRecord) -> None:
        payload = project.model_dump(mode="json", exclude_none=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    project_id,
                    name,
                    domain,
                    claim_graph_id,
                    created_at,
                    updated_at,
                    project_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    name = excluded.name,
                    domain = excluded.domain,
                    claim_graph_id = excluded.claim_graph_id,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    project_json = excluded.project_json
                """,
                (
                    project.id,
                    project.name,
                    project.domain.value,
                    project.claim_graph_id,
                    project.created_at.isoformat(),
                    payload.get("updated_at", project.created_at.isoformat()),
                    json.dumps(payload, default=str),
                ),
            )

    def _hydrate_database_from_mirrors(self) -> None:
        if self._table_has_rows():
            return
        for path in sorted(self.projects_dir.glob("*.json")):
            project = TraceProjectRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            self._upsert_project_record(project)

    def _load_project_record(self, project_id: str) -> TraceProjectRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT project_json FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if row is not None:
            project = TraceProjectRecord.model_validate(json.loads(row["project_json"]))
            mirror_path = _safe_project_path(self.projects_dir, project.id)
            if not mirror_path.exists():
                self._write_project_mirror(project)
            return project

        mirror_path = _safe_project_path(self.projects_dir, project_id)
        if not mirror_path.exists():
            return None
        project = TraceProjectRecord.model_validate_json(
            mirror_path.read_text(encoding="utf-8")
        )
        self._upsert_project_record(project)
        return project

    def save(
        self, project: TraceProjectRecord, graph_data: dict[str, Any] | None
    ) -> None:
        if graph_data and graph_data.get("claims"):
            validated = ClaimGraph.model_validate(graph_data)
            project.claim_graph_id = canonical_artifact_id(validated.graph_id)
            graph_data = validated.model_dump(mode="json", exclude_none=True)
            self.artifact_store.save_claim_graph(validated)
        self._upsert_project_record(project)
        self._write_project_mirror(project)
        self._project_cache[project.id] = project
        self._graph_cache[project.id] = graph_data

    def load(
        self, project_id: str
    ) -> tuple[TraceProjectRecord | None, dict[str, Any] | None]:
        if project_id in self._project_cache:
            return self._project_cache[project_id], self._graph_cache.get(project_id)

        project = self._load_project_record(project_id)
        if project is None:
            return None, None

        graph_data = None
        if project.claim_graph_id:
            graph = self.artifact_store.load_claim_graph(project.claim_graph_id)
            graph_data = graph.model_dump(mode="json", exclude_none=True)

        self._project_cache[project.id] = project
        self._graph_cache[project.id] = graph_data
        return project, graph_data

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT project_id, name, domain, claim_graph_id, project_json
                FROM projects
                ORDER BY project_id
                """
            ).fetchall()

        projects: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["project_json"])
            project = TraceProjectRecord.model_validate(payload)
            mirror_path = _safe_project_path(self.projects_dir, project.id)
            if not mirror_path.exists():
                self._write_project_mirror(project)
            graph_data = None
            if row["claim_graph_id"]:
                graph_data = self.artifact_store.load_claim_graph(
                    row["claim_graph_id"]
                ).model_dump(mode="json", exclude_none=True)
            projects.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "domain": project.domain.value,
                    "claims": len((graph_data or {}).get("claims", [])),
                }
            )
        return projects
