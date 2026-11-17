"""Translation prompt, answer parsing and failure behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.schemas.subtitle import SubtitleSegment
from app.services import translate_service


def _settings(tmp_path: Path) -> Settings:
    return Settings(models_dir=str(tmp_path / "models"))


def test_translate_rejects_unsupported_provider(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(translate_service.TranslateError, match="Unsupported translation provider"):
        translate_service.translate(
            [SubtitleSegment(index=1, start=0.0, end=1.0, text="xin chào")],
            provider="local",
            settings=settings,
        )


def test_prompts_name_the_target_language() -> None:
    system = translate_service._system_prompt("vi")
    user = translate_service._user_prompt(
        [SubtitleSegment(index=1, start=0.0, end=1.0, text="hello")], "en", "vi", "natural"
    )

    assert "Vietnamese" in system  # not the bare code "vi" a 3B model may misread
    assert "{target}" not in system
    assert "Vietnamese (vi)" in user


def test_language_codes_resolve_to_names() -> None:
    assert translate_service._language_name("pt-BR") == "Portuguese"
    assert translate_service._language_name("auto") == "the source language"
    assert translate_service._language_name("xx") == "xx"


def test_the_prompt_carries_no_sample_translation() -> None:
    """The worked example came back as a real line: "郭先生" -> its sentence."""
    chunk = [SubtitleSegment(index=1, start=0.0, end=1.0, text="这朵花放在这里不会")]
    prompt = translate_service._user_prompt(chunk, "zh", "vi", "natural")

    assert "Xin chào" not in prompt  # nothing the model can parrot
    assert "你好" not in prompt
    assert '"i": 1' in prompt


def test_split_escalation_halves_a_refusing_batch(tmp_path: Path) -> None:
    chunk = [
        SubtitleSegment(index=index, start=float(index), end=float(index) + 1, text=text)
        for index, text in ((1, "这朵花"), (2, "一起换"), (3, "那又怎样"), (4, "拜託"))
    ]
    seen: list[int] = []

    def stubborn_ask(prompt: str, _settings: Settings, _system: str) -> str:
        payload = next(line for line in reversed(prompt.splitlines()) if line.startswith("[{"))
        size = len(json.loads(payload))
        seen.append(size)
        # Copying the source is what a real 3B model does on this input.
        if size > 2:
            return '{"lines": [{"i": 1, "t": "这朵花"}]}'
        return '{"lines": [{"i": 1, "t": "Đây là hoa"}]}'

    mapping = translate_service._translate_chunk(
        chunk,
        ask=stubborn_ask,
        settings=_settings(tmp_path),
        system="",
        source_language="zh",
        target_language="vi",
        style="natural",
    )

    assert seen[0] == 4  # the whole batch is tried first
    assert 2 in seen  # then it is halved
    assert mapping  # small batches produced Vietnamese and were accepted


def test_parse_lines_accepts_the_shapes_a_small_model_returns() -> None:
    wrapped = '{"lines": [{"i": 1, "t": "Xin chào"}]}'
    bare = '{"i": 1, "t": "Xin chào"}'
    mapped = '{"1": "Xin chào"}'
    array = '[{"i": 1, "t": "Xin chào"}]'

    assert translate_service._parse_lines(wrapped) == {1: "Xin chào"}
    assert translate_service._parse_lines(bare) == {1: "Xin chào"}
    assert translate_service._parse_lines(mapped) == {1: "Xin chào"}
    assert translate_service._parse_lines(array) == {1: "Xin chào"}


def test_parse_lines_rejects_an_empty_answer() -> None:
    with pytest.raises(translate_service.TranslateError, match="no lines"):
        translate_service._parse_lines('{"lines": []}')


def test_residue_flags_a_chinese_answer_and_a_mixed_line() -> None:
    # The whole batch came back in Chinese...
    assert translate_service._residue_lines({1: "看下集哦"}, "vi") == [1]
    # ...and the far more common case: one line, 90% Vietnamese.
    mixed = {2: "Các khoản này是真的 hay giả", 3: "Anh đang nói gì?"}
    assert translate_service._residue_lines(mixed, "vi") == [2]
    # Chinese as the target is the point, not residue.
    assert translate_service._residue_lines({1: "看下集哦"}, "zh") == []


def test_a_line_that_keeps_chinese_characters_is_asked_again(tmp_path: Path) -> None:
    """Mixed lines passed every ratio test and shipped to the user as-is."""
    chunk = [SubtitleSegment(index=1, start=0.0, end=2.0, text="这些账目是真的还是假的")]
    answers = iter(
        [
            '{"lines": [{"i": 1, "t": "Các khoản này是真的 hay giả"}]}',
            '{"lines": [{"i": 1, "t": "Các khoản này thật hay giả?"}]}',
        ]
    )
    prompts: list[str] = []

    def leaky_ask(prompt: str, _settings: Settings, _system: str) -> str:
        prompts.append(prompt)
        return next(answers)

    mapping = translate_service._translate_chunk(
        chunk,
        ask=leaky_ask,
        settings=_settings(tmp_path),
        system="",
        source_language="zh",
        target_language="vi",
        style="natural",
    )

    assert len(prompts) == 2  # the residue answer was not accepted
    assert mapping == {1: "Các khoản này thật hay giả?"}


def test_repeated_translations_are_flagged() -> None:
    chunk = [
        SubtitleSegment(index=index, start=float(index), end=float(index) + 1, text=text)
        for index, text in ((1, "你在说什么"), (2, "我冲的就是你"), (3, "这些账目是真的还是假的"))
    ]
    lost = "Anh ta muốn gì mà anh ta muốn?"
    same = {1: lost, 2: lost, 3: lost}

    # Three ids answered with one line: that is the model losing the thread.
    assert translate_service._repeated_lines(same, chunk) == [1, 2, 3]


def test_short_interjections_may_honestly_repeat() -> None:
    chunk = [
        SubtitleSegment(index=index, start=float(index), end=float(index) + 1, text=text)
        for index, text in ((1, "好"), (2, "行"), (3, "可以"))
    ]
    mapping = {1: "Được", 2: "Được", 3: "Được"}

    assert translate_service._repeated_lines(mapping, chunk) == []


def test_a_line_that_honestly_repeats_is_not_flagged() -> None:
    chunk = [
        SubtitleSegment(index=1, start=0.0, end=1.0, text="好的"),
        SubtitleSegment(index=2, start=1.0, end=2.0, text="好的"),
        SubtitleSegment(index=3, start=2.0, end=3.0, text="没问题"),
    ]
    mapping = {1: "Được thôi", 2: "Được thôi", 3: "Không thành vấn đề"}

    assert translate_service._repeated_lines(mapping, chunk) == []


def test_two_ids_sharing_one_answer_are_both_flagged() -> None:
    """The quiet failure: two lines merged, everything after them slid by one."""
    chunk = [
        SubtitleSegment(index=index, start=float(index), end=float(index) + 1, text=text)
        for index, text in ((1, "无论什么原因离婚"), (2, "或是所有财产将归女方所有"))
    ]
    slid = {1: "Bất kể lý do gì ly hôn", 2: "Bất kể lý do gì ly hôn"}

    assert translate_service._repeated_lines(slid, chunk) == [1, 2]


def test_a_mixed_line_is_salvaged_rather_than_shown_in_chinese(tmp_path: Path) -> None:
    """Three refusals, but the answer was 90% usable Vietnamese."""
    chunk = [SubtitleSegment(index=1, start=0.0, end=2.0, text="这些账目是真的还是假的")]
    mixed = '{"lines": [{"i": 1, "t": "Đây là số chi phí是真的 hay giả?"}]}'

    def stubborn_ask(_prompt: str, _settings: Settings, _system: str) -> str:
        return mixed

    mapping = translate_service._translate_chunk(
        chunk,
        ask=stubborn_ask,
        settings=_settings(tmp_path),
        system="",
        source_language="zh",
        target_language="vi",
        style="natural",
    )

    assert mapping == {1: "Đây là số chi phí hay giả?"}


def test_a_line_left_entirely_in_chinese_is_not_invented(tmp_path: Path) -> None:
    """Stripping every character would leave nothing; an empty line is honest."""
    chunk = [SubtitleSegment(index=1, start=0.0, end=2.0, text="苏婉月不走")]
    copied = '{"lines": [{"i": 1, "t": "苏婉月不走"}]}'

    def stubborn_ask(_prompt: str, _settings: Settings, _system: str) -> str:
        return copied

    mapping = translate_service._translate_chunk(
        chunk,
        ask=stubborn_ask,
        settings=_settings(tmp_path),
        system="",
        source_language="zh",
        target_language="vi",
        style="natural",
    )

    assert mapping == {}


def test_only_the_failing_line_is_asked_again(tmp_path: Path) -> None:
    """Halving threw the whole batch away; a repair keeps the good eight."""
    chunk = [
        SubtitleSegment(index=index, start=float(index), end=float(index) + 1, text=text)
        for index, text in ((1, "你好"), (2, "走吧"), (3, "这些账目是真的还是假的"), (4, "再见"))
    ]
    answers = iter(
        [
            '{"lines": [{"i": 1, "t": "Xin chào"}, {"i": 2, "t": "Đi thôi"}, '
            '{"i": 3, "t": "Sổ sách này是真的 hay giả"}, {"i": 4, "t": "Tạm biệt"}]}',
            '{"lines": [{"i": 3, "t": "Sổ sách này thật hay giả?"}]}',
        ]
    )
    prompts: list[str] = []

    def leaky_ask(prompt: str, _settings: Settings, _system: str) -> str:
        prompts.append(prompt)
        return next(answers)

    mapping = translate_service._translate_chunk(
        chunk,
        ask=leaky_ask,
        settings=_settings(tmp_path),
        system="",
        source_language="zh",
        target_language="vi",
        style="natural",
    )

    assert len(prompts) == 2
    assert mapping == {
        1: "Xin chào",
        2: "Đi thôi",
        3: "Sổ sách này thật hay giả?",
        4: "Tạm biệt",
    }
    # The re-ask carries the one bad line, not the whole batch again.
    payload = json.loads(prompts[1].splitlines()[-1])
    assert [entry["i"] for entry in payload] == [3]


def test_parse_lines_reads_a_numbered_answer() -> None:
    raw = "1. Xin chào\n2) Đi thôi\n3: Nhanh lên"
    assert translate_service._parse_lines(raw) == {
        1: "Xin chào",
        2: "Đi thôi",
        3: "Nhanh lên",
    }


def test_parse_lines_still_rejects_prose_without_lines() -> None:
    with pytest.raises(translate_service.TranslateError, match="no lines"):
        translate_service._parse_lines("I cannot help with that request.")


def test_alignment_keeps_ids_when_they_match() -> None:
    chunk = [
        SubtitleSegment(index=index, start=float(index), end=float(index) + 1, text="x")
        for index in (7, 8)
    ]
    assert translate_service._align_mapping({7: "a", 8: "b"}, chunk) == {7: "a", 8: "b"}


def test_alignment_rekeys_a_model_that_numbers_from_one() -> None:
    chunk = [
        SubtitleSegment(index=index, start=float(index), end=float(index) + 1, text="x")
        for index in (7, 8)
    ]
    assert translate_service._align_mapping({1: "a", 2: "b"}, chunk) == {7: "a", 8: "b"}


def test_alignment_leaves_a_partial_answer_partial() -> None:
    chunk = [
        SubtitleSegment(index=index, start=float(index), end=float(index) + 1, text="x")
        for index in (1, 2, 3)
    ]
    # Dropping the missing line would ship a half-translated video.
    assert translate_service._align_mapping({1: "a"}, chunk) == {1: "a"}


def test_copied_source_is_flagged_for_latin_sources() -> None:
    chunk = [
        SubtitleSegment(index=1, start=0.0, end=1.0, text="I will kill you"),
        SubtitleSegment(index=2, start=1.0, end=2.0, text="You are dead"),
    ]
    copied = {1: "I will kill you", 2: "Mày chết rồi"}

    # Only the refused line, so the other one is not thrown away with it.
    assert translate_service._copied_lines(copied, chunk, "en", "vi") == [1]
    assert translate_service._copied_lines({1: "Tôi sẽ giết mày", 2: "Mày chết rồi"}, chunk, "en", "vi") == []


def test_a_short_name_may_legitimately_stay_unchanged() -> None:
    chunk = [SubtitleSegment(index=1, start=0.0, end=1.0, text="Nina")]
    assert translate_service._copied_lines({1: "Nina"}, chunk, "en", "vi") == []


def test_copied_source_ignores_a_source_already_in_the_target_language() -> None:
    chunk = [SubtitleSegment(index=1, start=0.0, end=1.0, text="xin chào các bạn")]
    assert translate_service._copied_lines({1: "xin chào các bạn"}, chunk, "vi", "vi") == []


def test_vietnamese_style_rules_carry_address_forms_and_the_genre() -> None:
    system = translate_service._system_prompt("vi", "zh", "short drama for tiktok")
    assert "xưng hô" in system
    assert "Hán-Việt" in system
    assert "hào môn" in system  # the drama preset is selected by the style hint
    assert "{target}" not in system


def test_non_vietnamese_targets_skip_the_vietnamese_rules() -> None:
    system = translate_service._system_prompt("en", "zh", "documentary")
    assert "xưng hô" not in system
    assert "documentary" in system.lower()


def test_the_prompt_carries_no_previous_batch() -> None:
    """History in the prompt made the model answer with the *previous* answers."""
    prompt = translate_service._user_prompt(
        [SubtitleSegment(index=7, start=0.0, end=2.0, text="还请留步")], "zh", "vi", "natural"
    )
    assert "Previous lines" not in prompt
    payload = json.loads(prompt.splitlines()[-1])
    assert [entry["i"] for entry in payload] == [7]


def test_user_prompt_carries_the_on_screen_budget() -> None:
    prompt = translate_service._user_prompt(
        [SubtitleSegment(index=1, start=0.0, end=2.5, text="hello")], "en", "vi", "natural"
    )
    assert '"d": 2.5' in prompt
    assert "15 characters per second" in translate_service.SYSTEM_PROMPT


def test_the_recommended_free_tier_models_keep_their_tuning() -> None:
    lite = translate_service.gemini_profile("gemini-3.1-flash-lite")
    assert 35 <= lite.lines <= 45  # 35-45 lines per request
    assert lite.thinking == "MINIMAL"
    assert lite.pause == 4.0

    hydrogen = translate_service.gemini_profile("gemini-4-hydrogen")
    assert 25 <= hydrogen.lines <= 30  # 25-30 lines per request
    assert hydrogen.thinking == "LOW"
    assert 2.0 <= hydrogen.pause <= 4.0


def test_a_dated_or_prefixed_model_id_still_finds_its_profile() -> None:
    lite = translate_service.GEMINI_PROFILES["gemini-3.1-flash-lite"]
    assert translate_service.gemini_profile("gemini-3.1-flash-lite-001") is lite
    assert translate_service.gemini_profile("models/gemini-3.1-flash-lite") is lite


def test_an_unlisted_model_keeps_a_safe_default() -> None:
    default = translate_service.gemini_profile("gemini-2.5-flash")
    # 2.5 models take a thinking *budget*, not a level: sending one would fail.
    assert default.thinking is None
    assert default.lines < translate_service.GEMINI_PROFILES["gemini-3.1-flash-lite"].lines


def test_the_thinking_level_reaches_the_sdk_enum() -> None:
    types = pytest.importorskip("google.genai.types")

    lite = translate_service._gemini_thinking(types, translate_service.gemini_profile("gemini-3.1-flash-lite"))
    assert lite is not None
    assert lite.thinking_level.value == "MINIMAL"

    # An unlisted model must not be sent a level it does not understand.
    assert translate_service._gemini_thinking(types, translate_service.gemini_profile("gemini-2.5-flash")) is None


def test_chunking_follows_the_requested_batch_size() -> None:
    segments = [SubtitleSegment(index=i, start=0.0, end=1.0, text="你好") for i in range(1, 41)]

    assert len(translate_service._chunk(segments)) == 8  # the 3B model keeps 5 lines
    assert len(translate_service._chunk(segments, max_lines=40, max_chars=4500)) == 1


def test_gemini_translation_groups_and_paces_by_the_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One request carries 40 lines, and the next one waits 4 s, not 0."""
    settings = Settings(
        models_dir=str(tmp_path / "models"),
        data_dir=str(tmp_path / "data"),
        gemini_model="gemini-3.1-flash-lite",
    )
    sizes: list[int] = []
    slept: list[float] = []
    clock = {"now": 1000.0}

    def fake_ask(prompt: str, _settings: Settings, _system: str) -> str:
        payload = json.loads(prompt.splitlines()[-1])
        sizes.append(len(payload))
        return json.dumps({"lines": [{"i": entry["i"], "t": f"Câu {entry['i']}"} for entry in payload]})

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(translate_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(translate_service.time, "sleep", fake_sleep)
    monkeypatch.setitem(translate_service._ASKERS, "gemini", fake_ask)

    segments = [SubtitleSegment(index=i, start=0.0, end=1.0, text="你好") for i in range(1, 61)]
    outcome = translate_service.translate(
        segments,
        provider="gemini",
        source_language="zh",
        target_language="vi",
        settings=settings,
    )

    assert sizes == [40, 20]  # not twelve five-line batches
    assert sum(slept) == pytest.approx(4.0)  # the quiet gap between the two calls
    assert len(outcome.segments) == 60
    assert outcome.model == "gemini-3.1-flash-lite"


def test_a_cancel_during_the_pause_stops_before_the_next_request() -> None:
    requests: list[str] = []
    calls = {"n": 0}

    def report(**_: object) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # the checkpoint that opens the wait
            raise translate_service.Cancelled("Job cancelled by user")

    pacer = translate_service._Pacer(4.0, report)
    pacer.mark()
    paced = translate_service._paced(lambda prompt, _s, _sys: requests.append(prompt) or "", pacer)

    with pytest.raises(translate_service.Cancelled):
        paced("second line", Settings(), "")
    assert requests == []
