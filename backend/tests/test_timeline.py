"""Subtitle cleaning: noise, loops, long cues and the clock.

These assertions encode the difference between "Whisper heard the words" and
"a viewer can read this", which is what the STT pass used to get wrong.
"""

from __future__ import annotations

from app.services.subtitle_timeline import Cue, polish


def test_bracketed_noise_is_dropped() -> None:
    cues = [Cue(0.0, 1.0, "[music]"), Cue(1.0, 1.2, "*sigh*"), Cue(1.2, 2.4, "Xin chào")]
    assert [cue.text for cue in polish(cues)] == ["Xin chào"]


def test_boilerplate_is_kept_unless_whisper_says_it_was_silence() -> None:
    invented = Cue(10.0, 12.0, "Thanks for watching!", no_speech_prob=0.92)
    spoken = Cue(10.0, 12.0, "Thanks for watching!", no_speech_prob=0.04)

    assert polish([invented]) == []
    # A real sign-off at the end of a vlog must survive.
    assert [cue.text for cue in polish([spoken])] == ["Thanks for watching!"]


def test_the_subscribe_ghost_is_dropped_even_when_whisper_is_confident() -> None:
    """Over end-card music the model invents a sign-off with no_speech_prob 0.12."""
    ghost = Cue(200.0, 225.0, "請不吝點贊訂閱轉發打賞支持明鏡與點點欄目", no_speech_prob=0.12)

    assert polish([ghost]) == []


def test_a_loop_over_music_collapses() -> None:
    cues = [Cue(index * 2.0, index * 2.0 + 1.0, "cảm ơn") for index in range(6)]
    result = polish(cues)
    assert len(result) == 2  # the signature is reported, not repeated six times


def test_fragments_split_on_a_breath_are_glued() -> None:
    cues = [Cue(0.0, 0.8, "Đây là"), Cue(0.9, 1.6, "hoa hồng")]
    result = polish(cues)

    assert len(result) == 1
    assert result[0].text == "Đây là hoa hồng"
    assert result[0].end == 1.6


def test_a_finished_sentence_is_never_glued_to_the_next() -> None:
    cues = [Cue(0.0, 1.0, "Đi thôi."), Cue(1.05, 2.0, "Nhanh lên")]
    assert len(polish(cues)) == 2


def test_a_long_line_is_split_on_word_boundaries() -> None:
    tokens = ["một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín", "mười"]
    words = [(index * 0.3, index * 0.3 + 0.3, token) for index, token in enumerate(tokens)]
    cue = Cue(0.0, 3.0, " ".join(tokens), words=words)

    result = polish([cue], max_chars=24)

    assert len(result) == 2
    assert all(len(piece.text) <= 24 for piece in result)
    # Equal-weight cut: "một hai ba bốn năm sáu" / "bảy tám chín mười", not an
    # orphan word on the second cue.
    assert result[0].text == "một hai ba bốn năm sáu"
    assert result[0].end <= result[1].start


def test_without_word_timings_the_split_is_proportional() -> None:
    cue = Cue(10.0, 14.0, "một hai ba bốn năm sáu bảy tám chín mười")
    result = polish([cue], max_chars=24)

    assert len(result) == 2
    assert result[0].start == 10.0
    assert result[-1].end == 14.0
    assert result[0].end <= result[1].start


def test_overlapping_cues_never_stack_on_screen() -> None:
    cues = [Cue(0.0, 2.0, "Một."), Cue(1.5, 3.0, "Hai.")]
    result = polish(cues)

    assert len(result) == 2
    assert result[0].end <= result[1].start


def test_a_flash_cue_stays_readable() -> None:
    result = polish([Cue(1.0, 1.05, "Ơ")])
    assert result[0].end - result[0].start >= 0.7


def test_a_marathon_cue_is_cut_into_pieces() -> None:
    cue = Cue(0.0, 14.0, "a" * 60)
    result = polish([cue])

    assert len(result) >= 2
    assert all(piece.duration <= 7.0 + 1e-6 for piece in result)
    assert all(left.end <= right.start for left, right in zip(result, result[1:], strict=False))


def test_empty_input_is_not_an_error() -> None:
    assert polish([]) == []
    assert polish([Cue(0.0, 1.0, "   ")]) == []


def test_cjk_fragments_are_not_glued_and_their_spaces_disappear() -> None:
    # Whisper emits Chinese clause by clause and separates words with spaces;
    # gluing two cues gives one run-on the translator cannot punctuate.
    cues = [Cue(0.0, 1.0, "你当妈妈 你当爸爸"), Cue(1.05, 2.0, "十年之后你必须把我娶回家")]

    result = polish(cues)

    assert [cue.text for cue in result] == ["你当妈妈你当爸爸", "十年之后你必须把我娶回家"]


def test_a_split_cjk_line_is_not_rejoined_with_spaces() -> None:
    characters = list("签字然后跟我完婚霍家就安然无恙")
    words = [(index * 0.2, index * 0.2 + 0.2, char) for index, char in enumerate(characters)]
    cue = Cue(0.0, 3.0, "".join(characters), words=words)

    result = polish([cue], max_chars=7)

    assert len(result) == 3
    assert all(" " not in piece.text for piece in result)  # never "签 字 然后"
    assert "".join(piece.text for piece in result) == "".join(characters)
