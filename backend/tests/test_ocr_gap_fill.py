"""The OCR gap pass: which stretches get re-read, and what comes back.

The reference project (vsub) listened to every stretch that carried sound but
produced no line, because Whisper drops a whole window when its first second
is music. The same stretches are the ones where a burned-in subtitle still
exists, so they are re-read from the picture instead. These tests pin the
measurement (what counts as a hole) and the merge (a re-read that repeats a
neighbouring line is not appended twice), with no audio and no weights.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.schemas.subtitle import SubtitleSegment, TranscribeRequest
from app.services import ocr_service, stt_service, subtitle_gaps

RATE = subtitle_gaps.SAMPLE_RATE


def _tone(total: float, spans: list[tuple[float, float]], amplitude: float = 0.5) -> np.ndarray:
    """Silence with ``spans`` filled with a loud constant, i.e. "sound here"."""
    audio = np.zeros(int(total * RATE), dtype=np.float32)
    for start, end in spans:
        audio[int(start * RATE) : int(end * RATE)] = amplitude
    return audio


def _segment(start: float, end: float, text: str, index: int = 1) -> SubtitleSegment:
    return SubtitleSegment(index=index, start=start, end=end, text=text)


@pytest.fixture
def sources(tmp_path: Path) -> tuple[Path, Path]:
    """The clip and its 16 kHz wave: only their paths have to exist here."""
    video = tmp_path / "clip.mp4"
    wave = tmp_path / "audio.wav"
    for path in (video, wave):
        path.write_bytes(b"")
    return video, wave


# ---------------------------------------------------------------------------
# What counts as a hole
# ---------------------------------------------------------------------------
def test_a_loud_stretch_no_cue_covers_is_a_hole() -> None:
    audio = _tone(20.0, [(10.0, 14.0)])

    windows = subtitle_gaps.unread_windows(audio, cues=[])

    assert len(windows) == 1
    start, end = windows[0]
    # The scan is padded slightly outwards, so it starts before the sound does.
    assert start == pytest.approx(9.75, abs=0.15)
    assert end == pytest.approx(14.25, abs=0.15)


def test_a_stretch_a_cue_already_covers_is_not_a_hole() -> None:
    audio = _tone(20.0, [(10.0, 14.0)])

    assert subtitle_gaps.unread_windows(audio, cues=[(9.5, 14.5)]) == []


def test_the_unread_part_of_a_partly_covered_stretch_is_read() -> None:
    audio = _tone(20.0, [(10.0, 14.0)])

    windows = subtitle_gaps.unread_windows(audio, cues=[(10.0, 11.5)])

    assert len(windows) == 1
    # The cue covers itself plus 0.3 s, and the scan reaches 0.25 s back into
    # that margin so a line that starts early is not cut in half. Whatever the
    # re-read finds there is dropped as a duplicate of the cue it belongs to.
    assert windows[0][0] == pytest.approx(11.55, abs=0.1)
    assert windows[0][1] == pytest.approx(14.25, abs=0.15)


def test_quiet_audio_has_no_holes() -> None:
    audio = np.zeros(int(20.0 * RATE), dtype=np.float32)

    assert subtitle_gaps.unread_windows(audio, cues=[(0.0, 2.0)]) == []


def test_a_blip_too_short_for_a_line_is_skipped() -> None:
    audio = _tone(20.0, [(5.0, 5.4)])

    assert subtitle_gaps.unread_windows(audio, cues=[]) == []


def test_a_long_stretch_is_read_in_equal_pieces() -> None:
    audio = _tone(40.0, [(0.0, 40.0)])

    windows = subtitle_gaps.unread_windows(audio, cues=[])

    assert len(windows) >= 4
    assert all(end - start <= subtitle_gaps.MAX_GAP + 1e-6 for start, end in windows)
    # No orphan tail: every piece is long enough to be worth a frame read.
    assert all(end - start >= subtitle_gaps.MIN_GAP for start, end in windows)
    # Contiguous: the pieces tile the stretch instead of re-reading overlaps.
    assert windows[0][0] == 0.0
    assert windows[-1][1] == pytest.approx(40.0, abs=0.01)


def test_empty_audio_returns_nothing_instead_of_failing() -> None:
    assert subtitle_gaps.unread_windows(np.zeros(0, dtype=np.float32), cues=[]) == []


# ---------------------------------------------------------------------------
# Filling the holes
# ---------------------------------------------------------------------------
def _outcome() -> stt_service.SttOutcome:
    return stt_service.SttOutcome(
        language="zh",
        duration=30.0,
        segments=[
            _segment(2.0, 4.0, "你等我", index=1),
            _segment(20.0, 22.0, "对不起", index=2),
        ],
        provider="local",
        model="medium",
        device="cuda",
    )


@pytest.fixture
def holes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    spans = [(9.0, 12.0)]
    monkeypatch.setattr(subtitle_gaps, "load_waveform", lambda _path: np.zeros(RATE, dtype=np.float32))
    monkeypatch.setattr(subtitle_gaps, "unread_windows", lambda *_args, **_kwargs: spans)
    return spans


def test_the_re_read_lines_are_appended_and_the_rest_kept(
    sources: tuple[Path, Path], holes: list[tuple[float, float]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ocr_service,
        "read_windows",
        lambda *_args, **_kwargs: [
            ocr_service.WindowReading(start=9.5, end=11.5, text="灵溪她出事了", candidates=["灵溪她出事了"]),
            # The frame still shows the tail of the cue before the hole.
            ocr_service.WindowReading(start=4.2, end=5.0, text="你等我", candidates=["你等我"]),
        ],
    )

    outcome, added = stt_service._fill_from_ocr(
        *sources,
        _outcome(),
        request=TranscribeRequest(video_path="clip.mp4", ocr_fill=True),
        report=lambda **_kwargs: None,
        settings=Settings(ocr_language="auto"),
    )

    assert added == 1
    assert [segment.text for segment in outcome.segments] == ["你等我", "灵溪她出事了", "对不起"]
    # The audio result is still the base: the speech engine keeps the credit.
    assert outcome.provider == "local+ocr"
    assert outcome.language == "zh"


def test_a_missing_ocr_engine_leaves_the_audio_result_alone(
    sources: tuple[Path, Path], holes: list[tuple[float, float]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args, **_kwargs):
        raise ocr_service.OcrError("rapidocr is not installed")

    monkeypatch.setattr(ocr_service, "read_windows", boom)
    original = _outcome()

    outcome, added = stt_service._fill_from_ocr(
        *sources,
        original,
        request=TranscribeRequest(video_path="clip.mp4", ocr_fill=True),
        report=lambda **_kwargs: None,
        settings=Settings(),
    )

    assert (outcome, added) == (original, 0)


def test_no_hole_means_no_ocr_call(
    sources: tuple[Path, Path], holes: list[tuple[float, float]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subtitle_gaps, "unread_windows", lambda *_args, **_kwargs: [])
    calls: list[object] = []
    monkeypatch.setattr(ocr_service, "read_windows", lambda *_args, **_kwargs: calls.append(1) or [])

    outcome, added = stt_service._fill_from_ocr(
        *sources,
        _outcome(),
        request=TranscribeRequest(video_path="clip.mp4", ocr_fill=True),
        report=lambda **_kwargs: None,
        settings=Settings(),
    )

    assert added == 0
    assert calls == []
    assert [segment.text for segment in outcome.segments] == ["你等我", "对不起"]


def test_an_unreadable_wave_skips_the_pass(
    sources: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subtitle_gaps, "load_waveform", lambda _path: None)
    original = _outcome()

    outcome, added = stt_service._fill_from_ocr(
        *sources,
        original,
        request=TranscribeRequest(video_path="clip.mp4", ocr_fill=True),
        report=lambda **_kwargs: None,
        settings=Settings(),
    )

    assert (outcome, added) == (original, 0)


# ---------------------------------------------------------------------------
# Which recogniser reads the frames
# ---------------------------------------------------------------------------
def test_the_detected_language_picks_the_ocr_recogniser() -> None:
    settings = stt_service._ocr_settings(
        TranscribeRequest(video_path="clip.mp4"), _outcome(), Settings(ocr_language="auto")
    )

    assert settings.ocr_language == "zh"


def test_an_explicit_source_language_wins_over_the_detected_one() -> None:
    settings = stt_service._ocr_settings(
        TranscribeRequest(video_path="clip.mp4", language="vi"), _outcome(), Settings(ocr_language="auto")
    )

    assert settings.ocr_language == "vi"


def test_the_language_chosen_for_the_picture_wins_over_the_spoken_one() -> None:
    # A Chinese drama carries Vietnamese hardsubs: the Chinese model reads those
    # as gibberish, so the OCR language the user set has the final say.
    settings = stt_service._ocr_settings(
        TranscribeRequest(video_path="clip.mp4"), _outcome(), Settings(ocr_language="vi")
    )

    assert settings.ocr_language == "vi"


# ---------------------------------------------------------------------------
# One read can carry two scripts
# ---------------------------------------------------------------------------
def test_the_read_keeps_only_the_script_the_recogniser_reads() -> None:
    # What the frame actually held: a Vietnamese cue with a Chinese caption box
    # next to it, handed back by the reader as a single string.
    line = "你十年前说过的话 Quên rồi sao?"

    assert ocr_service._one_script(line, cjk=False) == "Quên rồi sao?"
    assert ocr_service._one_script(line, cjk=True) == "你十年前说过的话"


def test_a_wrapped_cue_in_one_script_survives_intact() -> None:
    assert ocr_service._one_script("đừng đi anh ơi em xin anh", cjk=False) == "đừng đi anh ơi em xin anh"
    # A recogniser set to Chinese still keeps a Latin read: one line beats none.
    assert ocr_service._one_script("đừng đi anh", cjk=True) == "đừng đi anh"
    assert ocr_service._one_script("我们走 别回头", cjk=True) == "我们走 别回头"
    assert ocr_service._one_script("我们走", cjk=False) == "我们走"


def test_a_read_with_no_letters_is_kept_as_it_is() -> None:
    assert ocr_service._one_script("2019 ?", cjk=False) == "2019 ?"
    assert ocr_service._one_script("", cjk=False) == ""


def test_the_script_comes_from_the_language_the_reader_was_built_for() -> None:
    assert ocr_service._wanted_cjk("zh") is True
    assert ocr_service._wanted_cjk("ja") is True
    assert ocr_service._wanted_cjk("vi") is False
    # "auto" is the Chinese pair, so a mixed read keeps its CJK half.
    assert ocr_service._wanted_cjk("auto") is True
