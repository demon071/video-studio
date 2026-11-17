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

    report(stage="tts", fraction=0.0, message=f"{total_lines} lines via {chosen}/{speaker}")

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
        line = _render_line(
            segment,
            text,
            provider=chosen,
            voice=line_voice,
            rate=line_rate,
            folder=folder,
            slot_end=slots[segment.index],
            timing_rewrite=timing_rewrite,
            trim_silence=trim_silence,
            language=spoken_language,
            report=report,
            settings=settings,
        )
        if line.clip_path:
            clips.append((segment.start, Path(line.clip_path)))
        reports.append(line)
        report(
            fraction=position / total_lines,
            message=f"dubbed line {position}/{total_lines}",
            data={"index": segment.index, "ratio": line.applied_ratio, "clamped": line.clamped},
        )

    if not clips:
        raise TtsError("Every subtitle line was empty - nothing to dub")

    rewritten = sum(1 for item in reports if item.rewritten)

    duration = max((item.end for item in segments), default=0.0)
    if video_path:
        try:
            duration = max(duration, media_processor.probe_duration(video_path))
        except MediaError as exc:
            log.warning("cannot probe video duration: %s", exc)
    duration = max(duration, reports[-1].end, 0.5)

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
            clips.append((item.start, item.clip_path))
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
    "TtsError",
    "cue",
    "dub",
    "preview",
    "remix",
    "resolve_voice",
    "synthesize",
]
