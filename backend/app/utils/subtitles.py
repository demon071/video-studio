"""Subtitle serialisation (.srt / .ass) and timecode helpers.

ASS is the format that actually gets burned in: it carries styling, per-line
positioning and a fixed text area, while SRT is only produced for users who want
to hand the file to another tool.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from app.schemas.subtitle import SubtitleSegment, SubtitleStyle

_TIMECODE_RE = re.compile(r"(?P<h>\d+):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{1,3})")


def format_srt_time(seconds: float) -> str:
    """00:00:01,250"""
    seconds = max(seconds, 0.0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    whole = int(secs)
    millis = int(round((secs - whole) * 1000))
    if millis == 1000:  # rounding overflow must not emit 00:00:01,1000
        whole += 1
        millis = 0
    return f"{int(hours):02d}:{int(minutes):02d}:{whole:02d},{millis:03d}"


def format_ass_time(seconds: float) -> str:
    """0:00:01.25 (centisecond precision, required by libass)"""
    seconds = max(seconds, 0.0)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    whole = int(secs)
    centis = int(round((secs - whole) * 100))
    if centis == 100:
        whole += 1
        centis = 0
    return f"{int(hours)}:{int(minutes):02d}:{whole:02d}.{centis:02d}"


def parse_timecode(value: str) -> float:
    """Accept '00:00:01,250', '0:00:01.25' or a plain number of seconds."""
    value = value.strip()
    match = _TIMECODE_RE.search(value)
    if not match:
        return float(value)
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    fraction = match.group("ms").ljust(3, "0")
    return hours * 3600 + minutes * 60 + seconds + int(fraction) / 1000


def clean_text(text: str) -> str:
    """Strip characters that break ASS parsing or look broken on screen."""
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("{", "(").replace("}", ")")  # ASS override tags
    text = re.sub(r"\s+", " ", text).strip()
    return text


MAX_LINE_CHARS = 21  # two of these make the 42 character cue budget
_SPLIT_MARKS = "，。！？；：、,.!?;:…—"


def wrap_text(text: str, *, max_chars: int = MAX_LINE_CHARS) -> list[str]:
    """Balance one cue into at most two lines a viewer can scan.

    libass wraps on its own, but it wraps at the margin, which drops a single
    word onto a line of its own. Splitting near the middle at a word boundary -
    and cutting languages that have no spaces at all - is the difference between
    a cue that looks typeset and one that looks accidental.
    """
    text = text.strip()
    if not text or len(text) <= max_chars:
        return [text]

    tokens = text.split()
    if len(tokens) < 2:
        middle = len(text) // 2
        cut = max((text.rfind(mark, 0, middle + 8) for mark in _SPLIT_MARKS), default=-1)
        cut = cut + 1 if cut + 1 >= max(middle // 2, 1) else middle
        head, tail = text[:cut].strip(), text[cut:].strip()
        return [head, tail] if head and tail else [text]

    best_index, best_score = 1, None
    running = 0
    for index, token in enumerate(tokens[:-1], start=1):
        running += len(token) + 1  # the space that follows it
        score = abs(running - len(text) / 2)
        if best_score is None or score < best_score:
            best_index, best_score = index, score
    return [" ".join(tokens[:best_index]), " ".join(tokens[best_index:])]


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS is BGR with inverted alpha)."""
    value = hex_rgb.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


# ASS aligns with a numeric keypad: 7 8 9 on top, 4 5 6 in the middle, 1 2 3 below.
_ALIGNMENT: dict[tuple[str, str], int] = {
    ("top", "left"): 7,
    ("top", "center"): 8,
    ("top", "right"): 9,
    ("middle", "left"): 4,
    ("middle", "center"): 5,
    ("middle", "right"): 6,
    ("bottom", "left"): 1,
    ("bottom", "center"): 2,
    ("bottom", "right"): 3,
}


def _style_line(
    style: SubtitleStyle | None,
    *,
    width: int,
    height: int,
    base_size: int,
    font: str,
    primary: str,
    outline: str,
    margin_v: int | None,
) -> str:
    """The single ``Style:`` row, either the plain default or the editor's look."""
    if style is None:
        size, bold, border, back = base_size, 0, 1, "&H80000000"
        edge, shadow, alignment = max(base_size // 18, 2), 1, 2
        outline_color = _ass_color(outline)
        margin_l = margin_r = int(width * 0.06)
        margin_y = margin_v or int(height * 0.07)
    else:
        size = max(int(round(base_size * style.size)), 12)
        font = style.font
        primary = style.color
        bold = -1 if style.bold else 0
        border = 3 if style.background else 1
        back = (
            _ass_color(style.background_color, alpha=int(round((1 - style.background_opacity) * 255)))
            if style.background
            else "&H80000000"
        )
        outline_color = _ass_color(style.outline_color)
        # BorderStyle 3 turns the Outline field into the padding inside the box.
        edge = (
            max(int(round(size * 0.09 * max(style.outline, 1.0))), 2)
            if style.background
            else (max(int(round(size / 18 * style.outline)), 1) if style.outline > 0 else 0)
        )
        shadow = 0 if style.background else 1
        alignment = _ALIGNMENT[(style.position, style.horizontal)]
        margin_l = margin_r = int(width * style.margin_h / 100)
        margin_y = int(height * style.margin_v / 100)

    return (
        f"Style: Default,{font},{size},{_ass_color(primary)},&H000000FF,"
        f"{outline_color},{back},{bold},0,0,0,100,100,0,0,{border},{edge},{shadow},{alignment},"
        f"{margin_l},{margin_r},{margin_y},1"
    )


def dedupe_overlaps(segments: Iterable[SubtitleSegment], min_gap: float = 0.04) -> list[SubtitleSegment]:
    """Make the timeline strictly monotonic so libass never stacks two lines."""
    ordered = sorted(segments, key=lambda s: (s.start, s.index))
    fixed: list[SubtitleSegment] = []
    for segment in ordered:
        if fixed and segment.start < fixed[-1].end:
            previous = fixed[-1]
            # Shrink the previous line rather than dropping either one.
            previous.end = max(previous.start + min_gap, segment.start - min_gap)
        fixed.append(segment)
    return fixed


def to_srt(segments: Sequence[SubtitleSegment], use_translation: bool = False) -> str:
    blocks: list[str] = []
    for position, segment in enumerate(segments, start=1):
        text = (segment.translation if use_translation and segment.translation else segment.text) or ""
        body = "\n".join(wrap_text(clean_text(text)))
        blocks.append(
            f"{position}\n"
            f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}\n"
            f"{body}\n"
        )
    return "\n".join(blocks)


def to_ass(
    segments: Sequence[SubtitleSegment],
    *,
    width: int = 1080,
    height: int = 1920,
    font: str = "Arial",
    font_size: int | None = None,
    use_translation: bool = True,
    primary: str = "#FFFFFF",
    outline: str = "#101010",
    margin_v: int | None = None,
    style: SubtitleStyle | None = None,
) -> str:
    """Render a styled ASS file sized for the target frame.

    The font size is derived from the frame height (≈4.2 % of it) so a 9:16 export
    and a 16:9 export look equally readable. ``style`` (the editor's caption
    panel) overrides the individual keyword arguments; leaving it out keeps the
    plain look earlier versions rendered.
    """
    base_size = font_size or max(int(height * 0.042), 24)
    header = f"""[Script Info]
; Generated by ShortVideoAI
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{_style_line(style, width=width, height=height, base_size=base_size, font=font, primary=primary, outline=outline, margin_v=margin_v)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []
    for segment in dedupe_overlaps(segments):
        raw = segment.translation if use_translation and segment.translation else segment.text
        text = clean_text(raw or "")
        if not text:
            continue
        # A hard line break, not an automatic wrap: two balanced lines read
        # faster than one long line libass re-flows at the margin.
        body = "\\N".join(wrap_text(text))
        lines.append(
            "Dialogue: 0,"
            f"{format_ass_time(segment.start)},{format_ass_time(segment.end)},"
            f"Default,,0,0,0,,{body}"
        )

    return header + "\n".join(lines) + "\n"


def write_subtitles(
    segments: Sequence[SubtitleSegment],
    output_path: str | Path,
    *,
    fmt: str | None = None,
    **ass_kwargs,
) -> Path:
    """Write .srt or .ass based on the extension (or an explicit format)."""
    path = Path(output_path)
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "ass":
        content = to_ass(segments, **ass_kwargs)
    elif fmt == "srt":
        content = to_srt(segments, use_translation=ass_kwargs.get("use_translation", False))
    else:
        raise ValueError(f"Unsupported subtitle format: {fmt!r}")

    # UTF-8 with BOM keeps Vietnamese/Japanese legible in legacy Windows players.
    path.write_text(content, encoding="utf-8-sig")
    return path


def total_duration(segments: Sequence[SubtitleSegment]) -> float:
    return max((s.end for s in segments), default=0.0)


__all__ = [
    "MAX_LINE_CHARS",
    "clean_text",
    "dedupe_overlaps",
    "format_ass_time",
    "format_srt_time",
    "parse_timecode",
    "to_ass",
    "to_srt",
    "total_duration",
    "wrap_text",
    "write_subtitles",
]
