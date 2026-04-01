"""
Store layer: authoritative SQLite persistence with canonical JSON mirrors.

Persists ClaimGraph, AssuranceGraph, and AssuranceProfile payloads in SQLite
while preserving deterministic JSON exports for examples, diffs, and schema
validation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar, Type

import jsonschema
from pydantic import BaseModel

from .config import SCHEMA_DIR
from .models import ClaimGraph, AssuranceGraph, AssuranceProfile

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Schema cache
# ---------------------------------------------------------------------------

_SCHEMA_CACHE: dict[str, dict] = {}


def canonical_artifact_id(artifact_id: object) -> str:
    root_value = getattr(artifact_id, "root", None)
    if isinstance(root_value, str):
        return root_value
    text = str(artifact_id)
    if text.startswith("root='") and text.endswith("'"):
        return text[6:-1]
    if text.startswith('root="') and text.endswith('"'):
        return text[6:-1]
    return text


def _load_json_schema(name: str) -> dict:
    if name not in _SCHEMA_CACHE:
        path = SCHEMA_DIR / name
        _SCHEMA_CACHE[name] = json.loads(path.read_text())
    return _SCHEMA_CACHE[name]


CLAIM_GRAPH_SCHEMA = "claim-graph.schema.json"
ASSURANCE_GRAPH_SCHEMA = "assurance-graph.schema.json"
ASSURANCE_PROFILE_SCHEMA = "assurance-profile.schema.json"
SCHEMA_MAP = {
    "claim_graphs": CLAIM_GRAPH_SCHEMA,
    "assurance_graphs": ASSURANCE_GRAPH_SCHEMA,
    "assurance_profiles": ASSURANCE_PROFILE_SCHEMA,
}
OPAQUE_ARTIFACT_KINDS = {
    "source_mapping_bundles",
    "external_reference_registries",
    "evaluation_evidence_bundles",
}
ALL_ARTIFACT_KINDS = tuple(sorted({*SCHEMA_MAP.keys(), *OPAQUE_ARTIFACT_KINDS}))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_json(data: dict, schema_name: str) -> list[str]:
    """Return list of validation error messages (empty = OK)."""
    schema = _load_json_schema(schema_name)
    validator = jsonschema.Draft202012Validator(schema)
    return [e.message for e in validator.iter_errors(data)]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _json_text(data: Any, *, indent: int | None = None) -> str:
    return json.dumps(data, indent=indent, default=str, sort_keys=indent is None)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class ArtifactStore:
    """SQLite-backed artifact store with JSON export mirrors."""

    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        for kind in ALL_ARTIFACT_KINDS:
            (self.root / kind).mkdir(exist_ok=True)
            (self.root / "revisions" / kind).mkdir(parents=True, exist_ok=True)
        self.journals_dir = self.root / "journals"
        self.journals_dir.mkdir(exist_ok=True)
        self.review_events_path = self.journals_dir / "review_events.jsonl"
        self.migration_events_path = self.journals_dir / "migration_events.jsonl"
        self.migration_reports_dir = self.root / "migration_reports"
        self.migration_reports_dir.mkdir(exist_ok=True)
        self.db_path = self.root / "artifact_store.sqlite3"
        self._initialize_database()
        self._hydrate_database_from_mirrors()

    # --- generic helpers ---

    def _connect(self):
        connection = sqlite3.connect(self.db_path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return closing(connection)

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifact_latest (
                    kind TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    schema_version TEXT,
                    project_id TEXT,
                    claim_id TEXT,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    current_revision_id TEXT,
                    PRIMARY KEY (kind, artifact_id)
                );
                CREATE TABLE IF NOT EXISTS artifact_revisions (
                    revision_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    schema_version TEXT,
                    project_id TEXT,
                    claim_id TEXT,
                    metadata_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_events (
                    event_id TEXT PRIMARY KEY,
                    target_claim_id TEXT NOT NULL,
                    artifact_kind TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    actor_role TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migration_events (
                    event_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    legacy_id TEXT NOT NULL,
                    upgraded_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migration_reports (
                    report_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    report_json TEXT NOT NULL
                );
                """
            )
        self._ensure_review_event_schema()

    def _ensure_review_event_schema(self) -> None:
        with self._connect() as connection:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(review_events)").fetchall()
            }
            if "actor_role" not in columns:
                connection.execute(
                    "ALTER TABLE review_events ADD COLUMN actor_role TEXT NOT NULL DEFAULT ''"
                )

    def _table_has_rows(self, table_name: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT 1 FROM {table_name} LIMIT 1"
            ).fetchone()
        return row is not None

    def _path(self, kind: str, artifact_id: str) -> Path:
        return self.root / kind / f"{canonical_artifact_id(artifact_id)}.json"

    def _write_json(self, path: Path, payload: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json_text(payload, indent=2), encoding="utf-8")
        return path

    def _jsonable(self, model: BaseModel) -> dict[str, Any]:
        return model.model_dump(mode="json", exclude_none=True)

    def _coerce_payload(self, model: BaseModel | dict[str, Any]) -> dict[str, Any]:
        if isinstance(model, BaseModel):
            return self._jsonable(model)
        return json.loads(_json_text(model))

    def _revision_path(self, kind: str, artifact_id: str, revision_id: str) -> Path:
        return (
            self.root
            / "revisions"
            / kind
            / canonical_artifact_id(artifact_id)
            / f"{revision_id}.json"
        )

    def _artifact_metadata(
        self, payload: dict[str, Any]
    ) -> tuple[str | None, str | None, str | None]:
        return (
            payload.get("schema_version"),
            payload.get("project_id"),
            payload.get("claim_id"),
        )

    def _journal_event_id(self, prefix: str, target_id: str) -> str:
        stamp = now_utc().strftime("%Y%m%d%H%M%S%f")
        canonical_target = canonical_artifact_id(target_id)
        safe_target = (
            canonical_target.replace("\\", "_")
            .replace("/", "_")
            .replace(":", "_")
            .replace(" ", "_")
            .replace("..", "_")
        )
        digest = hashlib.sha256(
            f"{prefix}:{canonical_target}:{stamp}".encode("utf-8")
        ).hexdigest()[:8]
        return f"{prefix}.{safe_target}.{stamp}.{digest}"

    def _upsert_latest_record(
        self,
        kind: str,
        artifact_id: str,
        payload: dict[str, Any],
        *,
        updated_at: str,
        current_revision_id: str | None,
    ) -> None:
        schema_version, project_id, claim_id = self._artifact_metadata(payload)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifact_latest (
                    kind,
                    artifact_id,
                    schema_version,
                    project_id,
                    claim_id,
                    payload_json,
                    updated_at,
                    current_revision_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, artifact_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    project_id = excluded.project_id,
                    claim_id = excluded.claim_id,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at,
                    current_revision_id = excluded.current_revision_id
                """,
                (
                    kind,
                    canonical_artifact_id(artifact_id),
                    schema_version,
                    project_id,
                    claim_id,
                    _json_text(payload),
                    updated_at,
                    current_revision_id,
                ),
            )

    def _revision_payload_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "revision": {
                "revision_id": row["revision_id"],
                "artifact_kind": row["kind"],
                "artifact_id": row["artifact_id"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "reason": row["reason"],
                "sha256": row["sha256"],
                "schema_version": row["schema_version"],
                "project_id": row["project_id"],
                "claim_id": row["claim_id"],
                "metadata": json.loads(row["metadata_json"]),
            },
            "artifact": json.loads(row["payload_json"]),
        }

    def _upsert_revision_record(self, payload: dict[str, Any]) -> None:
        revision = payload["revision"]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifact_revisions (
                    revision_id,
                    kind,
                    artifact_id,
                    created_at,
                    actor,
                    reason,
                    sha256,
                    schema_version,
                    project_id,
                    claim_id,
                    metadata_json,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(revision_id) DO UPDATE SET
                    kind = excluded.kind,
                    artifact_id = excluded.artifact_id,
                    created_at = excluded.created_at,
                    actor = excluded.actor,
                    reason = excluded.reason,
                    sha256 = excluded.sha256,
                    schema_version = excluded.schema_version,
                    project_id = excluded.project_id,
                    claim_id = excluded.claim_id,
                    metadata_json = excluded.metadata_json,
                    payload_json = excluded.payload_json
                """,
                (
                    revision["revision_id"],
                    revision["artifact_kind"],
                    revision["artifact_id"],
                    revision["created_at"],
                    revision["actor"],
                    revision["reason"],
                    revision["sha256"],
                    revision["schema_version"],
                    revision["project_id"],
                    revision["claim_id"],
                    _json_text(revision.get("metadata") or {}),
                    _json_text(payload["artifact"]),
                ),
            )

    def _record_revision(
        self,
        kind: str,
        artifact_id: str,
        artifact_data: dict[str, Any],
        *,
        actor: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        serialized = json.dumps(artifact_data, sort_keys=True, separators=(",", ":"))
        revision_id = self._journal_event_id(
            f"rev.{kind[:-1]}",
            canonical_artifact_id(artifact_id),
        )
        payload = {
            "revision": {
                "revision_id": revision_id,
                "artifact_kind": kind,
                "artifact_id": canonical_artifact_id(artifact_id),
                "created_at": now_utc().isoformat(),
                "actor": actor,
                "reason": reason,
                "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
                "schema_version": artifact_data.get("schema_version"),
                "project_id": artifact_data.get("project_id"),
                "claim_id": artifact_data.get("claim_id"),
                "metadata": metadata or {},
            },
            "artifact": artifact_data,
        }
        self._write_json(self._revision_path(kind, artifact_id, revision_id), payload)
        self._upsert_revision_record(payload)
        return payload["revision"]

    def _hydrate_latest_from_mirrors(self) -> None:
        if self._table_has_rows("artifact_latest"):
            return
        for kind in ALL_ARTIFACT_KINDS:
            for path in sorted((self.root / kind).glob("*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                updated_at = (
                    payload.get("updated_at")
                    or payload.get("created_at")
                    or now_utc().isoformat()
                )
                self._upsert_latest_record(
                    kind,
                    path.stem,
                    payload,
                    updated_at=updated_at,
                    current_revision_id=None,
                )

    def _hydrate_revisions_from_mirrors(self) -> None:
        if self._table_has_rows("artifact_revisions"):
            return
        for kind in ALL_ARTIFACT_KINDS:
            for path in sorted((self.root / "revisions" / kind).glob("*/*.json")):
                payload = json.loads(path.read_text(encoding="utf-8"))
                if "revision" in payload and "artifact" in payload:
                    self._upsert_revision_record(payload)

    def _hydrate_review_events_from_mirrors(self) -> None:
        if self._table_has_rows("review_events") or not self.review_events_path.exists():
            return
        with self._connect() as connection:
            for line in self.review_events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO review_events (
                        event_id,
                        target_claim_id,
                        artifact_kind,
                        artifact_id,
                        event_type,
                        actor,
                        actor_role,
                        notes,
                        metadata_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        event["target_claim_id"],
                        event["artifact_kind"],
                        event["artifact_id"],
                        event["event_type"],
                        event["actor"],
                        str(
                            event.get("actor_role")
                            or (event.get("metadata") or {}).get("actor_role")
                            or ""
                        ),
                        event.get("notes", ""),
                        _json_text(event.get("metadata") or {}),
                        event["created_at"],
                    ),
                )

    def _hydrate_migration_events_from_mirrors(self) -> None:
        if self._table_has_rows("migration_events") or not self.migration_events_path.exists():
            return
        with self._connect() as connection:
            for line in self.migration_events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                connection.execute(
                    """
                    INSERT OR REPLACE INTO migration_events (
                        event_id,
                        batch_id,
                        kind,
                        source_path,
                        legacy_id,
                        upgraded_id,
                        action,
                        metadata_json,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        event["batch_id"],
                        event["kind"],
                        event["source_path"],
                        event["legacy_id"],
                        event["upgraded_id"],
                        event["action"],
                        _json_text(event.get("metadata") or {}),
                        event["created_at"],
                    ),
                )

    def _hydrate_migration_reports_from_mirrors(self) -> None:
        if self._table_has_rows("migration_reports"):
            return
        for path in sorted(self.migration_reports_dir.glob("*.json")):
            report = json.loads(path.read_text(encoding="utf-8"))
            report_id = report.get("migration_report_id") or path.stem
            created_at = (
                report.get("generated_at")
                or report.get("created_at")
                or now_utc().isoformat()
            )
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO migration_reports (
                        report_id,
                        created_at,
                        report_json
                    ) VALUES (?, ?, ?)
                    """,
                    (report_id, created_at, _json_text(report)),
                )

    def _hydrate_database_from_mirrors(self) -> None:
        self._hydrate_latest_from_mirrors()
        self._hydrate_revisions_from_mirrors()
        self._hydrate_review_events_from_mirrors()
        self._hydrate_migration_events_from_mirrors()
        self._hydrate_migration_reports_from_mirrors()

    def _load_payload(self, kind: str, artifact_id: str) -> dict[str, Any] | None:
        artifact_id = canonical_artifact_id(artifact_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM artifact_latest
                WHERE kind = ? AND artifact_id = ?
                """,
                (kind, artifact_id),
            ).fetchone()
        if row is not None:
            payload = json.loads(row["payload_json"])
            mirror_path = self._path(kind, artifact_id)
            if not mirror_path.exists():
                self._write_json(mirror_path, payload)
            return payload

        mirror_path = self._path(kind, artifact_id)
        if not mirror_path.exists():
            return None

        payload = json.loads(mirror_path.read_text(encoding="utf-8"))
        updated_at = (
            payload.get("updated_at")
            or payload.get("created_at")
            or now_utc().isoformat()
        )
        self._upsert_latest_record(
            kind,
            artifact_id,
            payload,
            updated_at=updated_at,
            current_revision_id=None,
        )
        return payload

    def load_payload(self, kind: str, artifact_id: str) -> dict[str, Any]:
        payload = self._load_payload(kind, artifact_id)
        if payload is None:
            raise FileNotFoundError(
                f"Artifact not found for {kind}:{canonical_artifact_id(artifact_id)}"
            )
        return payload

    def _save(
        self,
        kind: str,
        artifact_id: str,
        model: BaseModel | dict[str, Any],
        *,
        actor: str = "system",
        reason: str = "save",
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        payload = self._coerce_payload(model)
        revision = self._record_revision(
            kind,
            artifact_id,
            payload,
            actor=actor,
            reason=reason,
            metadata=metadata,
        )
        self._upsert_latest_record(
            kind,
            artifact_id,
            payload,
            updated_at=revision["created_at"],
            current_revision_id=revision["revision_id"],
        )
        return self._write_json(self._path(kind, artifact_id), payload)

    def _load(self, kind: str, artifact_id: str, cls: Type[T]) -> T:
        return cls.model_validate(self.load_payload(kind, artifact_id))

    def _list(self, kind: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact_id
                FROM artifact_latest
                WHERE kind = ?
                ORDER BY artifact_id
                """,
                (kind,),
            ).fetchall()
        if rows:
            return [str(row["artifact_id"]) for row in rows]
        return [path.stem for path in sorted((self.root / kind).glob("*.json"))]

    def get_latest_artifact(
        self,
        kind: str,
        artifact_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT artifact_id, payload_json, updated_at, current_revision_id
                FROM artifact_latest
                WHERE kind = ? AND artifact_id = ?
                """,
                (kind, canonical_artifact_id(artifact_id)),
            ).fetchone()
        if row is None:
            payload = self._load_payload(kind, artifact_id)
            if payload is None:
                return None
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT artifact_id, payload_json, updated_at, current_revision_id
                    FROM artifact_latest
                    WHERE kind = ? AND artifact_id = ?
                    """,
                    (kind, canonical_artifact_id(artifact_id)),
                ).fetchone()
            if row is None:
                return None
        return {
            "artifact_id": str(row["artifact_id"]),
            "updated_at": str(row["updated_at"]),
            "current_revision_id": (
                str(row["current_revision_id"]) if row["current_revision_id"] else None
            ),
            "payload": json.loads(row["payload_json"]),
        }

    def list_latest_artifacts(
        self,
        kind: str,
        *,
        project_id: str | None = None,
        claim_id: str | None = None,
    ) -> list[dict[str, Any]]:
        claim_filter = canonical_artifact_id(claim_id) if claim_id else None
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact_id, payload_json, updated_at, current_revision_id
                FROM artifact_latest
                WHERE kind = ?
                  AND (? IS NULL OR project_id = ?)
                  AND (? IS NULL OR claim_id = ?)
                ORDER BY artifact_id
                """,
                (kind, project_id, project_id, claim_filter, claim_filter),
            ).fetchall()
        return [
            {
                "artifact_id": str(row["artifact_id"]),
                "updated_at": str(row["updated_at"]),
                "current_revision_id": (
                    str(row["current_revision_id"])
                    if row["current_revision_id"]
                    else None
                ),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def save_json_artifact(
        self,
        kind: str,
        artifact_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "system",
        reason: str = "save",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_data = self._coerce_payload(payload)
        revision = self._record_revision(
            kind,
            artifact_id,
            artifact_data,
            actor=actor,
            reason=reason,
            metadata=metadata,
        )
        self._upsert_latest_record(
            kind,
            artifact_id,
            artifact_data,
            updated_at=revision["created_at"],
            current_revision_id=revision["revision_id"],
        )
        path = self._write_json(self._path(kind, artifact_id), artifact_data)
        return {
            "artifact_id": canonical_artifact_id(artifact_id),
            "revision_id": str(revision["revision_id"]),
            "path": str(path),
        }

    def list_revisions(self, kind: str, artifact_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM artifact_revisions
                WHERE kind = ? AND artifact_id = ?
                ORDER BY created_at, revision_id
                """,
                (kind, canonical_artifact_id(artifact_id)),
            ).fetchall()
        return [self._revision_payload_from_row(row)["revision"] for row in rows]

    def load_revision(
        self, kind: str, artifact_id: str, revision_id: str
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM artifact_revisions
                WHERE kind = ? AND artifact_id = ? AND revision_id = ?
                """,
                (kind, canonical_artifact_id(artifact_id), revision_id),
            ).fetchone()
        if row is None:
            path = self._revision_path(kind, artifact_id, revision_id)
            return json.loads(path.read_text(encoding="utf-8"))
        payload = self._revision_payload_from_row(row)
        revision_path = self._revision_path(kind, artifact_id, revision_id)
        if not revision_path.exists():
            self._write_json(revision_path, payload)
        return payload

    def append_review_event(
        self,
        *,
        target_claim_id: str,
        artifact_kind: str,
        artifact_id: str,
        event_type: str,
        actor: str,
        actor_role: str | None = None,
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": self._journal_event_id("review", target_claim_id),
            "target_claim_id": canonical_artifact_id(target_claim_id),
            "artifact_kind": artifact_kind,
            "artifact_id": canonical_artifact_id(artifact_id),
            "event_type": event_type,
            "actor": actor,
            "actor_role": str(actor_role or ""),
            "notes": notes,
            "metadata": metadata or {},
            "created_at": now_utc().isoformat(),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO review_events (
                    event_id,
                    target_claim_id,
                    artifact_kind,
                    artifact_id,
                    event_type,
                    actor,
                    actor_role,
                    notes,
                    metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["target_claim_id"],
                    event["artifact_kind"],
                    event["artifact_id"],
                    event["event_type"],
                    event["actor"],
                    event["actor_role"],
                    event["notes"],
                    _json_text(event["metadata"]),
                    event["created_at"],
                ),
            )
        with self.review_events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str) + "\n")
        return event

    def query_review_events(self, target_claim_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM review_events
                WHERE target_claim_id = ?
                ORDER BY created_at, event_id
                """,
                (canonical_artifact_id(target_claim_id),),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "target_claim_id": row["target_claim_id"],
                "artifact_kind": row["artifact_kind"],
                "artifact_id": row["artifact_id"],
                "event_type": row["event_type"],
                "actor": row["actor"],
                "actor_role": row["actor_role"] or None,
                "notes": row["notes"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def load_latest_payload_for_claim(
        self,
        kind: str,
        claim_id: str,
    ) -> dict[str, Any] | None:
        canonical_claim_id = canonical_artifact_id(claim_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM artifact_latest
                WHERE kind = ? AND claim_id = ?
                ORDER BY updated_at DESC, artifact_id
                LIMIT 1
                """,
                (kind, canonical_claim_id),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    def record_migration_event(
        self,
        *,
        batch_id: str,
        kind: str,
        source_path: str,
        legacy_id: str,
        upgraded_id: str,
        action: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": self._journal_event_id("migration", upgraded_id),
            "batch_id": batch_id,
            "kind": kind,
            "source_path": source_path,
            "legacy_id": canonical_artifact_id(legacy_id),
            "upgraded_id": canonical_artifact_id(upgraded_id),
            "action": action,
            "metadata": metadata or {},
            "created_at": now_utc().isoformat(),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO migration_events (
                    event_id,
                    batch_id,
                    kind,
                    source_path,
                    legacy_id,
                    upgraded_id,
                    action,
                    metadata_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["batch_id"],
                    event["kind"],
                    event["source_path"],
                    event["legacy_id"],
                    event["upgraded_id"],
                    event["action"],
                    _json_text(event["metadata"]),
                    event["created_at"],
                ),
            )
        with self.migration_events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str) + "\n")
        return event

    def list_migration_events(self, *, batch_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM migration_events"
        params: tuple[object, ...] = ()
        if batch_id:
            query += " WHERE batch_id = ?"
            params = (batch_id,)
        query += " ORDER BY created_at, event_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "batch_id": row["batch_id"],
                "kind": row["kind"],
                "source_path": row["source_path"],
                "legacy_id": row["legacy_id"],
                "upgraded_id": row["upgraded_id"],
                "action": row["action"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def write_migration_report(
        self,
        report: dict[str, Any],
        *,
        report_id: str | None = None,
    ) -> Path:
        report_id = report_id or self._journal_event_id(
            "migration-report",
            report.get("target_dir", "store"),
        )
        path = self.migration_reports_dir / f"{report_id}.json"
        report["migration_report_id"] = report_id
        report["migration_report_path"] = str(path)
        created_at = report.get("generated_at") or now_utc().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO migration_reports (
                    report_id,
                    created_at,
                    report_json
                ) VALUES (?, ?, ?)
                """,
                (report_id, created_at, _json_text(report)),
            )
        return self._write_json(path, report)

    # --- Claim Graph ---

    def save_claim_graph(
        self,
        cg: ClaimGraph,
        *,
        actor: str = "system",
        reason: str = "save",
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        return self._save(
            "claim_graphs",
            cg.graph_id,
            cg,
            actor=actor,
            reason=reason,
            metadata=metadata,
        )

    def load_claim_graph(self, graph_id: str) -> ClaimGraph:
        return self._load("claim_graphs", graph_id, ClaimGraph)

    def list_claim_graphs(self) -> list[str]:
        return self._list("claim_graphs")

    # --- Assurance Graph ---

    def save_assurance_graph(
        self,
        ag: AssuranceGraph,
        *,
        actor: str = "system",
        reason: str = "save",
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        return self._save(
            "assurance_graphs",
            ag.graph_id,
            ag,
            actor=actor,
            reason=reason,
            metadata=metadata,
        )

    def load_assurance_graph(self, graph_id: str) -> AssuranceGraph:
        return self._load("assurance_graphs", graph_id, AssuranceGraph)

    def list_assurance_graphs(self) -> list[str]:
        return self._list("assurance_graphs")

    # --- Assurance Profile ---

    def save_assurance_profile(
        self,
        ap: AssuranceProfile,
        *,
        actor: str = "system",
        reason: str = "save",
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        return self._save(
            "assurance_profiles",
            ap.profile_id,
            ap,
            actor=actor,
            reason=reason,
            metadata=metadata,
        )

    def load_assurance_profile(self, profile_id: str) -> AssuranceProfile:
        return self._load("assurance_profiles", profile_id, AssuranceProfile)

    def load_assurance_profile_for_claim(self, claim_id: str) -> AssuranceProfile:
        payload = self.load_latest_payload_for_claim("assurance_profiles", claim_id)
        if payload is None:
            raise FileNotFoundError(
                f"AssuranceProfile not found for claim:{canonical_artifact_id(claim_id)}"
            )
        return AssuranceProfile.model_validate(payload)

    def list_assurance_profiles(self) -> list[str]:
        return self._list("assurance_profiles")

    # --- validate raw JSON against schema ---

    def validate_file(self, kind: str, artifact_id: str) -> list[str]:
        return validate_json(self.load_payload(kind, artifact_id), SCHEMA_MAP[kind])
