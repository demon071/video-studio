"""Whisper options, the speech windows and the VAD fallback.

faster-whisper's VAD does not only skip silence: it *concatenates* the speech
islands and drops the pauses between them. Given that spliced audio the model
emits an early end-of-text, so a 29 s window that really held six lines came
back as one and a two minute opening lost every line in it. The windows are
therefore planned here and the library VAD is never asked to run, which is what
these tests pin.
"""

from __future__ import annotations

import pytest

from app.services import stt_service

TEN_SECONDS = 16_000 * 10


class _Info:
    language = "zh"
    language_probability = 0.98
    duration = 4.0


class _Word:
    def __init__(self, start: float, end: float, word: str) -> None:
        self.start = start
        self.end = end
        self.word = word


class _Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.words = [_Word(start, end, text)]
        self.no_speech_prob = 0.02


class _Model:
    """Minimal stand-in for faster_whisper.WhisperModel."""

    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.calls: list[dict] = []

    def transcribe(self, audio, *, language=None, **options):  # noqa: ANN001, ARG002
        self.calls.append({"options": options, "language": language, "samples": len(audio)})
        if self.failure:
            raise RuntimeError(self.failure)

        def segments():
            yield _Segment(0.0, 1.0, "你好")

        return segments(), _Info()


def test_transcribe_iter_returns_an_iterator_and_info() -> None:
    model = _Model()
    iterator, info = stt_service._transcribe_iter(model, [0.0] * TEN_SECONDS, language="zh", vad=False)

    assert info.language == "zh"
    assert info.duration == 10.0  # the whole clip, not the window
    assert [line.text for line in iterator] == ["你好"]


def test_transcribe_iter_sends_the_accuracy_options() -> None:
    model = _Model()
    stt_service._transcribe_iter(model, [0.0] * TEN_SECONDS, language=None, vad=False)
    options = model.calls[0]["options"]

    assert options["word_timestamps"] is True
    assert options["beam_size"] == 5
    assert options["condition_on_previous_text"] is False
    # The library VAD would splice the audio, so it is never allowed to run.
    assert options["vad_filter"] is False
    assert "vad_parameters" not in options


def test_windows_cover_the_gap_where_vad_found_no_speech() -> None:
    """Silero misses dialogue over music; nothing may fall between windows."""
    islands = [(2.0, 6.0), (7.0, 12.0), (40.0, 44.0)]

    assert stt_service._plan_windows(islands, 60.0) == [(0.0, 25.0), (25.0, 40.0), (40.0, 60.0)]


def test_windows_never_grow_past_one_whisper_window() -> None:
    islands = [(index * 5.0, index * 5.0 + 4.0) for index in range(10)]
    windows = stt_service._plan_windows(islands, 50.0)

    assert windows[0][0] == 0.0
    assert windows[-1][1] == 50.0
    assert all(right - left <= stt_service.WINDOW_SECONDS for left, right in windows)


def test_every_second_of_the_clip_belongs_to_a_window() -> None:
    windows = stt_service._plan_windows([(3.0, 5.0), (90.0, 92.0)], 100.0)

    assert windows[0][0] == 0.0
    assert windows[-1][1] == 100.0
    for (_, end), (start, _) in zip(windows, windows[1:], strict=False):
        assert end == start  # contiguous, no hole


def test_no_speech_islands_still_decode_the_whole_clip() -> None:
    assert stt_service._plan_windows([], 12.5) == [(0.0, 12.5)]
    assert stt_service._plan_windows([], 60.0) == [(0.0, 25.0), (25.0, 50.0), (50.0, 60.0)]


def test_a_broken_vad_decodes_the_whole_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("Failed to load onnxruntime library silero_vad.onnx")

    monkeypatch.setattr("faster_whisper.vad.get_speech_timestamps", boom)
    model = _Model()
    iterator, info = stt_service._transcribe_iter(model, [0.0] * TEN_SECONDS, language=None, vad=True)

    assert info.duration == 10.0
    assert [call["samples"] for call in model.calls] == [TEN_SECONDS]
    assert [line.text for line in iterator] == ["你好"]


def test_each_window_is_decoded_alone_and_shifted_back_in_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stamps = [
        {"start": 16_000 * 2, "end": 16_000 * 6},
        {"start": 16_000 * 40, "end": 16_000 * 44},
    ]
    monkeypatch.setattr("faster_whisper.vad.get_speech_timestamps", lambda *_a, **_k: stamps)
    model = _Model()
    iterator, _ = stt_service._transcribe_iter(model, [0.0] * (16_000 * 50), language="zh", vad=True)

    lines = list(iterator)

    # Each window is a plain excerpt of the file: the wave is never spliced.
    assert [call["samples"] for call in model.calls] == [25 * 16_000, 15 * 16_000, 10 * 16_000]
    assert [(line.start, line.end) for line in lines] == [(0.0, 1.0), (25.0, 26.0), (40.0, 41.0)]
    assert all(line.words for line in lines)  # word timings survive the shift


def test_a_decode_error_surfaces_immediately() -> None:
    model = _Model(failure="audio decode failed")

    with pytest.raises(RuntimeError, match="audio decode failed"):
        stt_service._transcribe_iter(model, [0.0] * TEN_SECONDS, language=None, vad=False)
    assert len(model.calls) == 1  # no pointless second pass
