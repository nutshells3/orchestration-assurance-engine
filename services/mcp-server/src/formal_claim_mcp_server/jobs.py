"""In-memory job model for long-running MCP operations."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from formal_claim_engine.store import now_utc

from .models import ArtifactRef, JobHandle, JobState, McpErrorPayload


class JobNotFoundError(KeyError):
    """Raised when a job handle cannot be found."""


class JobCapacityError(RuntimeError):
    """Raised when the queued job budget has been exhausted."""


class McpJobStore:
    def __init__(
        self,
        *,
        max_concurrent_jobs: int | None = None,
        max_queued_jobs: int | None = None,
    ) -> None:
        self.max_concurrent_jobs = max(
            1,
            int(
                max_concurrent_jobs
                or os.environ.get("FORMAL_CLAIM_MCP_MAX_CONCURRENT_JOBS", "2")
            ),
        )
        self.max_queued_jobs = max(
            1,
            int(
                max_queued_jobs
                or os.environ.get("FORMAL_CLAIM_MCP_MAX_QUEUED_JOBS", "8")
            ),
        )
        self._semaphore = asyncio.Semaphore(self.max_concurrent_jobs)
        self._jobs: dict[str, JobHandle] = {}

    def reset(self) -> None:
        self._jobs.clear()
        self._semaphore = asyncio.Semaphore(self.max_concurrent_jobs)

    def get(self, job_id: str) -> JobHandle:
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise JobNotFoundError(job_id) from exc

    def start(
        self,
        *,
        operation: str,
        request_id: str,
        meta: dict[str, Any],
        coro_factory: Callable[[], Awaitable[dict[str, Any]]],
        error_mapper: Callable[[Exception], McpErrorPayload],
        artifact_ref_extractor: Callable[[dict[str, Any]], list[ArtifactRef]],
    ) -> JobHandle:
        queued = sum(
            1
            for job in self._jobs.values()
            if job.status in {JobState.queued, JobState.running}
        )
        if queued >= self.max_queued_jobs:
            raise JobCapacityError(
                f"Queued job limit reached ({self.max_queued_jobs})."
            )

        job_id = f"job.{uuid.uuid4().hex[:12]}"
        handle = JobHandle(
            job_id=job_id,
            operation=operation,
            status=JobState.queued,
            request_id=request_id,
            queued_at=now_utc().isoformat(),
            meta=dict(meta),
        )
        self._jobs[job_id] = handle
        asyncio.create_task(
            self._run_job(
                job_id=job_id,
                coro_factory=coro_factory,
                error_mapper=error_mapper,
                artifact_ref_extractor=artifact_ref_extractor,
            )
        )
        return handle

    async def _run_job(
        self,
        *,
        job_id: str,
        coro_factory: Callable[[], Awaitable[dict[str, Any]]],
        error_mapper: Callable[[Exception], McpErrorPayload],
        artifact_ref_extractor: Callable[[dict[str, Any]], list[ArtifactRef]],
    ) -> None:
        async with self._semaphore:
            job = self._jobs[job_id]
            self._jobs[job_id] = job.model_copy(
                update={
                    "status": JobState.running,
                    "started_at": now_utc().isoformat(),
                }
            )
            try:
                result = await coro_factory()
            except Exception as exc:  # pragma: no cover - exercised via callers
                current = self._jobs[job_id]
                self._jobs[job_id] = current.model_copy(
                    update={
                        "status": JobState.failed,
                        "completed_at": now_utc().isoformat(),
                        "error": error_mapper(exc),
                    }
                )
                return

            current = self._jobs[job_id]
            self._jobs[job_id] = current.model_copy(
                update={
                    "status": JobState.completed,
                    "completed_at": now_utc().isoformat(),
                    "result": result,
                    "artifact_refs": artifact_ref_extractor(result),
                }
            )
