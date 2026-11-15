"""Subtitle and dubbing contracts shared by the API and the services.

The TypeScript side mirrors these shapes (`frontend/src/types/editor.ts`) so the
WebSocket payloads can be consumed without re-mapping.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex(value: str) -> str:
    """Normalise ``#rgb`` / ``#rrggbb`` to ``#RRGGBB`` (ASS needs six digits)."""
    match = _HEX_COLOR.match((value or "").strip())
    if not match:
        raise ValueError("must be a hex colour such as #FFFFFF")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    return f"#{digits.upper()}"


class Provider(StrEnum):
    LOCAL = "local"
    GROQ = "groq"
    OPENAI = "openai"
    GEMINI = "gemini"
    # Speech-to-text providers that need no key.
    CAPCUT = "capcut"
    BILIBILI = "bilibili"
    OCR = "ocr"
    # Offline CPU recogniser for Chinese / English / Japanese / Korean / Cantonese.
    SENSEVOICE = "sensevoice"
    # Free web translators.
    GOOGLE = "google"
    BING = "bing"
    # Dubbing voices: the cloud one, plus two local OmniVoice routes.
    OMNIVOICE = "omnivoice"
    VOICESTUDIO = "voicestudio"


class SubtitleSegment(BaseModel):
    """One subtitle line on the timeline."""

    index: int = Field(description="1-based, stable across translate / TTS")
    start: float = Field(ge=0, description="seconds on the timeline")
    end: float = Field(gt=0, description="seconds on the timeline")
    text: str
    speaker: str | None = None
    translation: str | None = None
    no_speech_prob: float | None = None

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, value: float, info) -> float:
        start = info.data.get("start", 0.0)
        return value if value > start else start + 0.25

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.05)


class SubtitleTrack(BaseModel):
    language: str = "auto"
    segments: list[SubtitleSegment] = Field(default_factory=list)

    @property
    def duration(self) -> float:
        return max((s.end for s in self.segments), default=0.0)


class TranscribeRequest(BaseModel):
    video_path: str = Field(description="Absolute Windows path of the source clip")
    language: str = "auto"
    provider: Literal[
        "local", "groq", "openai", "capcut", "bilibili", "ocr", "sensevoice"
    ] | None = None
    model: str | None = Field(default=None, description="Override the whisper size")
    vad: bool = True
    word_timestamps: bool = Field(
        default=False,
        description="Deprecated: word timings are always requested (the hallucination filter needs them)",
    )
    polish: bool = Field(
        default=True,
        description="Drop hallucinated lines, glue fragments, split long cues and repair the timeline",
    )
    audio_mode: Literal["fast", "clean"] | None = Field(
        default=None,
        description=(
            "fast = transcribe the mix; clean = separate the vocal first "
            "(better lines when music or effects cover the speech). "
            "None uses the mode saved in Settings."
        ),
    )
    ocr_fill: bool = Field(
        default=False,
        description=(
            "After the speech pass, read the burned-in subtitles inside every stretch that "
            "carries sound but produced no line. Uses the OCR settings; a missing engine "
            "leaves the audio result untouched."
        ),
    )


class TranscribeResult(BaseModel):
    language: str
    duration: float
    segments: list[SubtitleSegment]
    provider: str
    model: str
    device: str
    ocr_filled: int = Field(
        default=0,
        description="Lines the OCR pass added on stretches the speech engine left empty",
    )


class TranslateRequest(BaseModel):
    segments: list[SubtitleSegment]
    target_language: str = "vi"
    source_language: str = "auto"
    provider: Literal["local", "gemini", "openai", "google", "bing"] | None = None
    style: str = Field(default="short-form video subtitle", description="Tone hint for the LLM")


class OcrFrameRequest(BaseModel):
    """Second look at a single cue: the OCR (or the speech engine) got it wrong."""

    video_path: str = Field(description="Absolute Windows path of the source clip")
    start: float = Field(ge=0, description="Cue in-point, seconds on the timeline")
    end: float = Field(gt=0, description="Cue out-point, seconds on the timeline")
    samples: int = Field(default=3, ge=1, le=9, description="Frames to read inside the cue")
    language: str | None = Field(
        default=None,
        description="Recognition language for this read; None keeps the saved setting",
    )


class OcrFrameSample(BaseModel):
    """What one frame of the cue read."""

    time: float = Field(description="Seconds on the timeline")
    text: str


class OcrFrameResult(BaseModel):
    """The new reading, plus everything the confirmation dialog shows."""

    text: str = Field(default="", description="The line the frames agreed on (empty when nothing was read)")
    candidates: list[str] = Field(default_factory=list, description="Distinct readings, best first")
    frames: list[OcrFrameSample] = Field(default_factory=list, description="Per-frame readings")
    engine: str
    device: str = Field(description="Where the reader really ran: gpu or cpu")
    start: float
    end: float
    frame_path: str | None = Field(default=None, description="The cropped still the reader saw")
    region: list[float] = Field(
        default_factory=list,
        description="Scan rectangle used, [left, top, right, bottom] in percent of the frame",
    )


class TranslateResult(BaseModel):
    target_language: str
    provider: str
    model: str
    segments: list[SubtitleSegment]


class DubbingSegmentReport(BaseModel):
    index: int
    start: float
    end: float
    target_duration: float
    tts_duration: float
    applied_ratio: float
    clamped: bool
    overflow_s: float = Field(default=0.0, description="Seconds of speech that had to be trimmed")
    rewritten: bool = Field(default=False, description="The line was reworded to fit its slot")
    spoken: str | None = Field(default=None, description="Text actually sent to the voice")
    source: str | None = Field(
        default=None,
        description="Line text before any timing rewrite; comparing it with the cue tells whether "
        "this take is still current",
    )
    clip_path: str | None = Field(
        default=None,
        description="The fitted WAV of this line, kept so it can be replayed or re-assembled",
    )


DubbingProvider = Literal["capcut", "omnivoice", "voicestudio"]


class DubbingRequest(BaseModel):
    segments: list[SubtitleSegment]
    use_translation: bool = True
    provider: DubbingProvider | None = None
    voice: str | None = None
    rate: str | None = Field(default=None, description="Speeding hint, e.g. '+10%' (CapCut only)")
    timing_rewrite: bool = Field(
        default=True,
        description="Borrow following silence and reword over-long lines instead of speeding them up",
    )
    language: str | None = Field(default=None, description="Language being spoken, defaults to the target language")
    video_path: str | None = Field(default=None, description="Source clip, used to length the assembled track")


class DubbingResult(BaseModel):
    track_path: str
    duration: float
    provider: str
    voice: str
    segments: list[DubbingSegmentReport]
    skipped: int = 0
    rewritten: int = 0


class DubbingCueRequest(BaseModel):
    """Re-speak one cue after its text was edited."""

    segments: list[SubtitleSegment] = Field(description="The whole track: a cue borrows silence from its neighbour")
    index: int = Field(description="Which cue to render")
    audition: bool = Field(
        default=False,
        description="Hand the take back without touching the track (used to just listen to it)",
    )
    use_translation: bool = True
    provider: DubbingProvider | None = None
    voice: str | None = None
    rate: str | None = None
    timing_rewrite: bool = True
    language: str | None = None


class DubbingCueResult(BaseModel):
    index: int
    report: DubbingSegmentReport
    provider: str
    voice: str
    audition: bool


class DubbingRemixRequest(BaseModel):
    """Re-assemble the dub track from the per-line takes already on disk."""

    segments: list[DubbingSegmentReport]
    duration: float = Field(gt=0, description="Length of the track to build")


class DubbingRemixResult(BaseModel):
    track_path: str
    duration: float
    lines: int


class SubtitleStyle(BaseModel):
    """Look of the burned-in captions; the preview overlay mirrors these values."""

    font: str = Field(default="Arial", description="Any font installed on the machine")
    size: float = Field(default=1.0, ge=0.5, le=2.5, description="Multiplier on the frame-derived size")
    color: str = Field(default="#FFFFFF")
    outline_color: str = Field(default="#101010")
    outline: float = Field(default=1.0, ge=0.0, le=4.0, description="Edge thickness multiplier (0 = none)")
    bold: bool = True
    background: bool = Field(default=True, description="Draw a box behind the text")
    background_color: str = Field(default="#000000")
    background_opacity: float = Field(default=0.45, ge=0.0, le=1.0)
    position: Literal["top", "middle", "bottom"] = "bottom"
    horizontal: Literal["left", "center", "right"] = "center"
    margin_v: float = Field(default=7.0, ge=0.0, le=45.0, description="Percent of the height from that edge")
    margin_h: float = Field(default=6.0, ge=0.0, le=45.0, description="Percent of the width from the sides")

    @field_validator("font")
    @classmethod
    def _plain_font_name(cls, value: str) -> str:
        # A comma would break the ASS style line, an empty name breaks the render.
        cleaned = " ".join(value.replace(",", " ").split())
        return cleaned or "Arial"

    @field_validator("color", "outline_color", "background_color")
    @classmethod
    def _css_color(cls, value: str) -> str:
        return _hex(value)


class SubtitleExportRequest(BaseModel):
    segments: list[SubtitleSegment]
    output_path: str = Field(description="Absolute .srt / .ass path")
    fmt: Literal["srt", "ass"] = "ass"
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    use_translation: bool = True
    font: str | None = None
    style: SubtitleStyle | None = Field(default=None, description="Caption look; None keeps the legacy default")


class SubtitleExportResult(BaseModel):
    output_path: str
    fmt: str
    lines: int


class RenderRequest(BaseModel):
    video_path: str
    output_path: str
    aspect: Literal["9:16", "16:9", "1:1", "original"] = "9:16"
    subtitle_path: str | None = Field(default=None, description=".ass or .srt to burn in")
    dub_track_path: str | None = None
    background_path: str | None = Field(
        default=None, description="Explicit bed under the dub; defaults to the clip's own soundtrack"
    )
    background_gain: float = Field(
        default=0.35,
        ge=0.0,
        le=1.0,
        description="Level of the original audio under the dub (0 = mute it)",
    )
    keep_original_audio: bool = Field(
        default=False,
        description="Keep the clip's soundtrack in the mix; it is ducked under every dubbed line",
    )
    crf: int = 20
    preset: str = "medium"


class RenderResult(BaseModel):
    output_path: str
    duration: float
    size_bytes: int
    width: int
    height: int
    elapsed_s: float
