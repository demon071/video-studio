"""Progress reporting contract used by every service.

Services must not import the API layer, so they accept a ``Reporter``
callable. ``app.api.jobs`` provides the concrete implementation that persists
the job state and fans the event out to WebSocket subscribers.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.schemas.subtitle import SubtitleSegment


class Cancelled(RuntimeError):
    """Raised from inside a job when the user cancelled it."""


class Reporter(Protocol):
    """Callable used by services to report stage/fraction/subtitle updates."""

    def __call__(
        self,
        *,
        stage: str | None = None,
        fraction: float | None = None,
        message: str | None = None,
        segment: SubtitleSegment | None = None,
        data: dict[str, Any] | None = None,
        emit: bool = False,
    ) -> None:
        """Update the job.

        ``fraction`` is a 0..1 fraction for the *current* stage. Transient
        events (``emit=True``) are pushed over the socket without being merged
        into the job snapshot.
        """


NULL_REPORTER: Reporter


def _null_report(  # pragma: no cover - trivial
    *,
    stage: str | None = None,
    fraction: float | None = None,
    message: str | None = None,
    segment: SubtitleSegment | None = None,
    data: dict[str, Any] | None = None,
    emit: bool = False,
) -> None:
    return None


NULL_REPORTER = _null_report


__all__ = ["NULL_REPORTER", "Cancelled", "Reporter"]
