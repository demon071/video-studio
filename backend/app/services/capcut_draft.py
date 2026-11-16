"""Hand the open project over to CapCut Desktop as an editable draft.

CapCut keeps every project as a folder of JSON documents under
``%LOCALAPPDATA%\\CapCut\\User Data\\Projects\\com.lveditor.draft`` and lists
those folders in ``root_meta_info.json``. Writing one turns the export into a
hand-over instead of a flattening: the clip, the captions and the dub arrive as
separate, editable tracks that can be retimed, restyled or cut further.

The schema below was read off a draft the installed CapCut wrote itself
(``version 360000`` / ``new_version 191.0.0``): times are microseconds, the
text size is expressed on a 200-unit reference stretched to the canvas width,
and a track never holds overlapping segments - overlapping cues are spread over
several tracks instead.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.schemas.subtitle import DubbingSegmentReport, SubtitleSegment, SubtitleStyle
from app.services import media_processor
from app.utils.subtitles import clean_text, dedupe_overlaps, wrap_text

log = logging.getLogger(__name__)

# --- CapCut draft constants -----------------------------------------------
DRAFT_VERSION = 360000
NEW_VERSION = "191.0.0"
APP_ID = 359289

CANVAS: dict[str, tuple[int, int]] = dict(media_processor.TARGET_ASPECTS)

AUDIO_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus")

# A CapCut text size is a glyph height on a 200-unit-wide reference that is
# stretched to the canvas; the app sizes captions in frame pixels. 0.85 is the
# calibration the preview applies before the canvas ratio is taken out.
TEXT_REFERENCE_WIDTH = 200.0
TEXT_PREVIEW_SCALE = 0.85
LINE_HEIGHT = 1.2

# Every bucket a draft carries, including the ones this writer leaves empty:
# CapCut reads the file as a whole and a missing bucket is a missing panel.
MATERIAL_KEYS = (
    "flowers",
    "videos",
    "tail_leaders",
    "audios",
    "images",
    "texts",
    "effects",
    "stickers",
    "canvases",
    "transitions",
    "audio_effects",
    "audio_fades",
    "beats",
    "material_animations",
    "placeholders",
    "placeholder_infos",
    "speeds",
    "common_mask",
    "masks",
    "chromas",
    "text_templates",
    "realtime_denoises",
    "audio_pannings",
    "audio_pitch_shifts",
    "video_trackings",
    "hsl",
    "drafts",
    "color_curves",
    "hsl_curves",
    "primary_color_wheels",
    "log_color_wheels",
    "video_effects",
    "ai_text_effects",
    "ai_generate_holders",
    "audio_balances",
    "handwrites",
    "manual_deformations",
    "manual_beautys",
    "plugin_effects",
    "sound_channel_mappings",
    "green_screens",
    "shapes",
    "material_colors",
    "digital_humans",
    "digital_human_model_dressing",
    "smart_crops",
    "ai_translates",
    "audio_track_indexes",
    "loudnesses",
    "vocal_beautifys",
    "vocal_separations",
    "smart_relights",
    "time_marks",
    "multi_language_refs",
    "video_shadows",
    "video_strokes",
    "video_radius",
)

ProgressFn = Callable[[str, float], None]


class DraftError(RuntimeError):
    """Raised when a draft cannot be written (missing media, locked index...)."""


# ---------------------------------------------------------------------------
# Small schema helpers
# ---------------------------------------------------------------------------
def _id() -> str:
    """CapCut ids are upper-case UUID4 strings."""
    return str(uuid4()).upper()


def _us(seconds: float) -> int:
    """Seconds -> the integer microseconds every draft timeline uses."""
    value = float(seconds)
    if not math.isfinite(value) or value < 0:
        raise DraftError(f"Thời gian không hợp lệ: {seconds!r}")
    return int(round(value * 1_000_000))


def _range(start: float, duration: float) -> dict[str, int]:
    return {"start": _us(start), "duration": _us(duration)}


def _clamp(value: float, low: float = -0.98, high: float = 0.98) -> float:
    return max(low, min(high, value))


def _rgb(color: str) -> list[float]:
    """``#RRGGBB`` -> ``[r, g, b]`` in 0..1, the way CapCut stores a solid fill."""
    digits = color.lstrip("#")
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    if len(digits) != 6:
        digits = "FFFFFF"
    return [int(digits[index : index + 2], 16) / 255.0 for index in (0, 2, 4)]


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _font_faces() -> tuple[tuple[str, str, str], ...]:
    """``(family, face, path)`` for every font installed for this user.

    Pillow reads the family and the style out of the file itself, which is the
    only reliable way to find e.g. the bold cut of "Arial" (``arialbd.ttf``).
    """
    try:
        from PIL import ImageFont  # noqa: PLC0415 - optional, only for fonts
    except ImportError:  # pragma: no cover - Pillow ships with the OCR extras
        return ()

    local = os.environ.get("LOCALAPPDATA") or ""
    directories = [Path(os.environ.get("WINDIR") or "C:/Windows") / "Fonts"]
    if local:
        directories.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    faces: list[tuple[str, str, str]] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                family, face = ImageFont.truetype(str(path), 12).getname()
            except (OSError, ValueError):
                continue
            faces.append((family.casefold(), face.casefold(), str(path.resolve())))
    return tuple(faces)


# A width or weight variant shares the family name of the regular face, so
# "Arial" alone would happily hand CapCut "Arial Narrow Bold".
_STYLE_NOISE = ("narrow", "condensed", "expanded", "light", "thin", "medium", "black", "heavy")


def font_path(family: str, bold: bool = False) -> str:
    """Absolute path of the face CapCut should load, empty when unavailable."""
    wanted = (family or "Arial").casefold()
    scored: list[tuple[int, str]] = []
    for name, face, path in _font_faces():
        if name != wanted:
            continue
        score = int(("bold" in face) != bold)
        score += int("italic" in face or "oblique" in face)
        score += int(any(noise in face for noise in _STYLE_NOISE))
        scored.append((score, path))
    return min(scored)[1] if scored else ""


# ---------------------------------------------------------------------------
# Discovery: where does CapCut keep its projects?
# ---------------------------------------------------------------------------
def drafts_root() -> Path | None:
    """``%LOCALAPPDATA%\\CapCut\\User Data\\Projects\\com.lveditor.draft``."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    root = Path(local) / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
    return root if root.is_dir() else None


def installed_version() -> str | None:
    """Newest CapCut build on disk (``%LOCALAPPDATA%\\CapCut\\Apps\\<version>``)."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    apps = Path(local) / "CapCut" / "Apps"
    if not apps.is_dir():
        return None
    versions: list[tuple[tuple[int, ...], str]] = []
    for folder in apps.iterdir():
        parts = folder.name.split(".")
        if not folder.is_dir() or not all(part.isdigit() for part in parts):
            continue
        versions.append((tuple(int(part) for part in parts), folder.name))
    return max(versions)[1] if versions else None


def capcut_running() -> bool:
    """True while CapCut is open; it rewrites the project index when it exits."""
    try:
        import psutil  # noqa: PLC0415 - optional on a bare install
    except ImportError:
        return False
    for process in psutil.process_iter(["name"]):
        name = (process.info.get("name") or "").casefold()
        if name.startswith("capcut"):
            return True
    return False


def index_entries(root: Path) -> int:
    """How many drafts CapCut's index currently lists."""
    index = root / "root_meta_info.json"
    if not index.is_file():
        return 0
    try:
        data = json.loads(index.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return 0
    store = data.get("all_draft_store")
    return len(store) if isinstance(store, list) else 0


@dataclass(frozen=True)
class Targets:
    """What the export UI needs before it offers the button."""

    drafts_root: str | None
    exists: bool
    fallback_root: str
    capcut_version: str | None
    capcut_running: bool
    drafts: int


def targets() -> Targets:
    root = drafts_root()
    fallback = get_settings().data_path / "exports" / "capcut"
    return Targets(
        drafts_root=str(root) if root else None,
        exists=root is not None,
        fallback_root=str(fallback),
        capcut_version=installed_version(),
        capcut_running=capcut_running(),
        drafts=index_entries(root) if root else 0,
    )


# ---------------------------------------------------------------------------
# Text geometry
# ---------------------------------------------------------------------------
def text_size(style: SubtitleStyle, canvas_width: int, canvas_height: int) -> tuple[float, float]:
    """``(capcut_size, pixels)`` for the caption look.

    ``pixels`` is the same size the ASS burn uses - 4.2 % of the frame height,
    scaled by the slider - so a draft and an MP4 export of one project look
    alike. ``capcut_size`` re-expresses it on CapCut's canvas-relative scale.
    """
    base = max(int(canvas_height * 0.042), 24)
    pixels = max(int(round(base * style.size)), 12)
    size = pixels * TEXT_PREVIEW_SCALE * TEXT_REFERENCE_WIDTH / max(canvas_width, 1)
    return round(size, 3), float(pixels)


def text_width_px(text: str, pixels: float) -> float:
    """Rough rendered width: wide glyphs (CJK, emoji) count as a full em."""
    ems = sum(1.0 if unicodedata.east_asian_width(char) in ("W", "F") else 0.55 for char in text)
    return max(ems, 1.0) * pixels


def text_offset(
    style: SubtitleStyle,
    canvas_width: int,
    canvas_height: int,
    pixels: float,
    lines: int,
    width_px: float,
) -> tuple[float, float]:
    """``(x, y)`` in CapCut's ±1 = ±half-canvas transform space."""
    block = max(lines, 1) * pixels * LINE_HEIGHT
    if style.position == "top":
        centre = canvas_height * style.margin_v / 100 + block / 2
    elif style.position == "middle":
        centre = canvas_height / 2
    else:
        centre = canvas_height - canvas_height * style.margin_v / 100 - block / 2
    y = 1 - 2 * centre / canvas_height

    if style.horizontal == "center":
        x = 0.0
    else:
        margin = canvas_width * style.margin_h / 100
        anchor = margin + width_px / 2 if style.horizontal == "left" else canvas_width - margin - width_px / 2
        x = 2 * anchor / canvas_width - 1
    return _clamp(x), _clamp(y)


# ---------------------------------------------------------------------------
# The request, decoupled from HTTP
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DraftSpec:
    """Everything the writer needs, already validated by the caller."""

    video_path: str
    aspect: str = "9:16"
    segments: Sequence[SubtitleSegment] = ()
    style: SubtitleStyle = field(default_factory=SubtitleStyle)
    use_translation: bool = True
    presentation_mode: str = "single"
    secondary_segments: Sequence[SubtitleSegment] = ()
    secondary_style: SubtitleStyle | None = None
    dub_track_path: str | None = None
    dub_segments: Sequence[DubbingSegmentReport] = ()
    keep_original_audio: bool = True
    background_gain: float = 0.35
    copy_video: bool = False


@dataclass
class DraftResult:
    draft_path: str
    draft_name: str
    width: int
    height: int
    duration: float
    tracks: int
    captions: int
    dub_lines: int
    media_files: int
    registered: bool
    index_backup: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------
class _DraftWriter:
    """Builds one draft folder in a staging directory, then renames it."""

    def __init__(
        self,
        *,
        canvas: tuple[int, int],
        aspect: str,
        staging: Path,
        destination: Path,
        copy_video: bool = False,
        progress: ProgressFn | None = None,
    ) -> None:
        self.canvas = canvas
        self.aspect = aspect
        self.staging = staging
        self.destination = destination
        self.copy_video = copy_video
        self.progress = progress or (lambda message, fraction: None)
        self.warnings: list[str] = []
        self.materials: dict[str, list[dict[str, Any]]] = {key: [] for key in MATERIAL_KEYS}
        self.tracks: list[dict[str, Any]] = []
        self.captions: list[tuple[float, float, str]] = []
        self.dub_lines = 0
        self._assets: dict[tuple[str, bool], tuple[Path, Path]] = {}
        self._cache: dict[tuple[str, str], tuple[dict[str, Any], Any]] = {}
        (staging / "media").mkdir(parents=True, exist_ok=True)
        (staging / "Timelines").mkdir(parents=True, exist_ok=True)

    # -- warnings ----------------------------------------------------------
    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    # -- media -------------------------------------------------------------
    def _asset(self, source: str | Path, *, audio: bool = False) -> tuple[Path, Path]:
        """Copy (or extract) a file into ``media/`` and return both paths."""
        path = Path(source)
        if not path.is_file():
            raise DraftError(f"Không tìm thấy tệp media: {path}")
        extract = audio and path.suffix.lower() not in AUDIO_SUFFIXES
        key = (str(path.resolve()), extract)
        if key in self._assets:
            return self._assets[key]

        if not extract and not audio and not self.copy_video:
            # The clip is referenced where it already lives: copying a feature
            # episode would double the disk use for nothing.
            self._assets[key] = (path, path)
            return self._assets[key]

        name = f"{uuid4().hex[:12]}{'.wav' if extract else path.suffix.lower()}"
        local = self.staging / "media" / name
        if extract:
            # CapCut reads a real audio file: a WAV inside a video container is
            # not one, so the soundtrack is decoded once.
            media_processor.run_ffmpeg(["-i", str(path), "-vn", "-c:a", "pcm_s16le", str(local)])
        else:
            shutil.copyfile(path, local)
        self._assets[key] = (local, self.destination / "media" / name)
        return self._assets[key]

    def _video_material(self, source: str | Path) -> tuple[dict[str, Any], media_processor.VideoInfo]:
        path = Path(source).resolve()
        key = (str(path), "video")
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]

        self.progress(f"Chuẩn bị media: {path.name}", 0.15)
        local, final = self._asset(path)
        info = media_processor.probe_video(path)
        material = {
            "id": _id(),
            "unique_id": uuid4().hex,
            "type": "video",
            "duration": _us(info.duration),
            "path": final.as_posix(),
            "media_path": "",
            "local_id": "",
            "has_audio": info.has_audio,
            "width": info.width,
            "height": info.height,
            "category_id": "",
            "category_name": "local",
            "material_name": path.name,
            "material_id": "",
            "material_url": "",
            "crop": {
                "upper_left_x": 0.0,
                "upper_left_y": 0.0,
                "upper_right_x": 1.0,
                "upper_right_y": 0.0,
                "lower_left_x": 0.0,
                "lower_left_y": 1.0,
                "lower_right_x": 1.0,
                "lower_right_y": 1.0,
            },
            "crop_ratio": "free",
            "crop_scale": 1.0,
            "audio_fade": None,
            "extra_type_option": 0,
            "source": 0,
            "source_platform": 0,
            "formula_id": "",
            "check_flag": 62977983,
            "local_material_id": str(uuid4()),
            "origin_material_id": "",
            "request_id": "",
            "has_sound_separated": False,
            "is_text_edit_overdub": False,
            "is_ai_generate_content": False,
            "aigc_type": "none",
            "is_copyright": False,
            "aigc_history_id": "",
            "aigc_item_id": "",
            "local_material_from": "",
        }
        self.materials["videos"].append(material)
        self._cache[key] = (material, info)
        return material, info

    def _audio_material(self, source: str | Path) -> tuple[dict[str, Any], media_processor.AudioInfo]:
        path = Path(source).resolve()
        key = (str(path), "audio")
        if key in self._cache:
            return self._cache[key]  # type: ignore[return-value]

        local, final = self._asset(path, audio=True)
        info = media_processor.probe_audio(local)
        material = {
            "id": _id(),
            "unique_id": "",
            "type": "extract_music",
            "name": path.name,
            "duration": _us(info.duration),
            "path": final.as_posix(),
            "category_name": "local",
            "wave_points": [],
            "music_id": "",
            "app_id": 0,
            "text_id": "",
            "tone_type": "",
            "source_platform": 0,
            "video_id": "",
            "effect_id": "",
            "resource_id": "",
            "third_resource_id": "",
            "category_id": "",
            "intensifies_path": "",
            "formula_id": "",
            "check_flag": 1,
            "team_id": "",
            "local_material_id": str(uuid4()),
            "tone_speaker": "",
            "mock_tone_speaker": "",
            "tone_platform": "sami",
            "cloned_model_type": "",
            "request_id": "",
            "source_from": "",
            "is_ugc": False,
            "is_ai_clone_tone": False,
            "copyright_limit_type": "none",
            "aigc_history_id": "",
            "aigc_item_id": "",
            "music_source": "",
        }
        material["music_id"] = material["id"]
        self.materials["audios"].append(material)
        self._cache[key] = (material, info)
        return material, info

    # -- segment scaffolding ----------------------------------------------
    def _refs(self, *, beats: bool = False, canvas: bool = True) -> list[str]:
        """The support materials every segment points at.

        CapCut expects the speed, the sound channel mapping and the vocal
        separation even when nothing was separated: they are what its panels
        read when the clip is selected.
        """
        canvases = {"id": _id(), "type": "canvas_color", "color": "", "blur": 0.0, "image": "",
                    "album_image": "", "image_id": "", "image_name": "", "source_platform": 0,
                    "team_id": ""}
        speed = {"id": _id(), "type": "speed", "mode": 0, "speed": 1.0, "curve_speed": None}
        channels = {"id": _id(), "type": "", "audio_channel_mapping": 0, "is_config_open": False}
        vocal = {"id": _id(), "type": "vocal_separation", "choice": 0, "removed_sounds": [],
                 "time_range": None, "production_path": "", "final_algorithm": "", "enter_from": ""}
        if canvas:
            self.materials["canvases"].append(canvases)
        self.materials["speeds"].append(speed)
        self.materials["sound_channel_mappings"].append(channels)
        self.materials["vocal_separations"].append(vocal)
        refs = [speed["id"], channels["id"], vocal["id"]]
        if canvas:
            refs.insert(1, canvases["id"])
        if beats:
            beat = {"id": _id(), "type": "beats", "enable_ai_beats": False, "gear": 404,
                    "gear_count": 0, "mode": 404, "user_beats": [], "user_delete_ai_beats": None,
                    "ai_beats": {"melody_url": "", "melody_path": "", "beats_url": "",
                                 "beats_path": "", "melody_percents": [0.0], "beat_speed_infos": []}}
            self.materials["beats"].append(beat)
            refs.append(beat["id"])
        return refs

    def _base_segment(
        self, *, start: float, duration: float, volume: float, refs: list[str]
    ) -> dict[str, Any]:
        return {
            "id": _id(),
            "source_timerange": _range(0, duration),
            "target_timerange": _range(start, duration),
            "render_timerange": _range(0, 0),
            "desc": "",
            "state": 0,
            "speed": 1.0,
            "is_loop": False,
            "is_tone_modify": False,
            "reverse": False,
            "intensifies_audio": False,
            "cartoon": False,
            "volume": volume,
            "last_nonzero_volume": volume or 1.0,
            "clip": {
                "scale": {"x": 1.0, "y": 1.0},
                "rotation": 0.0,
                "rotation3d": [],
                "transform": {"x": 0.0, "y": 0.0},
                "flip": {"vertical": False, "horizontal": False},
                "alpha": 1.0,
            },
            "uniform_scale": {"on": True, "value": 1.0},
            "material_id": "",
            "extra_material_refs": refs,
            "render_index": 0,
            "keyframe_refs": [],
            "enable_lut": True,
            "enable_adjust": True,
            "enable_hsl": False,
            "visible": True,
            "group_id": "",
            "enable_color_curves": True,
            "enable_hsl_curves": True,
            "track_render_index": 0,
            "hdr_settings": {"mode": 1, "intensity": 1.0, "nits": 1000},
            "hdr_vivid_settings": None,
            "enable_color_wheels": True,
            "track_attribute": 0,
            "is_placeholder": False,
            "template_id": "",
            "enable_smart_color_adjust": False,
            "template_scene": "default",
            "common_keyframes": [],
            "caption_info": None,
            "responsive_layout": {"enable": False, "target_follow": "", "size_layout": 0,
                                  "horizontal_pos_layout": 0, "vertical_pos_layout": 0},
            "enable_color_match_adjust": False,
            "enable_color_correct_adjust": False,
            "enable_adjust_mask": False,
            "raw_segment_id": "",
            "lyric_keyframes": None,
            "enable_video_mask": False,
            "digital_human_template_group_id": "",
            "color_correct_alg_result": "",
            "source": "segmentsourcenormal",
            "enable_mask_stroke": False,
            "enable_mask_shadow": False,
            "enable_color_adjust_pro": False,
            "segment_color_tag": "",
        }

    def _audio_segment(self, material: dict[str, Any], *, start: float, duration: float,
                       source: float, volume: float) -> dict[str, Any]:
        segment = self._base_segment(start=start, duration=duration, volume=volume,
                                     refs=self._refs(beats=True, canvas=False))
        segment["source_timerange"] = _range(source, duration)
        segment["material_id"] = material["id"]
        segment["clip"] = None
        segment["uniform_scale"] = None
        segment["enable_lut"] = False
        segment["enable_adjust"] = False
        segment["enable_video_mask"] = True
        segment["hdr_settings"] = None
        return segment

    # -- tracks ------------------------------------------------------------
    def _track(self, name: str, kind: str, segments: list[dict[str, Any]]) -> None:
        """One logical track, spread over as many lanes as the overlaps need."""
        if not segments:
            return
        lanes: list[list[dict[str, Any]]] = []
        for segment in sorted(segments, key=lambda item: item["target_timerange"]["start"]):
            start = segment["target_timerange"]["start"]
            for lane in lanes:
                tail = lane[-1]["target_timerange"]
                if tail["start"] + tail["duration"] <= start:
                    lane.append(segment)
                    break
            else:
                lanes.append([segment])

        if len(lanes) > 1:
            self.warn(f"{name}: {len(lanes)} đoạn chồng thời gian được tách thành {len(lanes)} track.")

        for index, lane in enumerate(lanes):
            render = len(self.tracks)
            for segment in lane:
                segment["track_render_index"] = render
                segment["render_index"] = 14000 + render if kind == "text" else render * 1000
            self.tracks.append(
                {
                    "id": _id(),
                    "type": kind,
                    "name": name if not index else f"{name} {index + 1}",
                    "segments": lane,
                    "flag": 0,
                    "attribute": 0,
                    "is_default_name": False,
                }
            )

    # -- video -------------------------------------------------------------
    def add_source(self, path: str, *, volume: float) -> float:
        material, info = self._video_material(path)
        width, height = self.canvas
        if info.width and info.height:
            fit = min(width / info.width, height / info.height)
            fill = max(width / info.width, height / info.height)
            scale = fill / fit
        else:
            scale = 1.0

        duration = info.duration or media_processor.probe_duration(path)
        segment = self._base_segment(start=0.0, duration=duration, volume=volume, refs=self._refs())
        segment["material_id"] = material["id"]
        segment["clip"]["scale"] = {"x": round(scale, 6), "y": round(scale, 6)}
        segment["uniform_scale"] = {"on": True, "value": 1.0}
        self._track("Video 1", "video", [segment])
        return duration

    # -- dub ---------------------------------------------------------------
    def add_dub(self, *, track_path: str | None, segments: Sequence[DubbingSegmentReport],
                volume: float) -> None:
        takes: list[dict[str, Any]] = []
        if segments:
            for report in segments:
                clip = report.clip_path
                if not clip or not Path(clip).is_file():
                    self.warn(f"Câu {report.index}: thiếu tệp lồng tiếng, câu này không sang CapCut.")
                    continue
                material, info = self._audio_material(clip)
                takes.append(
                    self._audio_segment(
                        material,
                        start=report.start,
                        duration=info.duration,
                        source=0.0,
                        volume=volume,
                    )
                )
        elif track_path and Path(track_path).is_file():
            material, info = self._audio_material(track_path)
            takes.append(
                self._audio_segment(material, start=0.0, duration=info.duration, source=0.0, volume=volume)
            )
        if not takes:
            if track_path or segments:
                self.warn("Không dựng được track lồng tiếng: chưa có tệp thoại nào trên đĩa.")
            return
        self.dub_lines = len(takes)
        self._track("Lồng tiếng", "audio", takes)

    # -- captions ----------------------------------------------------------
    def add_captions(self, segments: Sequence[SubtitleSegment], style: SubtitleStyle,
                     use_translation: bool, track_name: str = "Phụ đề") -> None:
        ordered = dedupe_overlaps([segment.model_copy(deep=True) for segment in segments], min_gap=0.001)
        texts: list[dict[str, Any]] = []
        for segment in ordered:
            raw = segment.translation if use_translation and segment.translation else segment.text
            text = clean_text(raw or "")
            if not text:
                continue
            body = "\n".join(line for line in wrap_text(text) if line)
            duration = max(segment.end - segment.start, 0.2)
            texts.append(self._text_segment(body, segment.start, duration, style))
            self.captions.append((segment.start, segment.end, text))
        self._track(track_name, "text", texts)

    def _text_segment(self, text: str, start: float, duration: float, style: SubtitleStyle) -> dict[str, Any]:
        width, height = self.canvas
        size, pixels = text_size(style, width, height)
        lines = text.count("\n") + 1
        longest = max(text.split("\n"), key=len) if text else ""
        offset_x, offset_y = text_offset(style, width, height, pixels, lines, text_width_px(longest, pixels))

        content: dict[str, Any] = {
            "text": text,
            "styles": [
                {
                    "range": [0, len(text.encode("utf-16-le")) // 2],
                    "size": size,
                    "fill": {
                        "alpha": 1.0,
                        "content": {
                            "render_type": "solid",
                            "solid": {"alpha": 1.0, "color": _rgb(style.color)},
                        },
                    },
                }
            ],
        }
        path = font_path(style.font, style.bold)
        if not path:
            self.warn(f"Không tìm thấy font '{style.font}' trên máy; chọn lại font trong CapCut.")
        content["styles"][0]["font"] = {"id": "", "path": path}

        material: dict[str, Any] = {
            "id": _id(),
            "type": "text",
            "name": "",
            "content": "",
            "font_size": size,
            "alignment": {"left": 0, "center": 1, "right": 2}[style.horizontal],
            "check_flag": 7,
            "font_name": style.font,
            "font_path": path,
            "font_id": "",
            "text_color": style.color,
            "line_spacing": 0.02,
            "letter_spacing": 0.0,
            "global_alpha": 1.0,
            "typesetting": 0,
            "is_rich_text": True,
            "text_size": 30,
            "fixed_width": -1,
            "fixed_height": -1,
            "line_feed": 1,
            "line_max_width": 0.82,
            "force_apply_line_max_width": False,
            "fonts": [],
            "sub_type": 5,
            "bold_width": 0.0,
            "italic_degree": 0,
            "underline": False,
        }

        if style.outline > 0:
            edge = (pixels * 0.09 * max(style.outline, 1.0)) if style.background else (
                pixels / 18 * style.outline
            )
            # The native width is relative to the glyph, not to the frame.
            content["styles"][0]["strokes"] = [
                {
                    "width": min(0.2, max(edge, 1.0) / pixels),
                    "content": {"solid": {"alpha": 1.0, "color": _rgb(style.outline_color)}},
                }
            ]
            material["check_flag"] |= 8

        if style.background and style.background_opacity > 0:
            material.update(
                background_style=1,
                background_color=style.background_color,
                background_alpha=style.background_opacity,
                background_round_radius=0.0,
                background_width=0.14,
                background_height=0.14,
                background_horizontal_offset=0.0,
                background_vertical_offset=0.0,
                background_fill="",
            )
            material["check_flag"] |= 16

        material["content"] = json.dumps(content, ensure_ascii=False)
        self.materials["texts"].append(material)

        animation = {"id": _id(), "type": "sticker_animation", "animations": [],
                     "multi_language_current": "none"}
        self.materials["material_animations"].append(animation)

        segment = self._base_segment(start=start, duration=duration, volume=1.0, refs=[animation["id"]])
        segment["material_id"] = material["id"]
        segment["source_timerange"] = None
        segment["clip"]["transform"] = {"x": round(offset_x, 6), "y": round(offset_y, 6)}
        segment["enable_lut"] = False
        segment["enable_adjust"] = False
        segment["enable_video_mask"] = True
        segment["hdr_settings"] = None
        return segment

    # -- files -------------------------------------------------------------
    def write(self, *, name: str, fps: float, duration: float) -> None:
        now = time.time_ns() // 1000
        timeline_id = _id()
        draft_id = _id()
        width, height = self.canvas
        platform = {
            "app_id": APP_ID,
            "app_source": "cc",
            "app_version": "9.7.0",
            "os": "windows",
            "os_version": "10",
            "device_id": "",
            "hard_disk_id": "",
            "mac_address": "",
        }
        draft = {
            "id": timeline_id,
            "version": DRAFT_VERSION,
            "new_version": NEW_VERSION,
            "name": name,
            "duration": _us(duration),
            "create_time": now,
            "update_time": now,
            "fps": round(fps, 2) or 30.0,
            "canvas_config": {"ratio": self.aspect, "width": width, "height": height, "background": None},
            "config": {"maintrack_adsorb": False, "subtitle_sync": False, "use_float_render": False},
            "tracks": self.tracks,
            "materials": self.materials,
            "keyframes": {},
            "keyframe_graph_list": [],
            "platform": platform,
            "last_modified_platform": platform,
            "color_space": 0,
            "relationships": [],
            "group_container": None,
            "mutable_config": None,
            "mixed_track_mode_on": False,
            "render_index_track_mode_on": False,
            "free_render_index_mode_on": False,
        }
        meta = {
            "draft_id": draft_id,
            "draft_name": name,
            "draft_fold_path": self.destination.as_posix(),
            "draft_root_path": self.destination.parent.as_posix(),
            "draft_materials": [],
            "draft_cover": "",
            "draft_is_invisible": False,
            "draft_is_cloud_temp_draft": False,
            "draft_new_version": "",
            "draft_type": "",
            "draft_timeline_materials_size_": 0,
            "tm_draft_create": now,
            "tm_draft_modified": now,
            "tm_draft_removed": 0,
            "tm_duration": _us(duration),
        }
        project = {
            "id": draft_id,
            "version": 0,
            "main_timeline_id": timeline_id,
            "create_time": now,
            "update_time": now,
            "timelines": [
                {
                    "id": timeline_id,
                    "name": "Timeline 01",
                    "create_time": now,
                    "update_time": now,
                    "is_marked_delete": False,
                }
            ],
            "config": {"color_space": -1, "use_float_render": False},
        }

        timeline = f"Timelines/{timeline_id}"
        for filename in (
            "draft_content.json",
            "draft_content.json.bak",
            "template-2.tmp",
            f"{timeline}/draft_content.json",
            f"{timeline}/draft_content.json.bak",
            f"{timeline}/template.tmp",
            f"{timeline}/template-2.tmp",
        ):
            self._json(filename, draft)
        self._json("draft_meta_info.json", meta)
        self._json("Timelines/project.json", project)
        self._json("Timelines/project.json.bak", project)
        self._json(
            "shortvideoai.json",
            {
                "format": "shortvideoai-capcut-draft",
                "version": 1,
                "canvas": {"width": width, "height": height, "aspect": self.aspect},
                "warnings": self.warnings,
            },
        )
        (self.staging / "README.txt").write_text(self._readme(name, draft_id), encoding="utf-8")

    def _json(self, name: str, data: Any) -> None:
        path = self.staging / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    def _readme(self, name: str, draft_id: str) -> str:
        lines = [
            f"Dự án CapCut: {name}",
            f"draft_id: {draft_id}",
            "",
            "Thư mục này do ShortVideoAI tạo. Mở CapCut, dự án nằm trong danh sách",
            "ở màn hình chính; mọi thứ đều sửa được: video, phụ đề, lồng tiếng.",
            "",
            "Nếu dự án không xuất hiện: đóng CapCut rồi xuất lại, hoặc copy cả thư mục",
            "này vào %LOCALAPPDATA%\\CapCut\\User Data\\Projects\\com.lveditor.draft",
            "và thêm một mục vào root_meta_info.json.",
        ]
        if self.warnings:
            lines.extend(["", "Lưu ý:"] + [f"- {warning}" for warning in self.warnings])
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
def _safe_folder(name: str) -> str:
    cleaned = "".join(" " if char in '<>:"/\\|?*' or ord(char) < 32 else char for char in name)
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:60] or "ShortVideoAI"


def _unique_destination(root: Path, name: str) -> Path:
    candidate = root / name
    index = 2
    while candidate.exists():
        candidate = root / f"{name} {index}"
        index += 1
    return candidate


def register_draft(root: Path, destination: Path) -> str | None:
    """Add the new draft to CapCut's project index, keeping an exact backup.

    CapCut owns ``root_meta_info.json`` and rewrites it on exit, so the entry is
    only appended when the file did not change while the draft was written.
    """
    index = root / "root_meta_info.json"
    original = index.read_bytes() if index.exists() else None
    if original is None:
        return None
    data = json.loads(original.decode("utf-8-sig"))
    entries = data.get("all_draft_store")
    if not isinstance(entries, list):
        raise DraftError("root_meta_info.json của CapCut có cấu trúc lạ; draft vẫn nằm nguyên trên đĩa.")

    meta = json.loads((destination / "draft_meta_info.json").read_text(encoding="utf-8"))
    if any(entry.get("draft_id") == meta["draft_id"] for entry in entries):
        return None

    backup_dir = get_settings().cache_path
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"capcut_index_before_{meta['draft_id']}.bak"
    backup.write_bytes(original)

    entry = {
        "draft_id": meta["draft_id"],
        "draft_name": meta["draft_name"],
        "draft_fold_path": meta["draft_fold_path"],
        "draft_root_path": meta["draft_root_path"],
        "draft_json_file": (destination / "draft_content.json").as_posix(),
        "draft_cover": "",
        "draft_new_version": "",
        "draft_type": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_infinite_canvas_draft": False,
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_timeline_materials_size": 0,
        "draft_web_article_video_enter_from": "",
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "pippit_avatar_url": "",
        "pippit_extra_info": "",
        "pippit_id": "",
        "pippit_user_name": "",
        "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": meta["tm_draft_create"],
        "tm_draft_modified": meta["tm_draft_modified"],
        "tm_draft_removed": 0,
        "tm_duration": meta["tm_duration"],
    }
    entries.append(entry)
    draft_ids = data.get("draft_ids")
    data["draft_ids"] = max((draft_ids if isinstance(draft_ids, int) else 0) + 1, len(entries))

    pending = root / f".shortvideoai-index-{uuid4().hex}.tmp"
    try:
        pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if (index.read_bytes() if index.exists() else None) != original:
            raise DraftError("CapCut đã ghi lại danh sách dự án trong lúc xuất; draft vẫn nằm nguyên trên đĩa.")
        os.replace(pending, index)
    finally:
        pending.unlink(missing_ok=True)
    return str(backup)


def export(
    spec: DraftSpec,
    *,
    root: Path | None = None,
    name: str | None = None,
    register: bool = True,
    progress: ProgressFn | None = None,
) -> DraftResult:
    """Write ``spec`` into a new draft folder and return what was produced."""
    report = progress or (lambda message, fraction: None)
    source = Path(spec.video_path)
    if not source.is_file():
        raise DraftError(f"Không tìm thấy video nguồn: {source}")

    canvas = CANVAS.get(spec.aspect, CANVAS["9:16"])
    folder = root or drafts_root() or (get_settings().data_path / "exports" / "capcut")
    folder.mkdir(parents=True, exist_ok=True)

    draft_name = _safe_folder(name or source.stem or "ShortVideoAI")
    destination = _unique_destination(folder, draft_name)
    staging = Path(tempfile.mkdtemp(prefix=".shortvideoai-draft-", dir=folder))

    report("Đọc thông tin video", 0.05)
    info = media_processor.probe_video(source)
    writer = _DraftWriter(
        canvas=canvas,
        aspect=spec.aspect,
        staging=staging,
        destination=destination,
        copy_video=spec.copy_video,
        progress=report,
    )
    try:
        has_dub = bool(spec.dub_segments) or bool(spec.dub_track_path)
        if has_dub:
            volume = float(min(max(spec.background_gain, 0.0), 1.0)) if spec.keep_original_audio else 0.0
        else:
            volume = 1.0
        report("Dựng track video", 0.2)
        duration = writer.add_source(str(source), volume=volume)
        report("Dựng track lồng tiếng", 0.45)
        writer.add_dub(track_path=spec.dub_track_path, segments=spec.dub_segments, volume=1.0)
        report("Dựng phụ đề", 0.65)
        if spec.presentation_mode != "none":
            writer.add_captions(spec.segments, spec.style, spec.use_translation, track_name="Phụ đề")
            if spec.presentation_mode == "bilingual":
                sec_style = spec.secondary_style or spec.style.model_copy(
                    update={"margin_v": min(spec.style.margin_v + 9, 45), "size": max(spec.style.size * 0.8, 0.5)}
                )
                sec_segs = spec.secondary_segments or spec.segments
                use_trans_sec = False if not spec.secondary_segments else True
                writer.add_captions(sec_segs, sec_style, use_trans_sec, track_name="Phụ đề 2 (TS2)")
            if not spec.segments:
                writer.warn("Chưa có phụ đề nào nên draft chỉ có video và lồng tiếng.")

        report("Ghi draft_content.json", 0.85)
        writer.write(name=destination.name, fps=info.fps or 30.0, duration=duration)
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    registered = False
    backup: str | None = None
    if register and (folder / "root_meta_info.json").is_file():
        if capcut_running():
            writer.warn("CapCut đang mở: hãy đóng CapCut rồi mở lại để thấy dự án mới trong danh sách.")
        backup = register_draft(folder, destination)
        registered = True

    media_files = sum(1 for entry in writer.materials["videos"] + writer.materials["audios"] if entry.get("path"))
    report("Xong", 1.0)
    log.info("capcut draft written: %s (%s tracks)", destination, len(writer.tracks))
    return DraftResult(
        draft_path=str(destination),
        draft_name=destination.name,
        width=canvas[0],
        height=canvas[1],
        duration=round(duration, 3),
        tracks=len(writer.tracks),
        captions=len(writer.captions),
        dub_lines=writer.dub_lines,
        media_files=media_files,
        registered=registered,
        index_backup=backup,
        warnings=writer.warnings,
    )


__all__ = [
    "CANVAS",
    "DraftError",
    "DraftResult",
    "DraftSpec",
    "Targets",
    "capcut_running",
    "drafts_root",
    "export",
    "font_path",
    "index_entries",
    "installed_version",
    "register_draft",
    "targets",
    "text_offset",
    "text_size",
    "text_width_px",
]
