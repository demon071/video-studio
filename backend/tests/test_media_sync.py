"""atempo maths and the audio-sync contract (R = T_tts / T_orig, clamped)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import resolve_ffmpeg
from app.schemas.subtitle import SubtitleSegment
from app.services import tts_service
from app.services.media_processor import (
    atempo_chain,
    probe_duration,
    render_final,
    stretch_to_fit,
)

FFMPEG = resolve_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


def test_atempo_chain_stays_inside_single_stage_range() -> None:
    assert atempo_chain(1.35) == "atempo=1.35000"
    assert atempo_chain(0.8) == "atempo=0.80000"


def test_atempo_chain_handles_extreme_ratios() -> None:
    assert atempo_chain(3.0) == "atempo=2.00000,atempo=1.50000"
    assert atempo_chain(0.4) == "atempo=0.50000,atempo=0.80000"


def test_atempo_chain_rejects_zero() -> None:
    with pytest.raises(ValueError):
        atempo_chain(0)


def _tone(path: Path, seconds: float, *, frequency: int = 440) -> Path:
    assert FFMPEG is not None
    subprocess.run(  # noqa: S603 - fixed arguments
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={seconds}",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )
    return path


@needs_ffmpeg
def test_stretch_to_fit_clamps_and_never_overruns(tmp_path: Path) -> None:
    """A 3 s TTS clip in a 2 s slot needs R = 1.5, capped at 1.35 and trimmed."""
    source = _tone(tmp_path / "tts.wav", 3.0)
    target = tmp_path / "aligned.wav"

    report = stretch_to_fit(source, target, index=1, start=5.0, end=7.0, max_ratio=1.35)

    assert report.clamped is True
    assert report.applied_ratio == pytest.approx(1.35, abs=1e-4)
    assert report.target_duration == pytest.approx(2.0, abs=1e-3)
    assert report.tts_duration == pytest.approx(3.0, abs=0.05)
    # 3 s / 1.35 = 2.22 s: 0.22 s of speech still has to be trimmed.
    assert report.overflow_s == pytest.approx(0.22, abs=0.06)

    produced = probe_duration(target)
    assert produced == pytest.approx(2.0, abs=0.08)
    # The clip must fit inside the slot so it cannot bleed into the next line.
    assert produced <= report.target_duration + 0.05


@needs_ffmpeg
def test_stretch_to_fit_leaves_short_clips_alone(tmp_path: Path) -> None:
    source = _tone(tmp_path / "short.wav", 1.0)
    report = stretch_to_fit(source, tmp_path / "out.wav", index=2, start=0.0, end=4.0)

    assert report.clamped is False
    assert report.applied_ratio == 1.0
    assert probe_duration(report.output_path) == pytest.approx(1.0, abs=0.08)


@pytest.mark.skipif(shutil.which("ffmpeg") is None and FFMPEG is None, reason="ffmpeg missing")
def test_ffmpeg_is_resolvable() -> None:
    assert FFMPEG is not None and FFMPEG.is_file()


def test_slot_borrows_the_silence_before_the_next_line() -> None:
    segments = [
        SubtitleSegment(index=1, start=0.0, end=2.0, text="a"),
        SubtitleSegment(index=2, start=6.0, end=7.0, text="b"),
    ]
    slots = tts_service._slot_ends(segments)

    assert slots[1] == pytest.approx(2.0 + tts_service.GAP_BORROW_S)
    assert slots[2] == 7.0  # the last line keeps its own end


def test_slot_never_runs_into_the_next_cue() -> None:
    segments = [
        SubtitleSegment(index=1, start=0.0, end=2.0, text="a"),
        SubtitleSegment(index=2, start=2.4, end=3.0, text="b"),
    ]
    slots = tts_service._slot_ends(segments)

    assert slots[1] <= segments[1].start - 0.05 + 1e-9


def test_char_budget_tracks_the_sentence_and_has_a_floor() -> None:
    assert tts_service._char_budget(2.0) == int(2.0 * tts_service.CHARS_PER_SECOND)
    assert tts_service._char_budget(0.1) == tts_service.MIN_SLOT_CHARS


def test_plan_keeps_lines_that_fit_on_their_cue() -> None:
    plan = tts_service.plan_schedule(
        [(1, 0.0, 2.0, 1.5), (2, 5.0, 7.0, 1.0)],
        max_ratio=1.35,
    )

    assert plan[1].start == 0.0 and plan[1].ratio == 1.0
    assert plan[2].start == 5.0 and plan[2].ratio == 1.0
    assert not plan[1].clamped and not plan[2].clamped


def test_plan_spreads_one_long_line_over_its_back_to_back_group() -> None:
    # Three cues with no silence between them; only the middle take is long.
    # Per-line fitting would need 2.6/1.0 = 2.6x on line 2 and trim it; the
    # group has 3.0s of cues + borrowed silence for 4.1s of speech.
    plan = tts_service.plan_schedule(
        [(1, 0.0, 1.0, 0.7), (2, 1.0, 2.0, 2.6), (3, 2.0, 3.0, 0.8)],
        max_ratio=1.35,
    )

    ratios = {item.ratio for item in plan.values()}
    assert len(ratios) == 1  # one uniform speed for the whole group
    assert max(ratios) <= 1.35
    # Lines never overlap and keep a breath between them.
    assert plan[1].end + tts_service.CLIP_GAP_S <= plan[2].start + 1e-6
    assert plan[2].end + tts_service.CLIP_GAP_S <= plan[3].start + 1e-6


def test_plan_never_trims_and_pushes_the_next_group_back() -> None:
    # Far too much speech for the slot: capped at max_ratio, the rest spills.
    plan = tts_service.plan_schedule(
        [(1, 0.0, 1.0, 4.0), (2, 3.0, 4.0, 0.5)],
        max_ratio=1.35,
    )

    assert plan[1].clamped
    assert plan[1].ratio == pytest.approx(1.35)
    assert plan[1].end - plan[1].start == pytest.approx(4.0 / 1.35, abs=0.01)
    assert plan[2].start >= plan[1].end  # shifted, not overlapping


def test_plan_may_start_a_line_early_into_preceding_silence() -> None:
    # The next group starts at 4.6s, so lines 1-2 (2.58s of speech) do not fit
    # between 2.0s and 4.52s at normal speed - starting early avoids a speed-up.
    plan = tts_service.plan_schedule(
        [(1, 2.0, 3.0, 1.8), (2, 3.0, 4.0, 0.7), (3, 4.6, 5.0, 0.3)],
        max_ratio=1.35,
    )

    assert plan[1].ratio == 1.0
    assert plan[1].start < 2.0
    assert plan[1].start >= 2.0 - tts_service.LEAD_S - 1e-6
    assert plan[2].end <= 4.6 - tts_service.CLIP_GAP_S + 1e-6


# ---------------------------------------------------------------------------
# Dubbing keeps the clip's own audio, only quieter
# ---------------------------------------------------------------------------
def _clip_with_tone(path: Path, seconds: float) -> Path:
    """A tiny clip whose soundtrack is one full-scale tone."""
    assert FFMPEG is not None
    subprocess.run(  # noqa: S603 - fixed arguments
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=180x320:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    return path


def _silence(path: Path, seconds: float) -> Path:
    """A dub track that says nothing, so only the bed can make a sound."""
    assert FFMPEG is not None
    subprocess.run(  # noqa: S603 - fixed arguments
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=24000:cl=mono:d={seconds}",
            str(path),
        ],
        check=True,
    )
    return path


def _mean_volume_db(path: Path, frequency: int | None = None) -> float:
    if frequency is None:
        audio_filter = "volumedetect"
    else:
        # Only the bed's own tone reaches the meter, so the voice on top of it
        # cannot hide a ducked original.
        audio_filter = f"bandpass=f={frequency}:width_type=h:w=40,volumedetect"
    assert FFMPEG is not None
    completed = subprocess.run(  # noqa: S603 - fixed arguments
        [
            str(FFMPEG),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            audio_filter,
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?|inf) dB", completed.stderr)
    assert match, completed.stderr
    return float("-91") if match.group(1) == "-inf" else float(match.group(1))


@needs_ffmpeg
def test_render_keeps_the_original_track_under_the_dub(tmp_path: Path) -> None:
    """With a mute dub the output must still carry the clip's own sound."""
    clip = _clip_with_tone(tmp_path / "clip.mp4", 2.0)
    dub = _silence(tmp_path / "dub.wav", 2.0)
    output = tmp_path / "final.mp4"

    render_final(
        clip,
        output,
        aspect="original",
        dub_track_path=dub,
        keep_original_audio=True,
        background_gain=0.5,
    )

    # The tone is at half gain, so it is quieter than the source but far from
    # silence: anything below -30 dB would mean the original was dropped.
    assert -30.0 < _mean_volume_db(output) < -3.0
    assert probe_duration(output) == pytest.approx(2.0, abs=0.2)


@needs_ffmpeg
def test_render_can_drop_the_original_when_the_user_asks(tmp_path: Path) -> None:
    clip = _clip_with_tone(tmp_path / "clip.mp4", 2.0)
    dub = _silence(tmp_path / "dub.wav", 2.0)
    output = tmp_path / "final.mp4"

    render_final(clip, output, aspect="original", dub_track_path=dub, keep_original_audio=False)

    # Only the silent dub is mapped, so the render is digital silence.
    assert _mean_volume_db(output) < -60.0


@needs_ffmpeg
def test_render_ducks_the_original_below_the_voice(tmp_path: Path) -> None:
    """The sidechain pulls the bed down while a dubbed line is speaking."""
    clip = _clip_with_tone(tmp_path / "clip.mp4", 2.0)
    quiet_dub = _silence(tmp_path / "quiet.wav", 2.0)
    loud_dub = _tone(tmp_path / "loud.wav", 2.0, frequency=1000)
    quiet = tmp_path / "quiet.mp4"
    ducked = tmp_path / "ducked.mp4"

    # Same gain in both renders, so the only difference is the compressor.
    render_final(clip, quiet, aspect="original", dub_track_path=quiet_dub, keep_original_audio=True)
    render_final(clip, ducked, aspect="original", dub_track_path=loud_dub, keep_original_audio=True)

    # Both keep the 440 Hz bed at the same level; the second one has a voice on
    # top of it, so its bed must sit measurably lower.
    assert _mean_volume_db(ducked, 440) < _mean_volume_db(quiet, 440) - 2.0
