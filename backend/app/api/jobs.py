"""Job registry + the thread-safe ``Reporter`` implementation.

Two execution modes are supported because the UI needs both:

* ``execute`` runs inline and returns the result (the HTTP request blocks, the
  WebSocket shows live progress). Used by transcribe / translate / dubbing /
  render, where the caller must receive the produced data.
* ``submit`` runs on a worker pool and returns immediately with a job id.
  Used by model downloads, which can take many minutes.

``max_concurrent_jobs`` (default 1) is enforced with a semaphore: two GPU
stages must never overlap on a 4 GB card.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.api.events import EventBus
from app.api.events import bus as default_bus
from app.config import get_settings
from app.core.progress import Cancelled
from app.schemas.job import JobInfo, JobState, ProgressEvent
from app.schemas.subtitle import SubtitleSegment

log = logging.getLogger(__name__)

JobFn = Callable[..., dict[str, Any]]
PUBLISH_INTERVAL_S = 0.1
MAX_HISTORY = 100


class JobError(RuntimeError):
    """Terminal job failure, carrying the job id for the UI."""

    def __init__(self, message: str, job_id: str | None = None, cancelled: bool = False) -> None:
        super().__init__(message)
        self.job_id = job_id
        self.cancelled = cancelled


@dataclass
class _Job:
    info: JobInfo
    cancel: threading.Event = field(default_factory=threading.Event)
    last_publish: float = 0.0


class JobManager:
    def __init__(self, event_bus: EventBus | None = None, max_workers: int | None = None) -> None:
        self.bus = event_bus or default_bus
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._order: list[str] = []
        workers = max_workers or max(1, get_settings().max_concurrent_jobs)
        self._semaphore = threading.Semaphore(workers)
        self._pool: ThreadPoolExecutor | None = None
        self._workers = workers

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="sva-job")

    def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # -- registry ----------------------------------------------------------
    def get(self, job_id: str) -> JobInfo | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.info.model_copy() if job else None

    def list(self, limit: int = 20) -> list[JobInfo]:
        with self._lock:
            ids = self._order[-limit:][::-1]
            return [self._jobs[job_id].info.model_copy() for job_id in ids if job_id in self._jobs]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.info.state in {JobState.DONE, JobState.ERROR, JobState.CANCELLED}:
                return False
            job.cancel.set()
        log.info("cancel requested for job %s", job_id)
        return True

    # -- reporter ----------------------------------------------------------
    def reporter(self, job_id: str) -> Callable[..., None]:
        def report(
            *,
            stage: str | None = None,
            fraction: float | None = None,
            message: str | None = None,
            segment: SubtitleSegment | None = None,
            data: dict[str, Any] | None = None,
            emit: bool = False,
        ) -> None:
            with self._lock:
                job = self._jobs.get(job_id)
            if job is None:
                return
            if job.cancel.is_set():
                raise Cancelled("Job cancelled by user")

            info = job.info
            changed = False
            if stage and stage != info.stage:
                info.stage = stage
                # A new stage always restarts the bar so it never jumps back.
                info.progress = 0.0
                changed = True
            if fraction is not None:
                value = min(max(float(fraction), 0.0), 1.0)
                if abs(value - info.progress) >= 0.005:
                    info.progress = value
                    changed = True
            if message is not None:
                info.message = message
                changed = True

            if segment is not None:
                self.bus.publish(
                    ProgressEvent(
                        type="subtitle",
                        job_id=job_id,
                        kind=info.kind,
                        stage=info.stage,
                        progress=info.progress,
                        message=message,
                        segment=segment,
                    )
                )

            if not changed and not emit and data is None:
                return

            now = time.monotonic()
            if not changed and now - job.last_publish < PUBLISH_INTERVAL_S:
                return
            job.last_publish = now
            self.bus.publish(
                ProgressEvent(
                    type="progress",
                    job_id=job_id,
                    kind=info.kind,
                    stage=info.stage,
                    progress=info.progress,
                    message=info.message,
                    data=data,
                )
            )

        return report

    # -- execution ---------------------------------------------------------
    def _create(self, kind: str) -> _Job:
        job = _Job(
            info=JobInfo(
                id=uuid.uuid4().hex[:12],
                kind=kind,
                state=JobState.QUEUED,
                stage="queued",
                created_at=time.time(),
            )
        )
        with self._lock:
            self._jobs[job.info.id] = job
            self._order.append(job.info.id)
            if len(self._order) > MAX_HISTORY:
                for stale_id in self._order[:-MAX_HISTORY]:
                    self._jobs.pop(stale_id, None)
                self._order = self._order[-MAX_HISTORY:]
        return job

    def _announce(self, job: _Job, state: JobState, message: str | None = None, data: dict | None = None) -> None:
        job.info.state = state
        job.info.finished_at = time.time()
        if message:
            job.info.message = message
        self.bus.publish(
            ProgressEvent(
                type="result" if state is JobState.DONE else "error" if state is JobState.ERROR else "log",
                job_id=job.info.id,
                kind=job.info.kind,
                stage=job.info.stage,
                progress=job.info.progress,
                message=message,
                data=data,
            )
        )

    def _body(self, job: _Job, fn: JobFn) -> dict[str, Any]:
        info = job.info
        info.state = JobState.RUNNING
        info.started_at = time.time()
        log.info("job %s (%s) started", info.id, info.kind)
        self.bus.publish(
            ProgressEvent(
                type="progress",
                job_id=info.id,
                kind=info.kind,
                stage=info.stage,
                progress=0.0,
                message="started",
            )
        )
        try:
            with self._semaphore:
                if job.cancel.is_set():
                    raise Cancelled("Job cancelled before it started")
                result = fn(self.reporter(info.id)) or {}
        except Cancelled as exc:
            info.state = JobState.CANCELLED
            info.finished_at = time.time()
            self._announce(job, JobState.CANCELLED, str(exc))
            raise JobError(str(exc), info.id, cancelled=True) from exc
        except Exception as exc:  # noqa: BLE001 - reported to the UI verbatim
            info.state = JobState.ERROR
            info.error = f"{type(exc).__name__}: {exc}"
            info.finished_at = time.time()
            log.exception("job %s (%s) failed", info.id, info.kind)
            self._announce(job, JobState.ERROR, info.error)
            raise JobError(info.error, info.id) from exc

        info.state = JobState.DONE
        info.progress = 1.0
        info.result = result if isinstance(result, dict) else {"result": result}
        info.finished_at = time.time()
        log.info("job %s (%s) done in %.2fs", info.id, info.kind, info.finished_at - (info.started_at or 0))
        self._announce(job, JobState.DONE, "completed", info.result)
        return info.result

    def execute(self, kind: str, fn: JobFn) -> tuple[JobInfo, dict[str, Any]]:
        """Run ``fn`` on the calling thread and return ``(job, result)``."""
        job = self._create(kind)
        result = self._body(job, fn)
        return job.info.model_copy(), result

    def submit(self, kind: str, fn: JobFn) -> JobInfo:
        """Queue ``fn`` on the worker pool and return the job immediately."""
        if self._pool is None:
            self.start()
        job = self._create(kind)
        assert self._pool is not None

        def runner() -> None:
            with contextlib.suppress(JobError):
                self._body(job, fn)

        self._pool.submit(runner)
        return job.info.model_copy()


jobs = JobManager()


__all__ = ["JobError", "JobManager", "jobs"]
