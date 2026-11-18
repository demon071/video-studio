"""Subtitle serialisation (.srt / .ass) and timecode helpers.

ASS is the format that actually gets burned in: it carries styling, per-line
positioning and a fixed text area, while SRT is only produced for users who want
to hand the file to another tool.
"""

from __future__ import annotations

from functools import lru_cache
import os
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


_DISPLAY_PUNCT_RE = re.compile(r'[.,!?;:…—–"“”‘’«»،、，。！？；：]+')


def clean_text(text: str) -> str:
    """Strip characters that break ASS parsing or punctuation unwanted on screen."""
    text = text.replace("\r", " ").replace("\n", " ")
    text = text.replace("{", "(").replace("}", ")")  # ASS override tags
    text = _DISPLAY_PUNCT_RE.sub(" ", text)
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
    name: str = "Default",
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
        box_color = _ass_color(
            style.background_color,
            alpha=int(round((1 - style.background_opacity) * 255)),
        )
        back = box_color if style.background else "&H80000000"
        # In libass, BorderStyle=3 draws the background box using OutlineColour!
        outline_color = box_color if style.background else _ass_color(style.outline_color)
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
        f"Style: {name},{font},{size},{_ass_color(primary)},&H000000FF,"
        f"{outline_color},{back},{bold},0,0,0,100,100,0,0,{border},{edge},{shadow},{alignment},"
        f"{margin_l},{margin_r},{margin_y},1"
    )


_WIN_FONT_MAP: dict[tuple[str, bool], tuple[str, ...]] = {
    ("arial", False): ("arial.ttf",),
    ("arial", True): ("arialbd.ttf", "arial.ttf"),
    ("segoe ui", False): ("segoeui.ttf", "arial.ttf"),
    ("segoe ui", True): ("segoeuib.ttf", "segoeui.ttf", "arialbd.ttf"),
    ("tahoma", False): ("tahoma.ttf", "arial.ttf"),
    ("tahoma", True): ("tahomabd.ttf", "tahoma.ttf", "arialbd.ttf"),
    ("times new roman", False): ("times.ttf", "arial.ttf"),
    ("times new roman", True): ("timesbd.ttf", "times.ttf", "arialbd.ttf"),
}


@lru_cache(maxsize=64)
def _load_pil_font(font_name: str, px_size: int, bold: bool):
    try:
        from PIL import ImageFont
    except ImportError:
        return None
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    key = (font_name.strip().lower(), bool(bold))
    candidates = list(_WIN_FONT_MAP.get(key, ())) + [
        "arialbd.ttf" if bold else "arial.ttf",
        "segoeui.ttf",
    ]
    for filename in candidates:
        p = fonts_dir / filename
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), px_size)
            except OSError:
                continue
    return None


def _measure_line_px(line: str, font_name: str, ass_font_size: int, bold: bool) -> float:
    """Measure the rendered pixel width of ``line`` for an ASS ``Fontsize``."""
    # In libass (VSFilter/GDI metrics), Fontsize includes internal leading, so the
    # FreeType em-size is ~0.85 * Fontsize.
    px_size = max(int(round(ass_font_size * 0.85)), 10)
    pil_font = _load_pil_font(font_name, px_size, bold)
    if pil_font is not None:
        try:
            return float(pil_font.getlength(line))
        except Exception:
            pass
    factor = 0.56 if bold else 0.52
    return len(line) * ass_font_size * factor


def _rounded_box_path(w: int, h: int, r: int) -> str:
    r = max(0, min(r, w // 2, h // 2))
    return (
        f"m {r} 0 l {w - r} 0 b {w} 0 {w} 0 {w} {r} "
        f"l {w} {h - r} b {w} {h} {w} {h} {w - r} {h} "
        f"l {r} {h} b 0 {h} 0 {h} 0 {h - r} "
        f"l 0 {r} b 0 0 0 0 {r} 0"
    )


def _box_style_line(style: SubtitleStyle, *, name: str) -> str:
    font = (style.font or "Arial").replace(",", " ").strip() or "Arial"
    box_color = _ass_color(
        style.background_color,
        alpha=int(round((1 - style.background_opacity) * 255)),
    )
    return (
        f"Style: {name},{font},80,{box_color},&H000000FF,"
        f"{box_color},{box_color},0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1"
    )


def _flat_text_style_line(style: SubtitleStyle, *, name: str, base_size: int) -> str:
    """CapCap-style BorderStyle=1 text layer placed over the vector background box."""
    size = max(int(round(base_size * style.size)), 12)
    font = (style.font or "Arial").replace(",", " ").strip() or "Arial"
    bold = -1 if style.bold else 0
    return (
        f"Style: {name},{font},{size},{_ass_color(style.color)},&H000000FF,"
        f"&HFF000000,&HFF000000,{bold},0,0,0,100,100,0,0,1,0,0,5,0,0,0,1"
    )


def _box_and_text_events(
    wrapped: list[str],
    *,
    start_tc: str,
    end_tc: str,
    style: SubtitleStyle,
    base_size: int,
    width: int,
    height: int,
    text_style_name: str = "Default",
    box_style_name: str = "DefaultBox",
    base_layer: int = 0,
) -> list[str]:
    size = max(int(round(base_size * style.size)), 12)
    font = (style.font or "Arial").replace(",", " ").strip() or "Arial"
    line_w = max((_measure_line_px(line, font, size, style.bold) for line in wrapped), default=0.0)
    pad_x = max(int(round(size * 0.35)), 12)
    pad_y = max(int(round(size * 0.18)), 8)
    box_w = min(width - 16, max(int(round(line_w + pad_x * 2)), size))
    box_h = int(round(len(wrapped) * size * 1.0 + pad_y * 2))
    radius = max(min(int(round(size * 0.22)), box_w // 4, box_h // 4), 6)

    margin_l = int(width * style.margin_h / 100)
    margin_y = int(height * style.margin_v / 100)
    if style.horizontal == "left":
        cx = margin_l + box_w // 2
    elif style.horizontal == "right":
        cx = width - margin_l - box_w // 2
    else:
        cx = width // 2

    if style.position == "top":
        cy = margin_y + box_h // 2
    elif style.position == "middle":
        cy = height // 2
    else:
        cy = height - margin_y - box_h // 2

    path_cmd = _rounded_box_path(box_w, box_h, radius)
    body = "\\N".join(wrapped)
    return [
        f"Dialogue: {base_layer},{start_tc},{end_tc},{box_style_name},,0,0,0,,{{\\an5\\pos({cx},{cy})\\fscx100\\fscy100\\bord0\\shad0\\p1}}{path_cmd}{{\\p0}}",
        f"Dialogue: {base_layer + 1},{start_tc},{end_tc},{text_style_name},,0,0,0,,{{\\bord0\\shad0\\an5\\pos({cx},{cy})}}{body}",
    ]


def parse_srt(content: str) -> list[SubtitleSegment]:
    """Parse an SRT file (with flexible timestamp formats) into ``SubtitleSegment`` items (ported from CapCap)."""
    raw = (content or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    arrow_re = re.compile(r"^\s*([^\s\-]+)\s*-->\s*([^\s]+)")
    segments: list[SubtitleSegment] = []
    for block in re.split(r"\n\s*\n", raw):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        time_idx = -1
        match = None
        for idx, line in enumerate(lines[:2]):
            m = arrow_re.match(line)
            if m:
                time_idx = idx
                match = m
                break
        if match is None:
            continue
        try:
            start = max(0.0, parse_timecode(match.group(1)))
            end = max(start + 0.12, parse_timecode(match.group(2)))
        except ValueError:
            continue
        text = " ".join(lines[time_idx + 1 :]).strip()
        if not text:
            continue
        index = len(segments) + 1
        segments.append(
            SubtitleSegment(
                index=index,
                start=round(start, 3),
                end=round(end, 3),
                text=text,
                original_start=round(start, 3),
                original_end=round(end, 3),
            )
        )
    return segments


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
    presentation_mode: str = "single",
    secondary_segments: Sequence[SubtitleSegment] = (),
    secondary_style: SubtitleStyle | None = None,
) -> str:
    """Render a styled ASS file sized for the target frame (single, bilingual, or none).

    The font size is derived from the frame height (≈4.2 % of it) so a 9:16 export
    and a 16:9 export look equally readable. ``style`` (the editor's caption
    panel) overrides the individual keyword arguments; leaving it out keeps the
    plain look earlier versions rendered.
    """
    base_size = font_size or max(int(height * 0.042), 24)
    styles = [
        _style_line(
            style,
            name="Default",
            width=width,
            height=height,
            base_size=base_size,
            font=font,
            primary=primary,
            outline=outline,
            margin_v=margin_v,
        )
    ]
    if style is not None and style.background:
        styles.append(_box_style_line(style, name="DefaultBox"))
        styles.append(_flat_text_style_line(style, name="DefaultText", base_size=base_size))
    sec_style: SubtitleStyle | None = None
    if presentation_mode == "bilingual":
        sec_style = secondary_style or SubtitleStyle(
            font=style.font if style else font,
            size=round((style.size if style else 1.0) * 0.75, 2),
            color="#22D3EE",
            outline_color=style.outline_color if style else outline,
            outline=style.outline if style else 1.0,
            bold=False,
            background=style.background if style else True,
            background_color=style.background_color if style else "#000000",
            background_opacity=style.background_opacity if style else 0.45,
            position=style.position if style else "bottom",
            horizontal=style.horizontal if style else "center",
            margin_v=min(45.0, (style.margin_v if style else 7.0) + 7.0),
            margin_h=style.margin_h if style else 6.0,
        )
        styles.append(
            _style_line(
                sec_style,
                name="Secondary",
                width=width,
                height=height,
                base_size=base_size,
                font=sec_style.font,
                primary=sec_style.color,
                outline=sec_style.outline_color,
                margin_v=None,
            )
        )
        if sec_style.background:
            styles.append(_box_style_line(sec_style, name="SecondaryBox"))
            styles.append(_flat_text_style_line(sec_style, name="SecondaryText", base_size=base_size))

    styles_block = "\n".join(styles)
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
{styles_block}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    if presentation_mode == "none":
        return header

    lines: list[str] = []
    for segment in dedupe_overlaps(segments):
        raw = segment.translation if use_translation and segment.translation else segment.text
        text = clean_text(raw or "")
        if not text:
            continue
        wrapped = wrap_text(text)
        start_tc = format_ass_time(segment.start)
        end_tc = format_ass_time(segment.end)
        if style is not None and style.background:
            lines.extend(
                _box_and_text_events(
                    wrapped,
                    start_tc=start_tc,
                    end_tc=end_tc,
                    style=style,
                    base_size=base_size,
                    width=width,
                    height=height,
                    text_style_name="DefaultText",
                    box_style_name="DefaultBox",
                    base_layer=0,
                )
            )
        else:
            body = "\\N".join(wrapped)
            lines.append(f"Dialogue: 0,{start_tc},{end_tc},Default,,0,0,0,,{body}")

    if presentation_mode == "bilingual" and sec_style is not None:
        sec_source = secondary_segments if secondary_segments else segments
        for segment in dedupe_overlaps(sec_source):
            raw = segment.text if secondary_segments else segment.text
            text = clean_text(raw or "")
            if not text:
                continue
            wrapped = wrap_text(text)
            start_tc = format_ass_time(segment.start)
            end_tc = format_ass_time(segment.end)
            if sec_style.background:
                lines.extend(
                    _box_and_text_events(
                        wrapped,
                        start_tc=start_tc,
                        end_tc=end_tc,
                        style=sec_style,
                        base_size=base_size,
                        width=width,
                        height=height,
                        text_style_name="SecondaryText",
                        box_style_name="SecondaryBox",
                        base_layer=1,
                    )
                )
            else:
                body = "\\N".join(wrapped)
                lines.append(f"Dialogue: 1,{start_tc},{end_tc},Secondary,,0,0,0,,{body}")

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
    "parse_srt",
    "parse_timecode",
    "to_ass",
    "to_srt",
    "total_duration",
    "wrap_text",
    "write_subtitles",
]
