"""SenseVoice: the token splitter and the guards around the provider.

The model itself needs a 240 MB download, so these tests pin the part that can go
wrong quietly - where one window's token stream is cut into subtitle lines, and
what the user is told when the engine cannot run - not the weights.
"""

from __future__ import annotations

import pytest

from app.schemas.subtitle import TranscribeRequest
from app.services import sensevoice_stt, stt_service


def test_a_full_stop_ends_the_line() -> None:
    tokens = ["你", "等", "我", "。", "对", "不", "起", "？"]
    times = [0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1]

    lines = sensevoice_stt.split_tokens(tokens, times, 0.0, 2.1)

    # A line ends on its last *word*, not on the mark that follows it: the engine
    # times a closing "。" wherever the utterance ends, which can be seconds later.
    assert [(round(left, 2), round(right, 2), text) for left, right, text in lines] == [
        (0.0, 0.72, "你等我。"),
        (1.2, 1.92, "对不起？"),
    ]


def test_a_closing_mark_never_stretches_the_line_that_holds_it() -> None:
    # Straight from the episode: "交换戒指" 5.2 s, then "。" at 14.5 s.
    tokens = ["交", "换", "戒", "指", "。"]
    times = [5.22, 5.4, 5.82, 5.94, 14.52]

    assert sensevoice_stt.split_tokens(tokens, times, 0.0, 14.58) == [(5.22, 6.06, "交换戒指。")]


def test_a_mark_the_pause_pushed_off_its_line_closes_the_line_before() -> None:
    # From the episode: the 0.84 s pause cuts "艺我们就能结婚了", then "。" arrives.
    tokens = ["艺", "我", "们", "就", "能", "结", "婚", "了", "。", "结", "果"]
    times = [0.3, 0.48, 0.6, 0.72, 0.84, 1.02, 1.2, 1.44, 1.68, 2.28, 2.52]

    lines = sensevoice_stt.split_tokens(tokens, times, 0.0, 30.0)

    assert [(round(left, 2), round(right, 2), text) for left, right, text in lines] == [
        (0.3, 1.56, "艺我们就能结婚了。"),
        (2.28, 2.64, "结果"),
    ]


def test_a_comma_ends_a_clause_once_it_has_three_words() -> None:
    tokens = ["我", "们", "走", ",", "吧"]
    times = [0.0, 0.3, 0.6, 0.9, 1.2]

    lines = sensevoice_stt.split_tokens(tokens, times, 0.0, 1.2)

    assert [(round(left, 2), round(right, 2), text) for left, right, text in lines] == [
        (0.0, 0.72, "我们走,"),
        (1.2, 1.32, "吧"),
    ]


def test_a_short_clause_is_not_cut_on_a_comma() -> None:
    tokens = ["走", ",", "吧"]
    times = [0.0, 0.2, 0.4]

    assert [text for _left, _right, text in sensevoice_stt.split_tokens(tokens, times, 0.0, 0.4)] == [
        "走,吧"
    ]


def test_a_long_silence_ends_a_line_even_without_punctuation() -> None:
    tokens = ["好", "的", "吧"]
    times = [0.0, 0.3, 1.2]  # a 0.9 s pause between 的 and 吧

    assert [text for _left, _right, text in sensevoice_stt.split_tokens(tokens, times, 0.0, 1.2)] == [
        "好的",
        "吧",
    ]


def test_a_lone_interjection_joins_the_line_it_introduces() -> None:
    tokens = ["啊", "。", "好", "。"]
    times = [0.0, 0.1, 0.2, 0.3]

    assert [text for _left, _right, text in sensevoice_stt.split_tokens(tokens, times, 0.0, 0.3)] == [
        "啊。好。"
    ]


def test_the_model_s_own_tags_never_reach_a_subtitle() -> None:
    tokens = ["<|zh|>", "<|NEUTRAL|>", "你", "好", "。"]

    lines = sensevoice_stt.split_tokens(tokens, [0.0, 0.0, 0.1, 0.2, 0.3], 0.0, 0.3)

    assert [text for _left, _right, text in lines] == ["你好。"]


def test_a_window_without_tokens_produces_nothing() -> None:
    assert sensevoice_stt.split_tokens([], [], 0.0, 10.0) == []
    assert sensevoice_stt.split_tokens(["你"], [], 0.0, 10.0) == []


def test_a_line_never_runs_past_the_end_of_its_window() -> None:
    # The +0.12 s a line is given for readability stops at the window's edge.
    lines = sensevoice_stt.split_tokens(["好"], [9.95], 0.0, 10.0)

    assert lines == [(9.95, 10.07, "好")]


def test_two_lines_never_overlap() -> None:
    tokens = ["好", "的", "。", "走"]
    times = [0.0, 0.03, 0.05, 0.08]  # the mark lands after the next word started

    lines = sensevoice_stt.split_tokens(tokens, times, 0.0, 10.0)

    assert all(right > left for left, right, _text in lines)
    assert lines[0][1] <= lines[1][0]


def test_the_language_codes_are_normalised() -> None:
    assert sensevoice_stt.language_code("zh-CN") == "zh"
    assert sensevoice_stt.language_code("jp") == "ja"
    assert sensevoice_stt.language_code("kr") == "ko"
    assert sensevoice_stt.language_code("auto") == "auto"
    assert sensevoice_stt.language_code(None) == ""


def test_a_language_the_checkpoint_cannot_read_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sensevoice_stt, "available", lambda: True)
    request = TranscribeRequest(video_path="clip.mp4", provider="sensevoice", language="vi")

    with pytest.raises(stt_service.SttError, match="SenseVoice only reads"):
        stt_service._transcribe_sensevoice(
            request=request, wav=None, report=lambda **_kwargs: None, settings=None
        )


def test_a_missing_package_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sensevoice_stt, "available", lambda: False)
    request = TranscribeRequest(video_path="clip.mp4", provider="sensevoice", language="zh")

    with pytest.raises(stt_service.SttError, match="pip install sherpa-onnx"):
        stt_service._transcribe_sensevoice(
            request=request, wav=None, report=lambda **_kwargs: None, settings=None
        )


def test_the_panel_can_pick_the_engine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"0")
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"0")
    seen: dict[str, object] = {}

    def fake_transcribe(wav_path, request, *, report, settings):  # noqa: ANN001, ARG001
        seen["wav"] = wav_path
        return stt_service.SttOutcome(
            language="zh",
            duration=1.0,
            segments=[stt_service.SubtitleSegment(index=1, start=0.0, end=1.0, text="你好")],
            provider="sensevoice",
            model="int8",
            device="cpu",
        )

    monkeypatch.setattr(stt_service, "_cached_wav", lambda *_args, **_kwargs: wav)
    monkeypatch.setattr(stt_service, "_transcribe_sensevoice", fake_transcribe)
    result = stt_service.transcribe(
        TranscribeRequest(video_path=str(source), provider="sensevoice", ocr_fill=False)
    )

    assert result.provider == "sensevoice"
    assert result.segments[0].text == "你好"
    assert seen["wav"] == wav
