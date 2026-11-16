"""Fit Voice time-warp planning and coordinate mapping (ported from CapCap).

When a synthesized TTS line is longer than its subtitle slot even after
borrowing following silence, Fit Voice stretches ("slow") or holds ("freeze")
the underlying video segment so speech is never clipped or sped up unnaturally,
while keeping primary subtitles, secondary subtitles, and visual layers in sync.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

PLAN_VERSION = 2


@dataclass(frozen=True)
class WarpSpan:
    source_start: float
    source_end: float
    duration: float  # added seconds
    type: str        # "slow" | "freeze"
    indices: tuple[int, ...]
    timeline_start: float
    timeline_end: float
    speed: float


def compile_plan(warps: Sequence[dict[str, Any]] | None) -> tuple[WarpSpan, ...]:
    key: list[tuple[str, float, float, float]] = []
    for w in warps or ():
        kind = str(w.get("type", "freeze"))
        start = float(w.get("media_start", w.get("time", 0.0))) if kind == "slow" else float(w.get("time", 0.0))
        end = float(w.get("media_end", start)) if kind == "slow" else start
        key.append((kind, start, end, float(w.get("duration", 0.0))))
    return _compile(tuple(key))


@lru_cache(maxsize=128)
def _compile(key: tuple[tuple[str, float, float, float], ...]) -> tuple[WarpSpan, ...]:
    ordered: list[tuple[float, bool, float, int, str, float]] = []
    for i, (kind, start, end, extra) in enumerate(key):
        if not all(math.isfinite(v) for v in (start, end, extra)):
            raise ValueError("Non-finite Fit Voice time")
        if extra <= 0:
            continue
        if kind not in ("slow", "freeze") or start < 0 or end < start:
            raise ValueError("Invalid Fit Voice source range")
        ordered.append((end, kind == "freeze", start, i, kind, extra))
    ordered.sort()

    items: list[list[Any]] = []
    source = 0.0
    for end, _, start, i, kind, extra in ordered:
        if kind == "slow":
            start = max(start, source)
            if end <= start:
                previous = next((x for x in reversed(items) if x[3] == "slow" and x[1] == end), None)
                if previous is not None:
                    previous[2] += extra
                    previous[4].append(i)
                    continue
                kind, start = "freeze", end
        items.append([start, end, extra, kind, [i]])
        source = end

    result: list[WarpSpan] = []
    shift = 0.0
    for start, end, extra, kind, indices in items:
        target_duration = end - start + extra
        speed = (end - start) / target_duration if kind == "slow" else 1.0
        result.append(
            WarpSpan(
                source_start=start,
                source_end=end,
                duration=extra,
                type=kind,
                indices=tuple(indices),
                timeline_start=start + shift,
                timeline_end=end + shift + extra,
                speed=speed,
            )
        )
        shift += extra
    return tuple(result)


def normalized_warps(warps: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not warps:
        return result
    for span in compile_plan(warps):
        item = dict(warps[span.indices[0]])
        item.update(
            type=span.type,
            time=span.source_end,
            media_start=span.source_start,
            media_end=span.source_end,
            duration=span.duration,
            speed=span.speed,
        )
        result.append(item)
    return result


def video_parts(
    duration: float,
    warps: Sequence[dict[str, Any]] | None,
) -> list[tuple[float, float, float, float, bool]]:
    """Return ``(source_start, target_start, target_duration, speed, frozen)`` slices."""
    duration = float(duration)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("Invalid source video duration")
    source, target = 0.0, 0.0
    result: list[tuple[float, float, float, float, bool]] = []
    for span in compile_plan(warps):
        start, end = min(span.source_start, duration), min(span.source_end, duration)
        if start > source:
            result.append((source, target, start - source, 1.0, False))
            target += start - source
        length = end - start + span.duration
        freeze = span.type == "freeze" or end == start
        speed = (end - start) / length if not freeze else 1.0
        result.append((start, target, length, speed, freeze))
        source, target = end, target + length
    if source < duration:
        result.append((source, target, duration - source, 1.0, False))
    return result


def timeline_to_media(t: float, warps: Sequence[dict[str, Any]] | None) -> float:
    shift = 0.0
    for s in compile_plan(warps):
        if t < s.timeline_start:
            break
        if t < s.timeline_end:
            return s.source_start + (t - s.timeline_start) * s.speed if s.type == "slow" else s.source_start
        shift += s.duration
    return max(0.0, t - shift)


def media_to_timeline(m: float, warps: Sequence[dict[str, Any]] | None) -> float:
    shift = 0.0
    for s in compile_plan(warps):
        if m < s.source_start:
            break
        if s.type == "slow" and m < s.source_end:
            return s.timeline_start + (m - s.source_start) / s.speed
        shift += s.duration
    return m + shift


__all__ = [
    "PLAN_VERSION",
    "WarpSpan",
    "compile_plan",
    "media_to_timeline",
    "normalized_warps",
    "timeline_to_media",
    "video_parts",
]
