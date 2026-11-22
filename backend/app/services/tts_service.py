"""Text-to-speech and the dubbing assembly pipeline.

The sync contract from the spec lives in :func:`dub`:

    T_orig = end - start
    R      = T_tts / T_orig            (clamped to SVA_MAX_TEMPO_RATIO = 1.35)
    ffmpeg atempo=R  ->  clip fits exactly in [start, end]

Every line is synthesised, time-compressed by ``media_processor.stretch_to_fit``
and then dropped onto a silent bed at its own offset. The result is one mono
WAV of voice only: the clip's own soundtrack is kept (and ducked) later, by
``media_processor.render_final``, so nothing of the original is lost.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.core.progress import NULL_REPORTER, Reporter
from app.schemas.subtitle import DubbingSegmentReport, SubtitleSegment
from app.services import (
    capcut_client,
    elevenlabs_tts,
    media_processor,
    omnivoice_tts,
    piper_tts,
    provider_config,
    translate_service,
    vieneu_tts,
)
from app.services.media_processor import MediaError, SyncReport

log = logging.getLogger(__name__)

# CapCut needs no account and sounds right in Vietnamese on the first try, so it
# stays what an empty provider box runs. OmniVoice is the offline torch engine
# and ElevenLabs is the cloud studio alternative (all ported from CapCap).
DEFAULT_PROVIDER = "capcut"


class TtsError(RuntimeError):
    """Raised when a TTS provider fails."""


@dataclass(frozen=True)
class DubOutcome:
    track_path: Path
    duration: float
    provider: str
    voice: str
    reports: list[DubbingSegmentReport] = field(default_factory=list)
    skipped: int = 0
    rewritten: int = 0
    warps: list[dict[str, float | str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Provider primitives
# ---------------------------------------------------------------------------
def _synth_omnivoice(text: str, target: Path, *, voice: str, settings: Settings) -> Path:
    try:
        return omnivoice_tts.synthesize(text, target, voice=voice, settings=settings)
    except omnivoice_tts.OmnivoiceError as exc:
        raise TtsError(str(exc)) from exc


def _rate_factor(rate: str | None) -> float:
    """``"+15%"`` -> 1.15, the shape CapCut's SSML ``prosody`` expects."""
    text = (rate or "").strip().rstrip("%")
    try:
        factor = 1.0 + float(text) / 100.0
    except (TypeError, ValueError):
        return 1.0
    return min(max(factor, 0.5), 2.0)


def _synth_capcut(text: str, target: Path, *, voice: str, rate: str | None, settings: Settings) -> Path:
    try:
        audio = capcut_client.synthesize(
            text,
            voice=voice,
            rate=_rate_factor(rate),
            settings=settings,
        )
    except capcut_client.CapCutError as exc:
        raise TtsError(str(exc)) from exc

    target.write_bytes(audio)
    if not target.is_file() or target.stat().st_size < 512:
        raise TtsError("CapCut produced no audio")
    return target


def _synth_elevenlabs(text: str, target: Path, *, voice: str, settings: Settings) -> Path:
    try:
        return elevenlabs_tts.synthesize(text, target, voice=voice, settings=settings)
    except elevenlabs_tts.ElevenLabsError as exc:
        raise TtsError(str(exc)) from exc


def _synth_piper(text: str, target: Path, *, voice: str, rate: str | None, settings: Settings) -> Path:
    try:
        return piper_tts.synthesize(text, target, voice=voice, speed=_rate_factor(rate), settings=settings)
    except piper_tts.PiperError as exc:
        raise TtsError(str(exc)) from exc


def _synth_vieneu(text: str, target: Path, *, voice: str, settings: Settings) -> Path:
    try:
        return vieneu_tts.synthesize(text, target, voice=voice, settings=settings)
    except vieneu_tts.VieNeuError as exc:
        raise TtsError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Public primitives
# ---------------------------------------------------------------------------
def resolve_voice(provider: str, voice: str | None, settings: Settings) -> str:
    chosen = (provider or DEFAULT_PROVIDER).lower()
    if chosen == "omnivoice":
        return omnivoice_tts.resolve_voice(voice, settings)
    if chosen == "elevenlabs":
        return elevenlabs_tts.resolve_voice(voice, settings)
    if chosen == "piper":
        return piper_tts.resolve_voice(voice, settings)
    if chosen == "vieneu":
        return vieneu_tts.resolve_voice(voice, settings)
    if chosen == "capcut":
        return (voice or settings.capcut_voice or capcut_client.DEFAULT_TTS_VOICE).strip()
    if voice:
        return voice
    raise TtsError(f"Unsupported TTS provider: {chosen}")


def synthesize(
    text: str,
    output_path: str | Path,
    *,
    provider: str | None = None,
    voice: str | None = None,
    rate: str | None = None,
    settings: Settings | None = None,
) -> Path:
    """Synthesise one line with no timeline constraints (used for previews)."""
    settings = provider_config.overlay(settings or get_settings())
    chosen = (provider or settings.tts_provider or DEFAULT_PROVIDER).lower()
    speaker = resolve_voice(chosen, voice, settings)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    if chosen == "capcut":
        return _synth_capcut(text, target.with_suffix(".mp3"), voice=speaker, rate=rate, settings=settings)
    if chosen == "elevenlabs":
        return _synth_elevenlabs(text, target.with_suffix(".mp3"), voice=speaker, settings=settings)
    if chosen == "omnivoice":
        return _synth_omnivoice(text, target.with_suffix(".wav"), voice=speaker, settings=settings)
    if chosen == "piper":
        return _synth_piper(text, target.with_suffix(".wav"), voice=speaker, rate=rate, settings=settings)
    if chosen == "vieneu":
        return _synth_vieneu(text, target.with_suffix(".wav"), voice=speaker, settings=settings)
    raise TtsError(f"Unsupported TTS provider: {chosen}")


def _raw_extension(provider: str) -> str:
    return "mp3" if (provider or DEFAULT_PROVIDER).lower() in {"capcut", "elevenlabs"} else "wav"


# ---------------------------------------------------------------------------
# Dubbing pipeline
# ---------------------------------------------------------------------------
# Spoken Vietnamese runs at roughly 14-15 characters per second. A line denser
# than that is the one the renderer would have to speed up, so it is rewritten
# first (CapCap's "timing rescue") instead of being mechanically compressed.
CHARS_PER_SECOND = 14.5
MIN_SLOT_CHARS = 8
GAP_BORROW_S = 1.5


def _slot_ends(ordered: list[SubtitleSegment]) -> dict[int, float]:
    """How late each line may finish without stepping on the next one.

    Subtitles are cut where the speech *ends*, so most lines are followed by
    silence that belongs to nobody. Borrowing it (up to ``GAP_BORROW_S`` and
    never past the next cue) removes most speed-ups, which is the difference
    between "dubbed" and "dubbed and understandable".
    """
    ends: dict[int, float] = {}
    for position, segment in enumerate(ordered):
        end = segment.end
        if position + 1 < len(ordered):
            limit = min(ordered[position + 1].start - 0.05, segment.end + GAP_BORROW_S)
            end = max(end, limit)
        ends[segment.index] = end
    return ends


def _char_budget(seconds: float, *, rate: float = CHARS_PER_SECOND) -> int:
    return max(MIN_SLOT_CHARS, int(seconds * rate))


# ---------------------------------------------------------------------------
# Group scheduling: share the speed-up over a run of back-to-back lines
# ---------------------------------------------------------------------------
# Fitting every line into its own slot fails exactly where dialogue is dense:
# a line with no silence after it has to be sped up hard and then *trimmed*,
# while its neighbours may have time to spare. The planner instead treats lines
# that follow each other closely as one group, spreads one moderate speed-up
# over the whole group, lets a line start a little early or a little late, and
# never cuts a word - a group that still runs long pushes the next one back.
LEAD_S = 0.3        # a line may start this much before its cue (into silence)
CLIP_GAP_S = 0.08   # breath kept between two dubbed lines
JOIN_GAP_S = 0.4    # cues closer than this belong to the same group


@dataclass(frozen=True)
class Placement:
    index: int
    start: float
    end: float
    ratio: float
    needed: float
    clamped: bool
    group_size: int


def plan_schedule(
    items: Sequence[tuple[int, float, float, float]],
    *,
    max_ratio: float,
    end_limit: float | None = None,
) -> dict[int, Placement]:
    """Place ``(index, cue_start, cue_end, tts_seconds)`` lines on the track.

    Returns where each clip starts and how fast it must play. Lines keep their
    cue start whenever the group has room; otherwise they move earlier (into the
    silence before the group) or later (after the previous line) so the group's
    speed-up stays uniform and as small as possible.
    """
    ordered = sorted(items, key=lambda item: (item[1], item[0]))
    if not ordered:
        return {}

    groups: list[list[tuple[int, float, float, float]]] = [[ordered[0]]]
    for previous, item in zip(ordered, ordered[1:]):
        if item[1] - previous[2] < JOIN_GAP_S:
            groups[-1].append(item)
        else:
            groups.append([item])

    plan: dict[int, Placement] = {}
    cursor: float | None = None  # where the previous group's last clip ended
    max_ratio = max(1.0, float(max_ratio))

    for position, group in enumerate(groups):
        first_start, last_end = group[0][1], group[-1][2]
        win_start = max(0.0, first_start - LEAD_S)
        if cursor is not None:
            win_start = max(win_start, cursor + CLIP_GAP_S)
        if position + 1 < len(groups):
            win_end = min(groups[position + 1][0][1] - CLIP_GAP_S, last_end + GAP_BORROW_S)
        else:
            win_end = last_end + GAP_BORROW_S
            if end_limit is not None:
                win_end = min(win_end, end_limit)
        win_end = max(win_end, win_start + 0.05)

        speech = sum(max(item[3], 0.0) for item in group) + CLIP_GAP_S * (len(group) - 1)
        needed = speech / (win_end - win_start)
        ratio = 1.0 if needed <= 1.0 else min(needed, max_ratio)
        clamped = needed > max_ratio + 1e-3
        lengths = [max(item[3], 0.0) / ratio for item in group]

        # Latest start that still lets every following line end by `win_end`.
        latest: list[float] = [0.0] * len(group)
        moment = win_end
        for k in range(len(group) - 1, -1, -1):
            moment -= lengths[k]
            latest[k] = moment
            moment -= CLIP_GAP_S

        floor = win_start
        for k, (index, cue_start, _cue_end, _seconds) in enumerate(group):
            start = max(floor, min(cue_start, latest[k]))
            end = start + lengths[k]
            plan[index] = Placement(
                index=index,
                start=round(start, 3),
                end=round(end, 3),
                ratio=round(ratio, 4),
                needed=round(needed, 4),
                clamped=clamped,
                group_size=len(group),
            )
            floor = end + CLIP_GAP_S
        cursor = floor - CLIP_GAP_S
    return plan


@dataclass
class _Take:
    segment: SubtitleSegment
    source: str
    spoken: str
    raw: Path
    seconds: float
    voice: str
    rate: str | None
    rewritten: bool = False


def _speak_take(
    segment: SubtitleSegment,
    text: str,
    *,
    provider: str,
    voice: str,
    rate: str | None,
    folder: Path,
    trim_silence: bool,
    settings: Settings,
) -> tuple[Path, float]:
    """Synthesise one line, drop its boundary silence and measure it."""
    raw = folder / f"line_{segment.index:03d}.{_raw_extension(provider)}"
    try:
        produced = synthesize(text, raw, provider=provider, voice=voice, rate=rate, settings=settings)
        clip = Path(produced) if produced else raw
        if trim_silence:
            media_processor.trim_boundary_silence(clip)
        return clip, media_processor.probe_duration(clip)
    except (TtsError, MediaError) as exc:
        raise TtsError(f"line {segment.index}: {exc}") from exc


def _prepare_rewrite(timing_rewrite: bool, settings: Settings) -> bool:
    """Only LLM-backed providers can shorten a line to fit its time slot."""
    if not timing_rewrite:
        return False
    return (settings.translate_provider or "google").lower() in {
        "gemini",
        "openai",
        "deepseek",
        "ollama",
    }


def _render_line(
    segment: SubtitleSegment,
    text: str,
    *,
    provider: str,
    voice: str,
    rate: str | None,
    folder: Path,
    slot_end: float,
    timing_rewrite: bool,
    trim_silence: bool = True,
    language: str,
    settings: Settings,
    report: Reporter = NULL_REPORTER,
) -> DubbingSegmentReport:
    """Speak one line, reword it when it cannot fit, and land it in its slot."""
    raw = folder / f"line_{segment.index:03d}.{_raw_extension(provider)}"
    aligned = folder / f"line_{segment.index:03d}.aligned.wav"

    def render(spoken: str) -> SyncReport:
        try:
            synthesize(spoken, raw, provider=provider, voice=voice, rate=rate, settings=settings)
            return media_processor.stretch_to_fit(
                raw,
                aligned,
                index=segment.index,
                start=segment.start,
                end=slot_end,
                trim_silence=trim_silence,
            )
        except (TtsError, MediaError) as exc:
            raise TtsError(f"line {segment.index}: {exc}") from exc

    slot = max(slot_end - segment.start, 0.05)
    spoken, did_rewrite = text, False

    # A line far denser than its slot is rewritten before the first synthesis:
    # cheaper than rendering audio that has to be thrown away.
    if timing_rewrite and len(text) > _char_budget(slot) * 1.25:
        candidate = translate_service.shorten_for_timing(
            text,
            max_chars=_char_budget(slot),
            language=language,
            settings=settings,
        )
        if candidate != text:
            spoken, did_rewrite = candidate, True
            report(message=f"line {segment.index} rewritten to fit {slot:.2f}s")

    sync = render(spoken)

    # Measured rescue: the estimate above is only a guess, this one uses the
    # real speaking rate of this voice on this line.
    if sync.clamped and timing_rewrite and not did_rewrite:
        seen = min(max(len(spoken) / max(sync.tts_duration, 0.05), 6.0), 30.0)
        candidate = translate_service.shorten_for_timing(
            spoken,
            max_chars=_char_budget(slot, rate=seen),
            language=language,
            settings=settings,
        )
        if candidate != spoken:
            spoken, did_rewrite = candidate, True
            sync = render(spoken)
            report(message=f"line {segment.index} shortened after measuring {seen:.1f} chars/s")

    if sync.clamped:
        report(
            message=(
                f"line {segment.index} needed {sync.tts_duration / max(sync.target_duration, 0.01):.2f}x, "
                f"capped at {sync.applied_ratio:.2f}x"
                + (f", {sync.overflow_s:.2f}s trimmed" if sync.overflow_s > 0.05 else "")
            )
        )

    return DubbingSegmentReport(
        index=segment.index,
        start=sync.start,
        end=sync.end,
        target_duration=sync.target_duration,
        tts_duration=sync.tts_duration,
        applied_ratio=sync.applied_ratio,
        clamped=sync.clamped,
        overflow_s=sync.overflow_s,
        rewritten=did_rewrite,
        spoken=spoken,
        source=text,
        clip_path=str(sync.output_path),
    )


def dub(
    segments: list[SubtitleSegment],
    *,
    work_dir: str | Path,
    provider: str | None = None,
    voice: str | None = None,
    speaker_voices: dict[str, str] | None = None,
    rate: str | None = None,
    use_translation: bool = True,
    timing_rewrite: bool = True,
    trim_silence: bool = True,
    language: str | None = None,
    video_path: str | Path | None = None,
    fit_voice_mode: str = "off",
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> DubOutcome:
    """Synthesise every line, fit it into its slot and assemble one track."""
    settings = provider_config.overlay(settings or get_settings())
    if not segments:
        raise TtsError("No subtitle lines to dub")

    chosen = (provider or settings.tts_provider or DEFAULT_PROVIDER).lower()
    speaker = resolve_voice(chosen, voice, settings)
    folder = Path(work_dir)
    folder.mkdir(parents=True, exist_ok=True)
    spoken_language = language or settings.target_language

    # A local rewrite needs the LLM, and the LLM needs the VRAM whisper was
    # holding. Cloud providers need neither, so they are never disabled here.
    timing_rewrite = _prepare_rewrite(timing_rewrite, settings)

    ordered = sorted(segments, key=lambda item: (item.start, item.index))
    slots = _slot_ends(ordered)
    total_lines = len(ordered)
    reports: list[DubbingSegmentReport] = []
    clips: list[tuple[float, Path]] = []
    skipped = 0
    speaker_map = dict(speaker_voices or {})
    max_ratio = float(settings.max_tempo_ratio or 1.35)

    report(stage="tts", fraction=0.0, message=f"{total_lines} lines via {chosen}/{speaker}")

    # 1) Speak every line first: the planner needs the real length of each take.
    #    Cloud providers (CapCut, ElevenLabs) run in parallel (up to 4 workers,
    #    ported from CapCap voice_workflow.py) for 3-4x faster dubbing.
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    prepared_jobs: list[tuple[int, SubtitleSegment, str, str, str, str | None, bool]] = []
    for position, segment in enumerate(ordered, start=1):
        text = (segment.translation if use_translation and segment.translation else segment.text) or ""
        text = text.strip()
        if not text:
            skipped += 1
            continue

        line_voice = (
            segment.voice
            or (speaker_map.get(segment.speaker) if segment.speaker else None)
            or speaker
        )
        line_rate = (
            f"{int(round((segment.speed - 1.0) * 100)):+d}%"
            if segment.speed is not None and segment.speed > 0
            else rate
        )

        spoken, did_rewrite = text, False
        slot = max(slots[segment.index] - segment.start, 0.05)
        if timing_rewrite and len(text) > _char_budget(slot) * 1.25:
            candidate = translate_service.shorten_for_timing(
                text,
                max_chars=_char_budget(slot),
                language=spoken_language,
                settings=settings,
            )
            if candidate != text:
                spoken, did_rewrite = candidate, True
                report(message=f"line {segment.index} rewritten to fit {slot:.2f}s")

        prepared_jobs.append((position, segment, text, spoken, line_voice, line_rate, did_rewrite))

    def _run_one_job(job: tuple[int, SubtitleSegment, str, str, str, str | None, bool]) -> tuple[int, _Take]:
        pos, seg, src_text, spk_text, l_voice, l_rate, rewritten_flag = job
        raw_path, secs = _speak_take(
            seg,
            spk_text,
            provider=chosen,
            voice=l_voice,
            rate=l_rate,
            folder=folder,
            trim_silence=trim_silence,
            settings=settings,
        )
        return pos, _Take(
            segment=seg,
            source=src_text,
            spoken=spk_text,
            raw=raw_path,
            seconds=secs,
            voice=l_voice,
            rate=l_rate,
            rewritten=rewritten_flag,
        )

    takes: list[_Take] = []
    use_parallel = chosen in {"capcut", "elevenlabs"} and len(prepared_jobs) > 1
    if use_parallel:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for done_count, (position, take_item) in enumerate(pool.map(_run_one_job, prepared_jobs), start=1):
                takes.append(take_item)
                report(
                    fraction=0.85 * done_count / max(len(prepared_jobs), 1),
                    message=f"spoke line {done_count}/{len(prepared_jobs)}",
                    data={"index": take_item.segment.index},
                )
    else:
        for job in prepared_jobs:
            position, take_item = _run_one_job(job)
            takes.append(take_item)
            report(
                fraction=0.85 * position / total_lines,
                message=f"spoke line {position}/{total_lines}",
                data={"index": take_item.segment.index},
            )

    if not takes:
        raise TtsError("Every subtitle line was empty - nothing to dub")

    video_duration: float | None = None
    if video_path:
        try:
            video_duration = media_processor.probe_duration(video_path)
        except MediaError as exc:
            log.warning("cannot probe video duration: %s", exc)

    def schedule() -> dict[int, Placement]:
        return plan_schedule(
            [(t.segment.index, t.segment.start, t.segment.end, t.seconds) for t in takes],
            max_ratio=max_ratio,
            end_limit=video_duration,
        )

    # 2) Plan the whole track; a group that still needs too much speed-up gets
    #    its lines reworded (LLM providers only) and is planned again.
    plan = schedule()
    if timing_rewrite:
        changed = False
        for take in takes:
            placed = plan[take.segment.index]
            if not placed.clamped or take.rewritten:
                continue
            share = max_ratio / max(placed.needed, 0.01)
            candidate = translate_service.shorten_for_timing(
                take.spoken,
                max_chars=max(MIN_SLOT_CHARS, int(len(take.spoken) * share * 0.95)),
                language=spoken_language,
                settings=settings,
            )
            if candidate == take.spoken:
                continue
            take.raw, take.seconds = _speak_take(
                take.segment,
                candidate,
                provider=chosen,
                voice=take.voice,
                rate=take.rate,
                folder=folder,
                trim_silence=trim_silence,
                settings=settings,
            )
            take.spoken, take.rewritten, changed = candidate, True, True
            report(message=f"line {take.segment.index} shortened to fit its group")
        if changed:
            plan = schedule()

    # 3) Fit Voice time-warp planning (ported from CapCap time_warp_service.py):
    #    When fit_voice_mode is "slow" or "freeze", any line that still exceeds
    #    max_ratio stretches or freezes its video segment instead of clamping/shifting.
    warps: list[dict[str, float | str]] = []
    if fit_voice_mode in {"slow", "freeze"}:
        from app.services import time_warp_service  # noqa: PLC0415

        raw_warps: list[dict[str, float | str]] = []
        for take in takes:
            placed = plan[take.segment.index]
            if not placed.clamped:
                continue
            slot_len = max(take.segment.end - take.segment.start, 0.15)
            comfortable_len = take.seconds / min(max_ratio, 1.18)
            extra = round(max(0.0, comfortable_len - slot_len), 3)
            if extra >= 0.08:
                raw_warps.append(
                    {
                        "type": fit_voice_mode,
                        "time": round(take.segment.end, 3),
                        "media_start": round(take.segment.start, 3),
                        "media_end": round(take.segment.end, 3),
                        "duration": extra,
                    }
                )
        if raw_warps:
            warps = time_warp_service.normalized_warps(raw_warps)
            total_added = sum(float(w.get("duration", 0.0)) for w in warps)
            warped_items = [
                (
                    t.segment.index,
                    round(time_warp_service.media_to_timeline(t.segment.start, warps), 3),
                    round(time_warp_service.media_to_timeline(t.segment.end, warps), 3),
                    t.seconds,
                )
                for t in takes
            ]
            plan = plan_schedule(
                warped_items,
                max_ratio=max_ratio,
                end_limit=(video_duration + total_added) if video_duration else None,
            )
            report(message=f"Fit Voice ({fit_voice_mode}): stretched {len(warps)} segment(s) (+{total_added:.2f}s)")

    # 4) Render each take at the planned speed - nothing is trimmed.
    for take in takes:
        index = take.segment.index
        placed = plan[index]
        aligned = folder / f"line_{index:03d}.aligned.wav"
        try:
            media_processor.apply_tempo(take.raw, aligned, ratio=placed.ratio)
        except MediaError as exc:
            raise TtsError(f"line {index}: {exc}") from exc
        if placed.clamped:
            report(
                message=(
                    f"line {index}: group of {placed.group_size} needs {placed.needed:.2f}x, "
                    f"played at {placed.ratio:.2f}x and shifted to {placed.start:.2f}s"
                )
            )
        clips.append((placed.start, aligned))
        reports.append(
            DubbingSegmentReport(
                index=index,
                start=placed.start,
                end=placed.end,
                target_duration=round(placed.end - placed.start, 3),
                tts_duration=round(take.seconds, 3),
                applied_ratio=placed.ratio,
                clamped=placed.clamped,
                overflow_s=0.0,
                rewritten=take.rewritten,
                spoken=take.spoken,
                source=take.source,
                clip_path=str(aligned),
            )
        )

    rewritten = sum(1 for item in reports if item.rewritten)

    duration = max((item.end for item in segments), default=0.0)
    if video_duration:
        duration = max(duration, video_duration)
    duration = max(duration, max(item.end for item in reports), 0.5)

    track = media_processor.build_dub_track(
        clips,
        folder / "dub_track.wav",
        total_duration=duration,
        progress=lambda value: report(stage="mix", fraction=value, message="assembling dub track"),
    )

    rewritten_note = f" ({rewritten} rewritten to fit)" if rewritten else ""
    report(
        stage="done",
        fraction=1.0,
        message=f"{len(reports)} lines dubbed{rewritten_note}",
    )
    return DubOutcome(
        track_path=track,
        duration=round(duration, 3),
        provider=chosen,
        voice=speaker,
        reports=reports,
        skipped=skipped,
        rewritten=rewritten,
        warps=warps,
    )


@dataclass(frozen=True)
class CueOutcome:
    report: DubbingSegmentReport
    provider: str
    voice: str


def cue(
    segments: list[SubtitleSegment],
    index: int,
    *,
    work_dir: str | Path,
    provider: str | None = None,
    voice: str | None = None,
    rate: str | None = None,
    use_translation: bool = True,
    timing_rewrite: bool = True,
    audition: bool = False,
    language: str | None = None,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> CueOutcome:
    """Re-speak one cue, with the same slot maths :func:`dub` used for it.

    The whole track is passed in because a cue may borrow the silence that
    follows it - rendering it alone would make it shorter than the take it
    replaces. Only the one line is written; assembling the track is :func:`remix`.

    ``audition`` is the "let me hear it" call: the line is spoken exactly as it
    is written. Rewording to fit the slot is skipped there because it is a
    translation-model pass, and pressing play must not start one.
    """
    settings = provider_config.overlay(settings or get_settings())
    if not segments:
        raise TtsError("No subtitle lines to dub")

    target = next((item for item in segments if item.index == index), None)
    if target is None:
        raise TtsError(f"Line {index} is not on the timeline")

    text = ((target.translation if use_translation and target.translation else target.text) or "").strip()
    if not text:
        raise TtsError(f"Line {index} has no text to speak")

    chosen = (provider or settings.tts_provider or DEFAULT_PROVIDER).lower()
    speaker = resolve_voice(chosen, voice, settings)
    folder = Path(work_dir)
    folder.mkdir(parents=True, exist_ok=True)

    ordered = sorted(segments, key=lambda item: (item.start, item.index))
    report(stage="tts", fraction=0.0, message=f"line {index} via {chosen}/{speaker}")
    line = _render_line(
        target,
        text,
        provider=chosen,
        voice=speaker,
        rate=rate,
        folder=folder,
        slot_end=_slot_ends(ordered)[index],
        timing_rewrite=_prepare_rewrite(timing_rewrite and not audition, settings),
        language=language or settings.target_language,
        report=report,
        settings=settings,
    )
    report(stage="done", fraction=1.0, message=f"line {index} ready")
    return CueOutcome(report=line, provider=chosen, voice=speaker)


def remix(
    reports: Sequence[DubbingSegmentReport],
    *,
    output_path: str | Path,
    duration: float,
    report: Reporter = NULL_REPORTER,
) -> Path:
    """Re-assemble the dub track from the per-line takes already on disk."""
    clips: list[tuple[float, str]] = []
    missing: list[int] = []
    for item in sorted(reports, key=lambda entry: entry.start):
        if item.clip_path and Path(item.clip_path).is_file():
            clip_p = Path(item.clip_path)
            desired_dur = max(item.end - item.start, 0.15)
            if clip_p.name.endswith(".aligned.wav"):
                try:
                    cur_dur = media_processor.probe_duration(clip_p)
                    if abs(cur_dur - desired_dur) > 0.04:
                        raw_wav = clip_p.with_name(f"line_{item.index:03d}.wav")
                        raw_mp3 = clip_p.with_name(f"line_{item.index:03d}.mp3")
                        raw_src = raw_wav if raw_wav.is_file() else (raw_mp3 if raw_mp3.is_file() else clip_p)
                        src_dur = media_processor.probe_duration(raw_src) if raw_src != clip_p else cur_dur
                        ratio = min(max(src_dur / desired_dur, 0.5), 2.5)
                        media_processor.apply_tempo(raw_src, clip_p, ratio=ratio)
                except MediaError as exc:
                    log.warning("Could not restretch dub clip %s: %s", clip_p, exc)
            clips.append((item.start, str(clip_p)))
        else:
            missing.append(item.index)

    if missing:
        listed = ", ".join(str(index) for index in missing[:6])
        more = "" if len(missing) <= 6 else f" (+{len(missing) - 6} more)"
        raise TtsError(f"lines {listed}{more} have no audio take left on disk - dub the video again")

    track = media_processor.build_dub_track(
        clips,
        output_path,
        total_duration=max(duration, 0.5),
        progress=lambda value: report(stage="mix", fraction=value, message="assembling dub track"),
    )
    report(stage="done", fraction=1.0, message=f"{len(clips)} lines assembled")
    return track


def preview(
    text: str,
    *,
    provider: str | None = None,
    voice: str | None = None,
    rate: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Render a single line for the voice picker (no timeline maths)."""
    settings = provider_config.overlay(settings or get_settings())
    folder = settings.temp_path / "preview"
    folder.mkdir(parents=True, exist_ok=True)
    chosen = (provider or settings.tts_provider or DEFAULT_PROVIDER).lower()
    target = synthesize(
        text,
        folder / f"preview.{_raw_extension(chosen)}",
        provider=provider,
        voice=voice,
        rate=rate,
        settings=settings,
    )
    return {
        "path": str(target),
        "provider": chosen,
        "voice": resolve_voice(chosen, voice, settings),
        "duration": round(media_processor.probe_duration(target), 3),
    }


__all__ = [
    "CueOutcome",
    "DEFAULT_PROVIDER",
    "DubOutcome",
    "Placement",
    "TtsError",
    "cue",
    "dub",
    "plan_schedule",
    "preview",
    "remix",
    "resolve_voice",
    "synthesize",
]
