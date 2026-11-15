"""Job / progress contracts for the REST + WebSocket control plane.

Every long-running operation (model download, transcription, translation,
dubbing, render) is tracked as a :class:`Job`. The HTTP response carries the
result, while the same progress is pushed to every connected WebSocket client
as a :class:`ProgressEvent`, so the editor can show a live percentage and
stream subtitle lines in as they are recognised.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.subtitle import SubtitleSegment


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class JobInfo(BaseModel):
    """Pollable snapshot of one job (``GET /api/jobs``)."""

    id: str
    kind: str
    state: JobState = JobState.QUEUED
    stage: str = "queued"
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    message: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at or time.time()
        return round(end - (self.started_at or self.created_at or end), 2)


class ProgressEvent(BaseModel):
    """One WebSocket frame.

    ``type`` tells the UI how to treat the payload:

    * ``progress``  stage/fraction update (drive the progress bar)
    * ``subtitle``  a freshly recognised/translated line, streamed live
    * ``log``       human readable status line for the activity panel
    * ``result``    terminal success payload
    * ``error``     terminal failure payload
    * ``hello``     connection handshake with hardware information
    """

    type: Literal["progress", "subtitle", "log", "result", "error", "hello", "ping"] = "progress"
    job_id: str | None = None
    kind: str | None = None
    stage: str | None = None
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str | None = None
    segment: SubtitleSegment | None = None
    data: dict[str, Any] | None = None
    ts: float = Field(default_factory=time.time)


__all__ = ["JobInfo", "JobState", "ProgressEvent"]
