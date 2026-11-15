"""Subtitle extraction by OCR over sampled video frames.

Whisper hears the mix; hard-burned subtitles are *drawn* on it. For a clip
where the original subtitles are baked into the picture (and the whisper pass
starts from a music-heavy mix), reading the pixels is more faithful than
listening, so this is offered as an extraction provider next to the speech
engines.

The flow is deliberately boring:

1. ffmpeg samples the subtitle band (the bottom third by default) at
   ``SVA_OCR_FPS`` frames per second into greyscale PNGs - one process for the
   whole video, never one seek per frame.
2. A local OCR engine reads every frame (``rapidocr``, ``paddleocr`` or
   ``easyocr``; the first one installed wins unless configured).
3. Consecutive frames carrying the same text become one cue. A line has to be
   seen twice before it is trusted, which is what removes watermarks, channel
   logos and transition flashes in a single pass.

Timings are frame-accurate to one sample interval, so the merge step centers
each frame on its own timestamp (``(n - 0.5) / fps``).

A line that still came out wrong can be read again on its own
(:func:`read_cue`): the same crop and the same engine, one to three frames
inside that cue's window, no merging and no writing - the UI asks the user
before the new text replaces the old one.
"""

from __future__ import annotations

import logging
import re
import shutil
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings, get_settings
from app.core.progress import NULL_REPORTER, Reporter
from app.services import media_processor, provider_config

log = logging.getLogger(__name__)

ENGINES = ("rapidocr", "paddleocr", "easyocr")
# Speed/accuracy profiles, mirroring the reference project's two cards:
# "PP-OCRv6 Small - Chính xác" and "PP-OCRv6 Tiny Det - Nhanh/chuẩn".
PROFILES = ("small", "tiny")
# Where the frame reader runs. ``auto`` means "the GPU if this onnxruntime build
# has one, CPU otherwise"; ``gpu`` still degrades to CPU rather than failing a
# job when the build or its CUDA runtime is missing.
DEVICES = ("auto", "gpu", "cpu")
# Detector / recogniser size per profile. Detection is the expensive half, so
# "tiny" shrinks only the detector and keeps the stronger recogniser: halving
# the recogniser costs accuracy on diacritics, which is exactly what a
# Vietnamese subtitle cannot afford.
PROFILE_MODELS = {"small": ("SMALL", "SMALL"), "tiny": ("TINY", "SMALL")}
# PP-OCRv6 dropped Korean; that language stays on the v5 mobile pair instead of
# silently falling back to another engine.
_V5_LANGS = frozenset({"korean"})
# Three samples a second: a one-second cue then lands on three frames, so a
# sub-second line still survives the two-frame trust rule.
DEFAULT_FPS = 3.0
# Vertical dramas draw the subtitle in the bottom third, and its accents sit a
# few percent above that: a band that starts at 66% clips the diacritics off
# the first line ("thể" reads as "th"), so the scan starts at 55% instead.
DEFAULT_BAND = 0.45
MIN_CONFIDENCE = 0.55
# Two samples of the same cue must overlap this much to be the same line.
# Not 1.0: one dropped diacritic ("chao" for "chào") is 0.86, and that is the
# same line, while a following cue is nothing like this one.
SIMILARITY = 0.82
# A cue may miss this many seconds of samples and still continue (fades,
# half-second overlay animations).
GRACE = 0.75
MIN_FRAMES = 2
BAND_WIDTH = 1280

_SPACE_RE = re.compile(r"\s+")
_SPACE_ALL_RE = re.compile(r"[\s\u200b\ufeff]+")
# OCR regularly hallucinates stray glyphs around a line; they never form words.
_SYMBOL_ONLY_RE = re.compile(r"^[^\wÀ-ỹ]+$", re.UNICODE)
# Letters of the CJK scripts: kana, Han and Hangul, in the ranges the readers
# actually return.
_CJK_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

_LANGS: dict[str, dict[str, Any]] = {
    # rapidocr v3 resolves the detector *and* the recogniser from one language
    # token and ships a Vietnamese model, so "vi" is a first-class choice.
    "auto": {"rapidocr": "ch", "paddleocr": "ch", "easyocr": ("vi", "en")},
    "zh": {"rapidocr": "ch", "paddleocr": "ch", "easyocr": ("ch_sim", "en")},
    "ch": {"rapidocr": "ch", "paddleocr": "ch", "easyocr": ("ch_sim", "en")},
    "en": {"rapidocr": "en", "paddleocr": "en", "easyocr": ("en",)},
    "vi": {"rapidocr": "vi", "paddleocr": "vi", "easyocr": ("vi", "en")},
    "latin": {"rapidocr": "vi", "paddleocr": "vi", "easyocr": ("vi", "en")},
    "ja": {"rapidocr": "japan", "paddleocr": "japan", "easyocr": ("ja", "en")},
    "ko": {"rapidocr": "korean", "paddleocr": "korean", "easyocr": ("ko", "en")},
}
# Recogniser tokens that only read CJK scripts.
_CJK_MODELS = frozenset({"ch", "japan", "korean"})


class OcrError(RuntimeError):
    """Raised when the OCR provider cannot run."""


class _Reader(Protocol):
    def __call__(self, image: Path) -> list[str]:
        """Return the text lines found in one frame, top to bottom."""


# ---------------------------------------------------------------------------
# Frame sampling
# ---------------------------------------------------------------------------
def _crop(position: str, band: float) -> str:
    """ffmpeg crop expression for the part of the frame a subtitle lives in."""
    if position == "full":
        return ""
    share = min(max(band, 0.1), 1.0)
    if position == "top":
        return f"crop=iw:ih*{share:.3f}:0:0"
    return f"crop=iw:ih*{share:.3f}:0:ih*{1 - share:.3f}"


def _region_crop(region: tuple[float, float, float, float]) -> str:
    """ffmpeg crop for ``left, top, right, bottom`` in percent of the frame.

    Scanning the whole frame reads whatever else is drawn on it - a channel bug,
    a logo, a "follow for part 2" banner - and those become subtitle lines. The
    rectangle is what keeps only the cue in front of the reader.
    """
    left, top, right, bottom = region
    if (left, top, right, bottom) == (0.0, 0.0, 100.0, 100.0):
        return ""  # nothing to cut
    width = max(right - left, 1.0)
    height = max(bottom - top, 1.0)
    return (
        f"crop=iw*{width / 100:.4f}:ih*{height / 100:.4f}"
        f":iw*{left / 100:.4f}:ih*{top / 100:.4f}"
    )


def sample_frames(
    video: Path,
    folder: Path,
    *,
    fps: float,
    position: str = "bottom",
    band: float = DEFAULT_BAND,
    region: tuple[float, float, float, float] | None = None,
) -> list[Path]:
    """Write one greyscale PNG per sample and return them in playing order."""
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("frame_*.png"):
        stale.unlink(missing_ok=True)

    filters = [f"fps={fps:.3f}"]
    # An explicit rectangle wins: it is the only way to leave a logo or a title
    # card out of the read while still covering the subtitle line.
    crop_filter = _region_crop(region) if region is not None else _crop(position, band)
    if crop_filter:
        filters.append(crop_filter)
    filters.append(f"scale={BAND_WIDTH}:-2:flags=lanczos")
    filters.append("format=gray")

    media_processor.run_ffmpeg(
        [
            "-i",
            str(video),
            "-an",
            "-sn",
            "-vf",
            ",".join(filters),
            "-q:v",
            "2",
            str(folder / "frame_%06d.png"),
        ]
    )
    frames = sorted(folder.glob("frame_*.png"))
    if not frames:
        raise OcrError("ffmpeg produced no frames - is this a video file?")
    return frames


def grab_frame(
    video: Path,
    target: Path,
    when: float,
    *,
    position: str = "bottom",
    band: float = DEFAULT_BAND,
    region: tuple[float, float, float, float] | None = None,
) -> Path:
    """Write the frame at ``when`` seconds, cropped and scaled like a sampled one.

    Extraction samples a whole clip in one ffmpeg run; a second look at a single
    cue is the opposite case, so this seeks instead. ``-ss`` before ``-i``
    decodes from the preceding keyframe and discards up to the timestamp: fast
    enough for one frame and still exact.
    """
    filters: list[str] = []
    crop_filter = _region_crop(region) if region is not None else _crop(position, band)
    if crop_filter:
        filters.append(crop_filter)
    filters.append(f"scale={BAND_WIDTH}:-2:flags=lanczos")
    filters.append("format=gray")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    media_processor.run_ffmpeg(
        [
            "-ss",
            f"{max(when, 0.0):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            ",".join(filters),
            str(target),
        ]
    )
    if not target.is_file():
        raise OcrError(f"ffmpeg produced no frame at {when:.2f}s")
    return target


def cue_offsets(start: float, end: float, samples: int = 3) -> list[float]:
    """Evenly spaced instants inside a cue, never on its edges.

    The first and last frames of a cue are usually mid-fade, with the line half
    drawn, and reading there loses a diacritic. Each sample sits in the middle
    of its slice of the window instead.
    """
    begin = max(float(start), 0.0)
    # A cue whose end came from a bad edit still deserves a readable window.
    finish = max(float(end), begin + 0.2)
    count = max(1, min(int(samples), 9))
    span = finish - begin
    return [round(begin + span * (index + 0.5) / count, 3) for index in range(count)]


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------
def _ordered(lines: list[tuple[Any, str, float]]) -> list[str]:
    """Sort detections the way they are read: by row, then left to right."""

    def origin(box: Any) -> tuple[float, float]:
        # A box may be a numpy array, an empty list or None: never test truthiness.
        points = list(box) if box is not None else []
        if not points:
            return 0.0, 0.0
        left = min(float(point[0]) for point in points)
        top = min(float(point[1]) for point in points)
        return top, left

    usable = [(origin(box), text, score) for box, text, score in lines if text and score >= MIN_CONFIDENCE]
    usable.sort(key=lambda item: (round(item[0][0] / 12), item[0][1]))
    return [text.strip() for _, text, _ in usable if text.strip() and not _SYMBOL_ONLY_RE.match(text.strip())]


def model_root(settings: Settings | None = None) -> Path:
    """Where the OCR onnx pairs live: ``<data>/models/ocr``."""
    settings = settings or get_settings()
    return settings.models_path / "ocr"


def managed_models(settings: Settings | None = None) -> Path | None:
    """The managed folder, but only once it actually holds weights.

    The small pair ships inside the rapidocr wheel; pointing rapidocr at an
    empty folder would re-download ~30 MB on the first run of a fresh install,
    so the bundled copy is used until the user installs something extra.
    """
    folder = model_root(settings)
    try:
        return folder if any(folder.glob("*.onnx")) else None
    except OSError:
        return None


# PP-OCRv6 has no tiny Japanese detector; the small one is still small.
_DET_WITHOUT_TINY = frozenset({"japan"})

# Providers that mean "this onnxruntime build can run the models on a GPU".
# CUDA comes from onnxruntime-gpu; DirectML is the Windows wheel that works on
# any DX12 card, NVIDIA or not.
GPU_PROVIDERS = ("CUDAExecutionProvider", "DmlExecutionProvider")
# One wheel at a time: all three install the same `onnxruntime` package, so the
# Settings panel offers them as an exclusive choice rather than as extras.
ONNX_PACKS: tuple[tuple[str, str, str], ...] = (
    ("onnxruntime", "CPU — mặc định, không cần driver", "pip install onnxruntime"),
    ("onnxruntime-directml", "GPU — mọi card DX12 (NVIDIA, AMD, Intel)", "pip install onnxruntime-directml"),
    ("onnxruntime-gpu", "GPU — NVIDIA + CUDA 12 + cuDNN 9", "pip install onnxruntime-gpu"),
)


def onnx_providers() -> list[str]:
    """Execution providers the installed onnxruntime exposes (empty if absent)."""
    # The Settings panel asks for this on every load and the answer cannot change
    # while the process lives: the provider list comes from the installed wheel.
    return _providers()


@lru_cache(maxsize=1)
def _providers() -> list[str]:
    try:
        import onnxruntime as ort  # noqa: PLC0415 - optional and heavy
    except ImportError:
        return []
    try:
        return [str(name) for name in ort.get_available_providers()]
    except Exception:  # noqa: BLE001 - a broken install must not break the panel
        return []


@lru_cache(maxsize=1)
def onnx_pack() -> str:
    """Which distribution is installed: ``onnxruntime``, ``-directml`` or ``-gpu``.

    Knowing the wheel - not just its providers - is what lets the panel say
    "install the GPU pack" instead of guessing, because replacing it is a
    *different pip command* rather than another requirement line.
    """
    if not _providers():
        return ""
    try:
        from importlib import metadata  # noqa: PLC0415
    except ImportError:  # pragma: no cover - always present on 3.12
        return ""
    for name, _label, _command in ONNX_PACKS:
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        return name
    return "onnxruntime"


def runtime_info() -> dict[str, Any]:
    """The installed ONNX Runtime wheel, its version and what it can do."""
    pack = onnx_pack()
    version = ""
    if pack:
        try:
            from importlib import metadata  # noqa: PLC0415

            version = metadata.version(pack)
        except Exception:  # noqa: BLE001 - metadata is a hint, never a requirement
            version = ""
    providers = onnx_providers()
    return {
        "installed": bool(providers),
        "distribution": pack,
        "version": version,
        "providers": providers,
        "gpu_ready": any(name in providers for name in GPU_PROVIDERS),
        # `gpu_ready` answers "can frames run on a GPU at all"; the provider list
        # says which SDK that is, so a CUDA user is not told to install DirectML.
        "gpu_provider": next((name for name in GPU_PROVIDERS if name in providers), ""),
        "packs": [
            {"name": name, "label": label, "command": command, "installed": name == pack}
            for name, label, command in ONNX_PACKS
        ],
    }


def resolve_device(choice: str) -> str:
    """``gpu`` only when a GPU provider is installed, ``cpu`` otherwise.

    ``gpu`` here means "try the GPU": the wheel can be present while its CUDA
    runtime is not, which only shows up when a session is created - that is why
    :func:`_rapidocr` warms the engine up and falls back on failure.
    """
    wanted = (choice or "auto").strip().lower()
    if wanted == "cpu":
        return "cpu"
    providers = onnx_providers()
    return "gpu" if any(name in providers for name in GPU_PROVIDERS) else "cpu"


def _rapidocr_params(
    language: str,
    profile: str,
    model_folder: Path | None,
    device: str = "cpu",
) -> dict[str, Any]:
    """Build the rapidocr parameter dict for one language + profile.

    ``model_type``/``ocr_version`` must be the package's enums, not strings:
    rapidocr validates that and raises ``TypeError`` otherwise.
    """
    from rapidocr.utils.typings import ModelType, OCRVersion  # noqa: PLC0415

    wanted = _LANGS.get(language, _LANGS["auto"])["rapidocr"]
    params: dict[str, Any] = {
        "Global.text_score": MIN_CONFIDENCE,
        # Most sampled frames carry no subtitle, and every empty frame logs a
        # warning that would drown the job log.
        "Global.log_level": "error",
    }
    if device == "gpu":
        # Ask only for the SDK this wheel actually ships: rapidocr logs an
        # "install onnxruntime-gpu" warning for every flag whose provider is
        # missing, and a DirectML user should not read about CUDA on every run.
        installed = onnx_providers()
        if "CUDAExecutionProvider" in installed:
            params |= {
                "EngineConfig.onnxruntime.use_cuda": True,
                "EngineConfig.onnxruntime.cuda_ep_cfg.device_id": 0,
            }
        if "DmlExecutionProvider" in installed:
            params["EngineConfig.onnxruntime.use_dml"] = True
    if model_folder is not None:
        params["Global.model_root_dir"] = str(model_folder)

    if wanted in _V5_LANGS:
        params |= {
            "Det.ocr_version": OCRVersion.PPOCRV5,
            "Det.lang_type": "multi",
            "Det.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.lang_type": wanted,
            "Rec.model_type": ModelType.MOBILE,
        }
        return params

    detector, recogniser = PROFILE_MODELS.get(profile, PROFILE_MODELS["small"])
    if detector == "TINY" and wanted in _DET_WITHOUT_TINY:
        detector = "SMALL"
    params |= {
        "Det.lang_type": wanted,
        "Rec.lang_type": wanted,
        "Det.model_type": getattr(ModelType, detector),
        "Rec.model_type": getattr(ModelType, recogniser),
    }
    return params


def _probe(engine: Any) -> None:
    """Run one blank frame so the model sessions - and the GPU provider - load now.

    rapidocr builds its sessions lazily on the first image, already deep into a
    job. Warming up here turns "onnxruntime-gpu is installed, its CUDA runtime
    is not" into a warning and a CPU retry instead of a half-finished
    extraction.
    """
    import numpy as np  # noqa: PLC0415 - only needed for the warm-up

    engine(np.full((64, 320, 3), 255, dtype=np.uint8))


def _active_providers(engine: Any) -> list[str]:
    """Which execution provider the detector session really landed on."""
    session = getattr(getattr(getattr(engine, "text_det", None), "session", None), "session", None)
    try:
        return [str(name) for name in session.get_providers()]
    except Exception:  # noqa: BLE001 - introspection only, never fatal
        return []


def _rapidocr(
    language: str,
    profile: str = "small",
    model_folder: Path | None = None,
    device: str = "cpu",
) -> tuple[_Reader, str]:
    """rapidocr v3 (multilingual models) or the legacy Chinese-only build.

    Returns the reader and the device it really ended up on: "gpu" only after a
    successful warm-up on a GPU provider.
    """
    wanted = _LANGS.get(language, _LANGS["auto"])["rapidocr"]
    used = "cpu"
    try:
        from rapidocr import RapidOCR  # noqa: PLC0415 - heavy optional import
    except ImportError:
        try:
            from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415 - older name
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise OcrError("rapidocr is not installed (pip install rapidocr)") from exc
        if wanted != "ch":
            # The legacy wheel ships the Chinese PP-OCRv4 pair only; pointing it
            # at Vietnamese silently drops every diacritic ("có" -> "c6").
            log.warning(
                "rapidocr-onnxruntime has Chinese models only - %s needs `pip install rapidocr`",
                language,
            )
        if device == "gpu":
            log.warning("rapidocr-onnxruntime is CPU-only - `pip install rapidocr` for GPU support")
        engine = RapidOCR(text_score=MIN_CONFIDENCE)
    else:

        def build(profile_name: str, device_name: str) -> Any:
            return RapidOCR(params=_rapidocr_params(language, profile_name, model_folder, device_name))

        try:
            engine = build(profile, device)
        except (ValueError, TypeError) as exc:
            # An unexpected model/language combination must not cost the whole
            # extraction: the accurate pair is always available.
            log.warning("rapidocr %s profile rejected (%s) - using small", profile, exc)
            engine = build("small", device)

        if device == "gpu":
            try:
                _probe(engine)
            except Exception as exc:  # noqa: BLE001 - missing cuDNN, bad device id, ...
                log.warning("rapidocr GPU unusable (%s) - reading frames on CPU", exc)
                engine = build(profile, "cpu")
            else:
                active = _active_providers(engine)
                used = "gpu" if any(name in active for name in GPU_PROVIDERS) else "cpu"
                if used == "cpu":
                    log.warning(
                        "rapidocr stayed on CPU (%s) - install onnxruntime-gpu or onnxruntime-directml",
                        ", ".join(active) or "no providers",
                    )

    def read(image: Path) -> list[str]:
        output = engine(str(image))
        # v3 answers with a result object, the legacy build with (rows, timing).
        txts = getattr(output, "txts", None)
        if txts is not None:
            # boxes/scores are numpy arrays: `or` would be ambiguous on them.
            boxes = getattr(output, "boxes", None)
            scores = getattr(output, "scores", None)
            if boxes is None or len(boxes) != len(txts):
                boxes = [(0, 0)] * len(txts)
            if scores is None or len(scores) != len(txts):
                scores = [1.0] * len(txts)
            return _ordered(list(zip(boxes, txts, scores, strict=False)))
        rows = output[0] if isinstance(output, tuple) else output
        detections = []
        for row in rows or []:
            if len(row) >= 3:
                detections.append((row[0], str(row[1]), float(row[2])))
        return _ordered(detections)

    return read, used


def _paddleocr(language: str, device: str = "cpu") -> tuple[_Reader, str]:
    try:
        from paddleocr import PaddleOCR  # noqa: PLC0415 - heavy optional import
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise OcrError("paddleocr is not installed (pip install paddleocr paddlepaddle)") from exc

    lang = _LANGS.get(language, _LANGS["auto"])["paddleocr"]
    used = "cpu"
    engine = None
    if device == "gpu":
        try:
            engine = PaddleOCR(lang=lang, use_angle_cls=False, show_log=False, use_gpu=True)
            used = "gpu"
        except Exception as exc:  # noqa: BLE001 - CPU-only paddlepaddle wheel
            log.warning("paddleocr GPU unusable (%s) - reading frames on CPU", exc)
    if engine is None:
        try:
            engine = PaddleOCR(lang=lang, use_angle_cls=False, show_log=False)
        except TypeError:  # pragma: no cover - newer builds dropped show_log
            engine = PaddleOCR(lang=lang)

    def read(image: Path) -> list[str]:
        result = engine.ocr(str(image), cls=False)
        page = result[0] if isinstance(result, list) and result else result
        detections = []
        for row in page or []:
            if isinstance(row, (list, tuple)) and len(row) > 1 and isinstance(row[1], (list, tuple)):
                detections.append((row[0], str(row[1][0]), float(row[1][1]) if len(row[1]) > 1 else 1.0))
        return _ordered(detections)

    return read, used


def _easyocr(language: str, device: str = "cpu") -> tuple[_Reader, str]:
    try:
        import easyocr  # noqa: PLC0415 - heavy optional import
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise OcrError("easyocr is not installed (pip install easyocr)") from exc

    languages = _LANGS.get(language, _LANGS["auto"])["easyocr"]
    used = "cpu"
    engine = None
    if device == "gpu":
        try:
            engine = easyocr.Reader(list(languages), gpu=True)
            used = "gpu"
        except Exception as exc:  # noqa: BLE001 - torch build without CUDA
            log.warning("easyocr GPU unusable (%s) - reading frames on CPU", exc)
    if engine is None:
        engine = easyocr.Reader(list(languages), gpu=False)

    def read(image: Path) -> list[str]:
        rows = engine.readtext(str(image))
        return _ordered([(box, str(text), float(score)) for box, text, score in rows])

    return read, used


@dataclass(frozen=True)
class Engine:
    """A loaded reader plus what it really runs on (the job log says which)."""

    name: str
    device: str  # "gpu" | "cpu"
    read: _Reader

    @property
    def label(self) -> str:
        return f"{self.name} · {'GPU' if self.device == 'gpu' else 'CPU'}"


def load_engine(
    name: str,
    *,
    language: str = "auto",
    profile: str = "small",
    model_folder: Path | None = None,
    device: str = "auto",
) -> Engine:
    """Build the requested engine, falling back to whatever is installed."""
    chosen = (name or "rapidocr").strip().lower()
    if chosen not in ENGINES:
        chosen = "rapidocr"
    order = [chosen, *[item for item in ENGINES if item != chosen]]
    wanted_device = resolve_device(device)

    problems: list[str] = []
    for candidate in order:
        try:
            if candidate == "rapidocr":
                reader, used = _rapidocr(language, profile, model_folder, wanted_device)
            elif candidate == "paddleocr":
                reader, used = _paddleocr(language, wanted_device)
            else:
                reader, used = _easyocr(language, wanted_device)
        except OcrError as exc:
            problems.append(str(exc))
            continue
        log.info("ocr engine: %s (%s · %s · %s)", candidate, language, profile, used)
        return Engine(name=candidate, device=used, read=reader)
    raise OcrError("No OCR engine available: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Frame -> cue merging
# ---------------------------------------------------------------------------
def normalize_text(value: str) -> str:
    return _SPACE_ALL_RE.sub("", (value or "").casefold())


def _bare(value: str) -> str:
    """The same text without diacritics ("ừm" -> "um")."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _similar(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Two readings of one cue may differ only in the diacritics the recogniser
    # dropped. That is one edit on a long line but most of a short word
    # ("ừm" vs "um" scores 0.67), so the deaccented forms are compared too.
    same = SequenceMatcher(None, a, b).ratio()
    return max(same, SequenceMatcher(None, _bare(a), _bare(b)).ratio())


def _wanted_cjk(language: str) -> bool:
    """True when the configured recogniser was built for a CJK script."""
    return _LANGS.get(language, _LANGS["auto"])["rapidocr"] in _CJK_MODELS


def _one_script(line: str, *, cjk: bool) -> str:
    """Drop the boxes a read picked up in the other script.

    The scanned band can carry more than the cue - a channel bug, a caption box
    with the same line in another language - and the reader hands them back as
    one string. A read that is written in a single script is left alone: the
    recognisers read Latin text too, and a line is worth more than no line.
    """
    runs: list[tuple[bool, str]] = []
    current: list[str] = []
    flag: bool | None = None
    for char in line or "":
        if _CJK_TEXT_RE.match(char):
            kind: bool | None = True
        elif char.isalpha():
            kind = False
        else:
            kind = None  # spaces, digits and punctuation belong to their run
        if kind is None:
            current.append(char)
            continue
        if flag is None:
            flag = kind
        elif kind != flag:
            runs.append((flag, "".join(current)))
            current, flag = [], kind
        current.append(char)
    if flag is None:
        # Only digits and punctuation: no script to judge the read by.
        return _SPACE_RE.sub(" ", "".join(current)).strip()
    runs.append((flag, "".join(current)))
    if all(is_cjk == runs[0][0] for is_cjk, _text in runs):
        return _SPACE_RE.sub(" ", line).strip()
    kept = "".join(text for is_cjk, text in runs if is_cjk == cjk)
    return _SPACE_RE.sub(" ", kept).strip()


@dataclass
class _Run:
    start: float
    last: float
    count: int
    text: str

    def extend(self, when: float, text: str) -> None:
        self.last = when
        self.count += 1
        # Subtitles are wrapped onto two lines, and the OCR of one frame
        # occasionally loses the second: keep the most complete reading.
        if len(text) > len(self.text):
            self.text = text


def merge_observations(
    observations: list[tuple[float, str]],
    *,
    fps: float,
    grace: float = GRACE,
    similarity: float = SIMILARITY,
    min_frames: int = MIN_FRAMES,
) -> list[tuple[float, float, str]]:
    """Turn per-frame readings into ``(start, end, text)`` cues."""
    step = 1.0 / max(fps, 0.1)
    runs: list[_Run] = []
    current: _Run | None = None

    for when, raw in observations:
        text = _SPACE_RE.sub(" ", str(raw or "")).strip()
        if not text:
            continue
        if current is not None and when - current.last <= step + grace and _similar(current.text, text) >= similarity:
            current.extend(when, text)
            continue
        if current is not None:
            runs.append(current)
        current = _Run(start=when, last=when, count=1, text=text)
    if current is not None:
        runs.append(current)

    cues: list[tuple[float, float, str]] = []
    for run in runs:
        if run.count < min_frames:
            continue
        start = max(run.start - step / 2, 0.0)
        end = run.last + step / 2
        cues.append((round(start, 3), round(end, 3), run.text))
    return cues


def _accents(value: str) -> int:
    """How many marks the text carries after dropping none of them."""
    return len(unicodedata.normalize("NFKD", value)) - len(value)


def _pick(group: list[str]) -> str:
    """The line a group stands for: seen most often, then the accented form.

    Two readings of one cue that differ only in diacritics are the same line and
    fold into one group. The recogniser *drops* marks far more often than it
    invents them, so when both forms were seen equally often the accented one
    is the one that was on screen.
    """
    counts = Counter(group)
    return max(counts, key=lambda text: (counts[text], _accents(text), len(text)))


def best_reading(
    readings: list[tuple[float, str]],
    *,
    similarity: float = SIMILARITY,
) -> tuple[str, list[str]]:
    """The line most of the frames agree on, plus every distinct reading.

    A second look reads two or three frames of one cue, and the recogniser
    regularly drops a diacritic or a wrapped second line on one of them. The
    majority wins; among equally seen readings the accented, longer line is the
    complete one.
    """
    groups: list[list[str]] = []
    for _when, raw in readings:
        text = _SPACE_RE.sub(" ", str(raw or "")).strip()
        if not text:
            continue
        for group in groups:
            if _similar(group[0], text) >= similarity:
                group.append(text)
                break
        else:
            groups.append([text])
    if not groups:
        return "", []

    # Most samples first, then the longest line: three reads of a one-word cue
    # beat a single frame that happened to read a banner instead.
    groups.sort(key=lambda group: (len(group), len(_pick(group))), reverse=True)
    candidates = [_pick(group) for group in groups]
    return candidates[0], candidates


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def transcribe(
    video: str | Path,
    *,
    work_dir: str | Path,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> list[tuple[float, float, str]]:
    """Read the burned-in subtitles of one video, returning ``(start, end, text)``."""
    settings = provider_config.overlay(settings or get_settings())
    source = Path(video)
    if not source.is_file():
        raise OcrError(f"Video not found: {source}")

    fps = float(settings.ocr_fps or DEFAULT_FPS)
    region = provider_config.resolved_region(settings)
    report(
        stage="frames",
        fraction=0.02,
        message=(
            f"sampling frames at {fps:g} fps · "
            f"x {region[0]:g}-{region[2]:g}% · y {region[1]:g}-{region[3]:g}%"
        ),
    )
    try:
        frames = sample_frames(
            source,
            Path(work_dir) / "frames",
            fps=fps,
            position=settings.ocr_position or "bottom",
            band=settings.ocr_band,
            region=region,
        )
    except media_processor.MediaError as exc:
        raise OcrError(str(exc)) from exc

    engine = load_engine(
        settings.ocr_engine,
        language=settings.ocr_language,
        profile=settings.ocr_profile,
        model_folder=managed_models(settings),
        device=settings.ocr_device,
    )
    report(stage="ocr", fraction=0.05, message=f"{len(frames)} frame(s) · {engine.label}")
    step = 1.0 / max(fps, 0.1)

    observations: list[tuple[float, str]] = []
    for index, frame in enumerate(frames, start=1):
        text = " ".join(engine.read(frame))
        if text:
            observations.append((round((index - 0.5) * step, 3), text))
        if index % 20 == 0 or index == len(frames):
            report(
                stage="ocr",
                fraction=0.05 + 0.85 * index / len(frames),
                message=f"OCR frame {index}/{len(frames)}",
            )

    cues = merge_observations(observations, fps=fps)
    report(stage="transcribe", fraction=0.95, message=f"OCR found {len(cues)} line(s)")
    return cues


# ---------------------------------------------------------------------------
# Several windows, one reader
# ---------------------------------------------------------------------------
# The gap pass hands the reader a handful of short windows in a single call:
# building the engine loads the ONNX sessions, which costs more than reading a
# frame, so it is done once for the whole pass.
GAP_SAMPLES = 3
# A hole is sampled at this spacing (capped at MAX_SAMPLES): one-second cues
# must land on two frames, or the agreement rule below throws them away.
GAP_STEP = 0.6
MAX_SAMPLES = 8
# A hole is only believed when this many of its frames read the same line. One
# frame alone picks up decoration, logos and half-drawn fades often enough that
# a single read is not worth a subtitle line.
MIN_AGREEMENT = 2
# Nothing read for this many windows in a row means the picture carries no
# burned-in subtitles at all. Without this the pass would happily spend minutes
# reading music, logos and empty rooms on a clip that has nothing to find.
EMPTY_STREAK = 8
MAX_WINDOWS = 240


@dataclass(frozen=True)
class WindowReading:
    """What one hole in the transcript read off the picture."""

    start: float
    end: float
    text: str
    candidates: list[str]


def _engine_for(settings: Settings) -> Engine:
    """Build the configured reader; used by both the re-read and the gap pass."""
    return load_engine(
        settings.ocr_engine,
        language=settings.ocr_language,
        profile=settings.ocr_profile,
        model_folder=managed_models(settings),
        device=settings.ocr_device,
    )


def _gap_instants(start: float, end: float, minimum: int) -> list[float]:
    """Instants to sample inside one hole: dense enough to read a line twice."""
    span = max(float(end) - float(start), 0.2)
    count = min(max(int(round(span / GAP_STEP)), max(minimum, 1)), MAX_SAMPLES)
    return cue_offsets(start, end, count)


def read_windows(
    video: str | Path,
    windows: list[tuple[float, float]],
    *,
    work_dir: str | Path,
    samples: int = GAP_SAMPLES,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> list[WindowReading]:
    """Read the picture inside every window that the audio pass left empty.

    This is the mixed extraction the reference project used: recognise the
    speech from the separated vocal, then ask the frames only about the
    stretches that carry sound and produced no text. Windows that read nothing
    are dropped, so the caller can simply append the answers it gets back.
    """
    settings = provider_config.overlay(settings or get_settings())
    source = Path(video)
    if not source.is_file():
        raise OcrError(f"Video not found: {source}")

    wanted = [
        (max(float(start), 0.0), float(end))
        for start, end in windows
        if float(end) - float(start) > 0.05
    ][:MAX_WINDOWS]
    if not wanted:
        return []

    region = provider_config.resolved_region(settings)
    folder = Path(work_dir) / "gaps"
    engine = _engine_for(settings)
    cjk = _wanted_cjk(settings.ocr_language)
    report(stage="ocr", fraction=0.0, message=f"{len(wanted)} window(s) to re-read · {engine.label}")

    readings: list[WindowReading] = []
    empty = 0
    try:
        for position, (start, end) in enumerate(wanted, start=1):
            instants = _gap_instants(start, end, samples)
            seen: list[tuple[float, str]] = []
            for index, when in enumerate(instants):
                frame = grab_frame(
                    source,
                    folder / f"gap{position:03d}_{index:02d}.png",
                    when,
                    position=settings.ocr_position or "bottom",
                    band=settings.ocr_band,
                    region=region,
                )
                seen.append((when, _one_script(" ".join(engine.read(frame)), cjk=cjk)))
            text, candidates = best_reading(seen)
            # The winner is only a cue when more than one frame saw it: a single
            # frame reads a chandelier as letters happily, a cue stays on screen.
            agreement = sum(1 for _when, value in seen if value and _similar(value, text) >= SIMILARITY)
            if text and agreement >= min(MIN_AGREEMENT, len(seen)):
                empty = 0
                readings.append(
                    WindowReading(
                        start=round(start, 3),
                        end=round(end, 3),
                        text=text,
                        candidates=candidates,
                    )
                )
            else:
                empty += 1
                text = ""
            report(
                stage="ocr",
                fraction=position / len(wanted),
                message=f"OCR window {position}/{len(wanted)}" + (f" · {text}" if text else ""),
            )
            if empty >= EMPTY_STREAK:
                report(stage="ocr", fraction=1.0, message="no burned-in subtitle found - stopping")
                break
    finally:
        # One PNG per sample: on a long clip this is the largest thing the pass
        # writes, and nothing downstream reads them again.
        shutil.rmtree(folder, ignore_errors=True)

    report(stage="transcribe", fraction=1.0, message=f"OCR recovered {len(readings)} line(s)")
    return readings


@dataclass(frozen=True)
class CueReading:
    """What a second look at one cue's frames recognised."""

    text: str
    candidates: list[str]
    frames: list[tuple[float, str]]
    frame_path: Path | None
    engine: str
    device: str  # "gpu" | "cpu", as the engine really landed
    start: float
    end: float
    region: tuple[float, float, float, float]


def read_cue(
    video: str | Path,
    *,
    start: float,
    end: float,
    work_dir: str | Path,
    samples: int = 3,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> CueReading:
    """Read one cue's frame(s) again - the "this line came out wrong" path.

    The extraction pass trusts only what it saw twice, which is what removes
    watermarks and transition flashes; a line that still came out wrong is the
    one case where a second opinion is worth the round trip. Nothing is written
    here: the caller decides whether this reading replaces the line.
    """
    settings = provider_config.overlay(settings or get_settings())
    source = Path(video)
    if not source.is_file():
        raise OcrError(f"Video not found: {source}")

    region = provider_config.resolved_region(settings)
    instants = cue_offsets(start, end, samples)
    folder = Path(work_dir) / "cue"
    report(
        stage="frames",
        fraction=0.05,
        message=f"reading {len(instants)} frame(s) from {instants[0]:.2f}s to {instants[-1]:.2f}s",
    )
    try:
        frames = [
            grab_frame(
                source,
                folder / f"cue_{position:02d}.png",
                when,
                position=settings.ocr_position or "bottom",
                band=settings.ocr_band,
                region=region,
            )
            for position, when in enumerate(instants)
        ]
    except media_processor.MediaError as exc:
        raise OcrError(str(exc)) from exc

    engine = _engine_for(settings)
    report(stage="ocr", fraction=0.5, message=f"{len(frames)} frame(s) · {engine.label}")

    readings = [(when, " ".join(engine.read(frame))) for frame, when in zip(frames, instants, strict=True)]
    text, candidates = best_reading(readings)
    report(stage="ocr", fraction=1.0, message=text or "no text in this frame")
    # The middle frame is what the dialog shows: the very crop the reader saw,
    # so a scan rectangle that misses the line is visible instead of guessed.
    middle = frames[len(frames) // 2]
    window_start = round(max(float(start), 0.0), 3)
    window_end = round(max(float(end), window_start + 0.2), 3)
    return CueReading(
        text=text,
        candidates=candidates,
        frames=readings,
        frame_path=middle,
        engine=engine.name,
        device=engine.device,
        start=window_start,
        end=window_end,
        region=region,
    )


__all__ = [
    "DEVICES",
    "ENGINES",
    "CueReading",
    "Engine",
    "PROFILES",
    "OcrError",
    "WindowReading",
    "best_reading",
    "cue_offsets",
    "grab_frame",
    "load_engine",
    "managed_models",
    "merge_observations",
    "model_root",
    "normalize_text",
    "onnx_pack",
    "onnx_providers",
    "read_cue",
    "read_windows",
    "resolve_device",
    "runtime_info",
    "sample_frames",
    "transcribe",
]
