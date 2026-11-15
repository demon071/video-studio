"""Small helpers shared by the routers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.api.jobs import JobError, jobs
from app.config import get_settings


def run_job(kind: str, work: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    """Execute ``work`` synchronously, mapping failures onto HTTP status codes."""
    try:
        _, result = jobs.execute(kind, work)
    except JobError as exc:
        raise HTTPException(status_code=409 if exc.cancelled else 500, detail=str(exc)) from exc
    return result


def work_dir(prefix: str) -> Path:
    """Fresh temp folder for one pipeline run."""
    folder = get_settings().temp_path / f"{prefix}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def with_suffix(path: str | Path, suffix: str) -> Path:
    target = Path(path)
    return target if target.suffix else target.with_suffix(suffix)


__all__ = ["run_job", "with_suffix", "work_dir"]
