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
    # Free web translators & extra LLM providers (ported from CapCap).
    GOOGLE = "google"
    BING = "bing"
    DEEPSEEK = "deepseek"
    OLLAMA = "ollama"
    BROWSER = "browser"
    # Dubbing voices: the cloud ones, plus local OmniVoice / VoiceStudio routes.
    OMNIVOICE = "omnivoice"
    VOICESTUDIO = "voicestudio"
    ELEVENLABS = "elevenlabs"


class SubtitleSegment(BaseModel):
    """One subtitle line on the timeline."""

    index: int = Field(description="1-based, stable across translate / TTS")
    start: float = Field(ge=0, description="seconds on the timeline")
    end: float = Field(gt=0, description="seconds on the timeline")
    text: str
    speaker: str | None = None
    translation: str | None = None
    no_speech_prob: float | None = None
    original_start: float | None = Field(default=None, description="Last pipeline start timestamp for Restore Timing")
    original_end: float | None = Field(default=None, description="Last pipeline end timestamp for Restore Timing")
    voice: str | None = Field(default=None, description="Per-segment TTS voice override")
    speed: float | None = Field(default=None, description="Per-segment TTS speed multiplier override")
    highlights: list[str] | None = Field(default=None, description="Manual highlighted keywords")

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
    # CapCap ASR cleanup & speaker diarization options
    dedup_enabled: bool = Field(
        default=True,
        description="Reduce repeated phrases where audio sections join, keeping the fuller sentence",
    )
    dedup_similarity: float = Field(
        default=0.82,
        ge=0.5,
        le=1.0,
        description="Normalized text similarity threshold (0.50 .. 1.00) for duplicate phrase removal",
    )
    hallucination_filter_enabled: bool = Field(
        default=True,
        description="Reduce made-up subtitles during silence",
    )
    hallucination_silence_threshold: float = Field(
        default=3.0,
        ge=0.5,
        le=10.0,
        description="Silence duration threshold in seconds (0.5 .. 10.0) for hallucination filtering",
    )
    diarize: bool = Field(
        default=False,
        description="Run offline speaker diarization (Sherpa-ONNX) and tag segments with SPEAKER_XX",
    )
    num_speakers: int = Field(
        default=-1,
        description="Expected number of speakers (-1 = auto cluster)",
    )
    start_time: float | None = Field(
        default=None,
        ge=0.0,
        description="Optional range start (seconds) for selective timeline re-transcription",
    )
    end_time: float | None = Field(
        default=None,
        gt=0.0,
        description="Optional range end (seconds) for selective timeline re-transcription",
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
    provider: Literal["local", "gemini", "openai", "deepseek", "ollama", "browser", "google", "bing"] | None = None
    style: str = Field(default="short-form video subtitle", description="Tone hint or genre preset for the LLM")
    preset: str | None = Field(default=None, description="CapCap genre preset key (e.g. zh_short_drama)")
    custom_prompt: str | None = Field(default=None, description="Custom system/style prompt override")
    auto_context: bool = Field(
        default=False,
        description="Automatically analyze character identities & two-way address rules before translating",
    )
    context_guidance: str | None = Field(
        default=None,
        description="Confirmed character profile and two-way pronoun rules injected into every batch",
    )


class ContextAnalyzeRequest(BaseModel):
    """Analyze dialogue cues to extract character profiles and two-way pronoun rules (ported from CapCap)."""

    segments: list[SubtitleSegment]
    source_language: str = "auto"
    target_language: str = "vi"
    provider: Literal["local", "gemini", "openai", "deepseek", "ollama"] | None = None
    user_guidance: str = Field(
        default="",
        description="User feedback to revise character roles/genders/pronouns (e.g. 'Đảo ngược 2 nhân vật A và B')",
    )
    existing_context: str = Field(
        default="",
        description="Previous character profile draft to revise when re-analyzing with feedback",
    )


class ContextAnalyzeResult(BaseModel):
    context: str
    provider: str
    model: str
    sampled_cues: int


class SrtImportRequest(BaseModel):
    """Import an external .srt file (or raw SRT content) as the transcript or secondary track."""

    srt_path: str | None = None
    content: str | None = None


class OcrRegionRequest(BaseModel):
    """Standalone OCR Translator: read and optionally translate text from any rectangle on a video frame."""

    video_path: str
    time: float = Field(default=0.0, ge=0.0)
    region: list[float] = Field(
        default_factory=lambda: [0.0, 55.0, 100.0, 100.0],
        description="[left, top, right, bottom] in percent (0..100)",
    )
    language: str = "auto"
    translate_to: str | None = None
    translate_provider: str | None = None


class OcrRegionResult(BaseModel):
    text: str
    translation: str = ""
    engine: str
    device: str


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


DubbingProvider = Literal["capcut", "omnivoice", "voicestudio", "elevenlabs"]


class DubbingRequest(BaseModel):
    segments: list[SubtitleSegment]
    use_translation: bool = True
    provider: DubbingProvider | None = None
    voice: str | None = None
    speaker_voices: dict[str, str] = Field(
        default_factory=dict,
        description="Per-speaker voice overrides (e.g. {'SPEAKER_00': 'BV075_streaming'})",
    )
    rate: str | None = Field(default=None, description="Speeding hint, e.g. '+10%' (CapCut only)")
    timing_rewrite: bool = Field(
        default=True,
        description="Borrow following silence and reword over-long lines instead of speeding them up",
    )
    trim_silence: bool = Field(
        default=True,
        description="Trim excessive leading/trailing silence per CapCap tts_audio_policy before fitting",
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
    highlight_mode: Literal["off", "auto", "manual", "both"] = Field(
        default="off", description="Keyword highlight mode (ported from CapCap)"
    )
    highlight_color: str = Field(default="#FACC15", description="Accent hex color for highlighted keywords")

    @field_validator("font")
    @classmethod
    def _plain_font_name(cls, value: str) -> str:
        # A comma would break the ASS style line, an empty name breaks the render.
        cleaned = " ".join(value.replace(",", " ").split())
        return cleaned or "Arial"

    @field_validator("color", "outline_color", "background_color", "highlight_color")
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
    presentation_mode: Literal["none", "single", "bilingual"] = Field(
        default="single", description="Subtitle presentation mode on video (none | single | bilingual)"
    )
    secondary_segments: list[SubtitleSegment] = Field(
        default_factory=list, description="Secondary subtitle cues (TS2) when presentation_mode == 'bilingual'"
    )
    secondary_style: SubtitleStyle | None = Field(
        default=None, description="Independent caption style for secondary subtitles (TS2)"
    )


class SubtitleExportResult(BaseModel):
    output_path: str
    fmt: str
    lines: int


class VisualLayer(BaseModel):
    """Timed visual overlay on the timeline: blur, mask, logo/image, or text (ported from CapCap)."""

    id: str
    kind: Literal["blur", "mask", "logo", "text"]
    start: float = Field(default=0.0, ge=0.0)
    end: float = Field(default=5.0, gt=0.0)
    # Normalized rectangle [x, y, width, height] in percent (0..100) of the frame.
    x: float = Field(default=5.0, ge=0.0, le=100.0)
    y: float = Field(default=70.0, ge=0.0, le=100.0)
    width: float = Field(default=90.0, gt=0.0, le=100.0)
    height: float = Field(default=18.0, gt=0.0, le=100.0)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    # Kind-specific parameters
    blur_strength: int = Field(default=24, ge=2, le=100)
    color: str = Field(default="#000000")
    text: str = Field(default="")
    font_size: int = Field(default=28, ge=8, le=160)
    image_path: str | None = None
    locked: bool = False
    visible: bool = True


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
    voice_gain: float = Field(
        default=1.0,
        ge=0.0,
        le=2.0,
        description="Level of the synthesized voice track",
    )
    keep_original_audio: bool = Field(
        default=False,
        description="Keep the clip's soundtrack in the mix; it is ducked under every dubbed line",
    )
    crf: int = 20
    preset: str = "medium"
    quality_profile: Literal["balanced", "low"] | None = Field(
        default=None, description="CapCap compression profile: balanced (CRF 22 / CQ 25) or low (CRF 26 / CQ 28)"
    )
    use_nvenc: bool = Field(
        default=True, description="Prefer NVIDIA NVENC GPU encoding with automatic CPU fallback"
    )
    flip_horizontal: bool = Field(
        default=False, description="Mirror the video horizontally (CapCap Video Inspector)"
    )
    layers: list[VisualLayer] = Field(
        default_factory=list, description="Timed Blur, Mask, Logo, and Text visual layers"
    )
    start_time: float | None = Field(
        default=None, ge=0.0, description="Optional start offset (used by Fast Preview 5s)"
    )
    sample_duration: float | None = Field(
        default=None, gt=0.0, description="Optional clip length in seconds (used by Fast Preview 5s)"
    )


class RenderResult(BaseModel):
    output_path: str
    duration: float
    size_bytes: int
    width: int
    height: int
    elapsed_s: float
