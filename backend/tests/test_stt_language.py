"""Which language the clip is decoded in.

Whisper decides the language from the first 30 s of the file. On these episodes
that stretch is the music intro: "002 - Tập 002" came back as *ru* at 0.45 and
all 53 lines were written in Russian - the whole transcript, not just a line.
The vote over several speech slices below is what keeps that from happening.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services import stt_service

TEN_SECONDS = 16_000 * 10


def _wave(seconds: float = 10.0) -> np.ndarray:
    return np.zeros(int(16_000 * seconds), dtype=np.float32)


class _Info:
    def __init__(self, language: str | None = None, probability: float = 0.45) -> None:
        self.language = language
        self.language_probability = probability
        self.duration = 4.0


class _Segment:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.words = []
        self.no_speech_prob = 0.02


class _Model:
    """A stand-in for WhisperModel that answers ru until it is asked to vote."""

    def __init__(self, *, vote: str = "zh", probability: float = 0.97, legacy: bool = False) -> None:
        self.vote = vote
        self.probability = probability
        self.legacy = legacy
        self.detect_calls: list[dict] = []
        self.transcribe_calls: list[str | None] = []

    def detect_language(self, audio, **options):  # noqa: ANN001, ANN003, ARG002
        self.detect_calls.append(options)
        if self.legacy and options:
            raise TypeError("detect_language() got an unexpected keyword argument")
        return self.vote, self.probability, []

    def transcribe(self, audio, *, language=None, **options):  # noqa: ANN001, ARG002
        self.transcribe_calls.append(language)

        def segments():
            yield _Segment(0.0, 1.0, "你好")

        return segments(), _Info(language or "ru", 0.45)


def test_the_vote_reads_several_slices_of_speech_with_a_higher_bar() -> None:
    model = _Model()
    iterator, info = stt_service._transcribe_iter(model, _wave(), language=None, vad=False)
    list(iterator)

    assert len(model.detect_calls) == 1
    options = model.detect_calls[0]
    assert options["vad_filter"] is True  # the music intro is not asked
    assert options["language_detection_segments"] == stt_service.DETECT_SEGMENTS > 1
    assert options["language_detection_threshold"] == stt_service.DETECT_THRESHOLD > 0.5
    # The voted language, not the first window's guess, decodes the clip.
    assert info.language == "zh"
    assert model.transcribe_calls == ["zh"]


def test_one_answer_decodes_every_window(monkeypatch: pytest.MonkeyPatch) -> None:
    stamps = [
        {"start": 16_000 * 2, "end": 16_000 * 6},
        {"start": 16_000 * 40, "end": 16_000 * 44},
    ]
    monkeypatch.setattr("faster_whisper.vad.get_speech_timestamps", lambda *_a, **_k: stamps)
    model = _Model()
    iterator, _info = stt_service._transcribe_iter(model, _wave(60.0), language=None, vad=True)
    list(iterator)

    assert len(model.transcribe_calls) == 3  # 25 s + 15 s + 20 s
    assert model.transcribe_calls == ["zh", "zh", "zh"]


def test_the_probability_reported_is_the_vote_s() -> None:
    model = _Model(probability=0.96)
    _iterator, info = stt_service._transcribe_iter(model, _wave(), language=None, vad=False)

    assert info.language_probability == pytest.approx(0.96)  # not the window's 0.45


def test_a_language_the_user_picked_is_never_voted_on() -> None:
    model = _Model()
    _iterator, info = stt_service._transcribe_iter(model, _wave(), language="vi", vad=False)

    assert model.detect_calls == []
    assert model.transcribe_calls == ["vi"]
    assert info.language == "vi"


def test_an_old_wheel_is_asked_without_the_vote_options() -> None:
    model = _Model(legacy=True)
    _iterator, info = stt_service._transcribe_iter(model, _wave(), language=None, vad=False)

    assert len(model.detect_calls) == 2  # the vote first, then the plain call
    assert "vad_filter" in model.detect_calls[0]
    assert model.detect_calls[-1] == {}
    assert info.language == "zh"


def test_a_failing_detection_leaves_the_first_window_to_decide() -> None:
    class _Broken(_Model):
        def detect_language(self, audio, **options):  # noqa: ANN001, ANN003, ARG002
            raise RuntimeError("Failed to load onnxruntime library silero_vad.onnx")

    model = _Broken()
    iterator, info = stt_service._transcribe_iter(model, _wave(), language=None, vad=False)

    assert [line.text for line in iterator] == ["你好"]  # nothing raised
    assert model.transcribe_calls == [None]  # as before the vote existed
    assert info.language == "ru"


def test_a_waveform_that_is_really_a_path_is_not_voted_on() -> None:
    # _waveform hands the file over when soundfile cannot read it; detection
    # wants samples, so it is skipped instead of crashing on a str.
    model = _Model()
    _iterator, info = stt_service._transcribe_iter(model, "clip.wav", language=None, vad=False)

    assert model.detect_calls == []
    assert info.language == "ru"


def test_no_language_leaves_the_transcript_unlabelled() -> None:
    class _Silent(_Model):
        def transcribe(self, audio, *, language=None, **options):  # noqa: ANN001, ARG002
            self.transcribe_calls.append(language)

            def segments():
                return iter(())

            return segments(), _Info(None, 0.0)

    model = _Silent()
    _iterator, info = stt_service._transcribe_iter(model, _wave(), language=None, vad=False)

    assert model.transcribe_calls == ["zh"]
    assert info.language == "zh"  # the vote still stands
