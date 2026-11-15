"""Timecode, SRT/ASS serialisation and overlap handling."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.subtitle import SubtitleSegment, SubtitleStyle
from app.utils.subtitles import (
    dedupe_overlaps,
    format_ass_time,
    format_srt_time,
    parse_timecode,
    to_ass,
    to_srt,
    wrap_text,
    write_subtitles,
)


def test_format_helpers_round() -> None:
    assert format_srt_time(3661.25) == "01:01:01,250"
    assert format_srt_time(0) == "00:00:00,000"
    assert format_ass_time(1.256) == "0:00:01.26"
    assert format_ass_time(0) == "0:00:00.00"


def test_parse_timecode_accepts_both_separators() -> None:
    assert parse_timecode("00:00:01,250") == 1.25
    assert parse_timecode("0:00:01.25") == 1.25
    assert parse_timecode("12.5") == 12.5


def test_segments_are_sorted_and_deduplicated() -> None:
    segments = [
        SubtitleSegment(index=1, start=0.0, end=3.0, text="one"),
        SubtitleSegment(index=2, start=2.5, end=4.0, text="two"),
        SubtitleSegment(index=3, start=4.0, end=5.0, text="three"),
    ]
    fixed = dedupe_overlaps(segments)

    assert [s.index for s in fixed] == [1, 2, 3]
    assert fixed[0].end < fixed[1].start
    assert all(left.end <= right.start for left, right in zip(fixed, fixed[1:], strict=False))


def test_ass_carries_frame_size_and_translation() -> None:
    segments = [
        SubtitleSegment(index=1, start=0.0, end=1.5, text="hello", translation="xin chào"),
    ]
    content = to_ass(segments, width=1080, height=1920)

    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,Default,,0,0,0,,xin chào" in content
    # Font size scales with the frame: 4.2 % of 1920 = 80 px.
    assert ",80," in content


def test_ass_escapes_override_braces() -> None:
    segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="a {b} c")]
    content = to_ass(segments)
    assert "a (b) c" in content


def test_srt_uses_original_text_by_default(tmp_path: Path) -> None:
    segments = [
        SubtitleSegment(index=1, start=0.0, end=2.0, text="source", translation="translated"),
    ]
    text = to_srt(segments)
    assert "source" in text
    assert "translated" not in text

    path = write_subtitles(segments, tmp_path / "out.srt", use_translation=True)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for legacy Windows players
    assert "translated" in raw.decode("utf-8-sig")


def test_wrap_text_balances_a_long_line_at_a_word() -> None:
    lines = wrap_text("Tôi không thể tin nổi chuyện này lại xảy ra")
    assert len(lines) == 2
    assert lines[0].endswith("nổi") or lines[0].endswith("tin")
    assert max(len(line) for line in lines) <= len("Tôi không thể tin nổi chuyện này lại xảy ra")


def test_wrap_text_leaves_short_lines_alone() -> None:
    assert wrap_text("Đi thôi") == ["Đi thôi"]


def test_wrap_text_cuts_languages_without_spaces() -> None:
    lines = wrap_text("这朵花放在这里不会被人发现但是我们还是要小心一点")
    assert len(lines) == 2
    assert "".join(lines) == "这朵花放在这里不会被人发现但是我们还是要小心一点"


def test_ass_breaks_a_long_translation_into_two_lines() -> None:
    segments = [
        SubtitleSegment(
            index=1,
            start=0.0,
            end=3.0,
            text="x",
            translation="Anh không thể tin được chuyện này lại xảy ra ngay tại đây",
        )
    ]
    content = to_ass(segments)
    assert "\\N" in content


# ---------------------------------------------------------------------------
# Caption style panel -> ASS style row
# ---------------------------------------------------------------------------
def _style_row(content: str) -> str:
    return next(line for line in content.splitlines() if line.startswith("Style: Default"))


def test_style_moves_the_caption_and_paints_a_translucent_box() -> None:
    style = SubtitleStyle(
        color="#FFCC00",
        size=1.5,
        bold=False,
        background=True,
        background_opacity=0.4,
        position="top",
        horizontal="left",
        margin_v=10,
        margin_h=4,
    )
    content = to_ass([SubtitleSegment(index=1, start=0.0, end=1.0, text="hi")], style=style)

    # 80 px * 1.5 = 120; alignment 7 = top-left; 1080 * 4 % = 43; 1920 * 10 % = 192.
    assert (
        _style_row(content) == "Style: Default,Arial,120,&H0000CCFF,&H000000FF,&H00101010,&H99000000,"
        "0,0,0,0,100,100,0,0,3,11,0,7,43,43,192,1"
    )


def test_style_without_a_box_keeps_an_outline_and_a_shadow() -> None:
    style = SubtitleStyle(
        font="Be Vietnam, Pro",
        bold=True,
        background=False,
        outline=2.0,
        position="bottom",
        horizontal="right",
    )
    content = to_ass([SubtitleSegment(index=1, start=0.0, end=1.0, text="hi")], style=style)

    assert (
        _style_row(content) == "Style: Default,Be Vietnam Pro,80,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,9,1,3,64,64,134,1"
    )


def test_style_can_switch_the_edge_off() -> None:
    style = SubtitleStyle(background=False, outline=0.0)
    content = to_ass([SubtitleSegment(index=1, start=0.0, end=1.0, text="hi")], style=style)

    # The Outline field sits between BorderStyle and Shadow.
    assert ",1,0,1,2," in _style_row(content)


def test_style_scales_with_the_export_frame() -> None:
    style = SubtitleStyle(size=1.0, margin_v=10.0, margin_h=10.0)
    content = to_ass(
        [SubtitleSegment(index=1, start=0.0, end=1.0, text="hi")],
        width=1920,
        height=1080,
        style=style,
    )

    # 1080 * 4.2 % = 45 px, margins 192 px sideways and 108 px down.
    assert ",45," in _style_row(content)
    assert _style_row(content).endswith(",192,192,108,1")


def test_style_normalises_short_hex_colours() -> None:
    style = SubtitleStyle(color="#abc", outline_color="fff")
    assert style.color == "#AABBCC"
    assert style.outline_color == "#FFFFFF"


def test_style_rejects_a_colour_that_is_not_hex() -> None:
    with pytest.raises(ValueError):
        SubtitleStyle(color="red")


def test_style_keeps_the_default_look_when_none_is_given() -> None:
    content = to_ass([SubtitleSegment(index=1, start=0.0, end=1.0, text="hi")])
    assert "&H80000000,0,0,0,0,100,100,0,0,1,4,1,2,64,64,134,1" in _style_row(content)


# ---------------------------------------------------------------------------
# HTTP contract: the style the editor holds is the style the file gets
# ---------------------------------------------------------------------------
def test_export_endpoint_writes_the_style_it_was_given(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    target = tmp_path / "sub.ass"
    with TestClient(app) as client:
        response = client.post(
            "/api/subtitles/export",
            json={
                "segments": [
                    {"index": 1, "start": 0.0, "end": 1.5, "text": "xin chào", "translation": "hello"}
                ],
                "output_path": str(target),
                "fmt": "ass",
                "aspect": "16:9",
                "use_translation": True,
                "style": {
                    "font": "Be Vietnam Pro",
                    "size": 1.2,
                    "color": "#FFCC00",
                    "bold": False,
                    "background": False,
                    "outline": 0.5,
                    "position": "top",
                    "horizontal": "left",
                    "margin_v": 12,
                    "margin_h": 9,
                },
            },
        )

    assert response.status_code == 200, response.text
    row = _style_row(target.read_text(encoding="utf-8"))
    # 16:9 frame: 1080 * 4.2 % = 45 px, scaled by 1.2 -> 54. Font, colour and the
    # plain-edge fields follow; the top-left alignment is 7 and the margins are
    # 9 % sideways (172 px) and 12 % down (129 px).
    assert row == (
        "Style: Default,Be Vietnam Pro,54,&H0000CCFF,&H000000FF,&H00101010,&H80000000,"
        "0,0,0,0,100,100,0,0,1,2,1,7,172,172,129,1"
    )
