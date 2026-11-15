"""Online AI provider configuration written from the UI.

Until now a key could only arrive through ``SVA_*_API_KEY`` in ``backend/.env``
or the environment, which meant a packaged app had *nowhere* to enter one. This
module owns that missing half: a small JSON document in the data folder that the
Settings panel edits and every service reads.

Precedence is deliberately simple — **stored value wins, environment is the
seed**:

* the first read copies ``SVA_*``/conventional variables into the document, so a
  developer's existing ``.env`` shows up pre-filled in the UI instead of being
  silently overridden;
* after that the document is the single source of truth, so what the user types
  is what runs.

``overlay()`` folds the document into a ``Settings`` copy; the services call it
once at their entry point and keep reading ``settings.gemini_model`` etc. as
before. Nothing else in the pipeline needs to know this file exists.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.services import model_manager

log = logging.getLogger(__name__)

CONFIG_VERSION = 1
CONFIG_NAME = "providers.json"
CONFIG_FOLDER = "settings"
TIMEOUT = httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0)

# Fields a provider document may carry. ``model`` is the chat / STT / TTS model
# depending on the provider; OpenAI and Groq need two, so the slot list below is
# what the UI renders.
SLOTS: dict[str, tuple[str, ...]] = {
    "gemini": ("model",),
    "openai": ("model", "stt_model"),
    "groq": ("model",),
    "voicestudio": ("model",),
}


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of one online provider (labels, presets, hints)."""

    name: str
    label: str
    kind: str                        # translate | stt | tts
    key_hint: str = ""
    key_url: str = ""
    base_url: str = ""
    slots: tuple[tuple[str, str, tuple[str, ...]], ...] = ()
    notes: str = ""

    def to_dict(self, stored: dict[str, Any], settings: Settings) -> dict[str, Any]:
        env_names = _env_names(self.name, self.slots)
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "key_hint": self.key_hint,
            "key_url": self.key_url,
            "base_url": stored.get("base_url") or self.base_url,
            "base_url_default": self.base_url,
            "notes": self.notes,
            # The key itself never leaves the engine: the UI shows "đã lưu" and
            # only sends a new value when the user types one.
            "api_key_set": bool(stored.get("api_key") or _env_value(self.name, settings)),
            # True when the value still comes from the environment: the UI says
            # so instead of pretending the box it shows is the whole story.
            "api_key_from_env": bool(not stored.get("api_key") and _env_value(self.name, settings)),
            "slots": [
                {
                    "key": key,
                    "label": label,
                    "presets": list(presets),
                    "value": stored.get(key) or "",
                    # What an empty box runs: the engine default, not the first
                    # suggestion. They differ now - the presets lead with the
                    # recommended free-tier model, the default stays the safe one.
                    "default": _engine_default(self.name, key, settings) or (presets[0] if presets else ""),
                    "from_env": env_names.get(key, ""),
                }
                for key, label, presets in self.slots
            ],
        }


def _engine_default(provider: str, slot: str, settings: Settings) -> str:
    """The value the services actually run when the box is left empty."""
    field = _SLOT_FIELDS.get((provider, slot), "")
    return str(getattr(settings, field, "") or "") if field else ""


PROVIDERS: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec(
        name="gemini",
        label="Google Gemini (AI Studio)",
        kind="translate",
        key_hint="Lấy API key tại https://aistudio.google.com/apikey",
        key_url="https://aistudio.google.com/apikey",
        base_url="https://generativelanguage.googleapis.com/",
        slots=(
            (
                "model",
                "Model dịch",
                (
                    "gemini-3.1-flash-lite",
                    "gemini-4-hydrogen",
                    "gemini-2.5-flash",
                    "gemini-2.5-pro",
                    "gemini-2.0-flash",
                ),
            ),
        ),
        notes=(
            "Free Tier: gemini-3.1-flash-lite (nghĩ ít nhất, 40 câu/lượt, nghỉ 4s) cho phim dài, "
            "phụ đề đơn giản; gemini-4-hydrogen (25–30 câu/lượt, nghỉ 3s) cho thoại cần xưng hô và "
            "mạch cảm xúc. Engine tự gom lô và giãn nhịp theo model, các model khác dùng lô 20 câu."
        ),
    ),
    "openai": ProviderSpec(
        name="openai",
        label="OpenAI",
        kind="translate",
        key_hint="Lấy API key tại https://platform.openai.com/api-keys",
        key_url="https://platform.openai.com/api-keys",
        base_url="https://api.openai.com/v1",
        slots=(
            ("model", "Model dịch (chat)", ("gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1", "gpt-4o")),
            ("stt_model", "Model nhận dạng", ("whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe")),
        ),
        notes="Nhận dạng giọng nói và dịch dùng chung một key.",
    ),
    "groq": ProviderSpec(
        name="groq",
        label="Groq",
        kind="stt",
        key_hint="Lấy API key tại https://console.groq.com/keys",
        key_url="https://console.groq.com/keys",
        base_url="https://api.groq.com/openai/v1",
        slots=(
            (
                "model",
                "Model nhận dạng",
                ("whisper-large-v3-turbo", "whisper-large-v3", "distil-whisper-large-v3-en"),
            ),
        ),
        notes="Whisper trên LPU: nhanh nhất trong các lựa chọn online.",
    ),
    "voicestudio": ProviderSpec(
        name="voicestudio",
        label="VoiceStudio (OmniVoice, local)",
        kind="tts",
        key_hint=(
            "Chỉ cần khi server VoiceStudio chạy với OMNIVOICE_API_KEY "
            "(Docker hoặc máy khác). Gọi từ cùng máy thì để trống."
        ),
        base_url="http://127.0.0.1:3900/v1",
        slots=(("model", "Model giọng đọc", ("tts-1",)),),
        notes=(
            "VoiceStudio (github.com/debpalash/voicestudio) mở API kiểu OpenAI ở cổng 3900; "
            "engine mặc định của nó là OmniVoice của k2-fsa. Cài app hoặc chạy Docker, rồi chọn "
            "VoiceStudio ở bước 3 · Lồng tiếng."
        ),
    ),
}

# Which Settings field each slot overrides.
_SLOT_FIELDS: dict[tuple[str, str], str] = {
    ("gemini", "model"): "gemini_model",
    ("openai", "model"): "openai_chat_model",
    ("openai", "stt_model"): "openai_stt_model",
    ("groq", "model"): "groq_stt_model",
    ("voicestudio", "model"): "voicestudio_model",
}

_KEY_ENV_ALIASES = {
    "gemini": ("GOOGLE_AI_STUDIO_API_KEY", "GEMINI_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "voicestudio": ("OMNIVOICE_API_KEY", "VOICESTUDIO_API_KEY"),
}

_SLOT_ENV_ALIASES = {
    ("gemini", "model"): ("SVA_GEMINI_MODEL", "GEMINI_MODEL", "GOOGLE_AI_STUDIO_MODEL"),
    ("openai", "model"): ("SVA_OPENAI_CHAT_MODEL", "OPENAI_MODEL"),
    ("openai", "stt_model"): ("SVA_OPENAI_STT_MODEL",),
    ("groq", "model"): ("SVA_GROQ_STT_MODEL", "GROQ_MODEL"),
    ("voicestudio", "model"): ("SVA_VOICESTUDIO_MODEL",),
}

# OCR engine names the UI may pick, and the speed/accuracy profiles.
OCR_ENGINES = ("rapidocr", "paddleocr", "easyocr")
OCR_PROFILES = ("small", "tiny")
# Where the frame reader runs: auto = GPU when the ONNX build has one, CPU
# otherwise. Reading frames is the slow half of OCR, so the default now tries
# the GPU; "gpu" is a preference, never a hard requirement (a job must not fail
# because a wheel is missing).
OCR_DEVICES = ("auto", "gpu", "cpu")
# The scan rectangle is stored as "left,top,right,bottom" in percent of the
# frame. Text keeps providers.json hand-editable and the merge below single-typed;
# a region narrower than this is a slip of the mouse, not a subtitle line.
MIN_REGION_SPAN = 4.0

Region = tuple[float, float, float, float]


class ProviderConfigError(RuntimeError):
    """Raised when the document cannot be read or a value is invalid."""


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
def config_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.data_path / CONFIG_FOLDER


def config_path(settings: Settings | None = None) -> Path:
    return config_dir(settings) / CONFIG_NAME


def _blank() -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "providers": {name: {slot: "" for slot in ("api_key", "base_url", *SLOTS[name])} for name in PROVIDERS},
        # Which GGUF the offline translator loads. Empty = the engine default.
        "llm": {"model": ""},
        "ocr": {"engine": "", "profile": "", "region": "", "device": ""},
        "audio": {"mode": ""},
    }


def _merge(document: dict[str, Any]) -> dict[str, Any]:
    """Fill every known key so a hand-edited or older file still reads."""
    merged = _blank()
    providers = document.get("providers")
    if isinstance(providers, dict):
        for name, values in providers.items():
            if name in merged["providers"] and isinstance(values, dict):
                for key in merged["providers"][name]:
                    value = values.get(key)
                    if isinstance(value, str):
                        merged["providers"][name][key] = value.strip()
    for section, keys in (
        ("llm", ("model",)),
        ("ocr", ("engine", "profile", "region", "device")),
        ("audio", ("mode",)),
    ):
        values = document.get(section)
        if isinstance(values, dict):
            for key in keys:
                value = values.get(key)
                if isinstance(value, str):
                    merged[section][key] = value.strip()
    return merged


# path -> ((mtime_ns, size), document). `overlay()` runs once per service call
# and `dub()` synthesises hundreds of lines, so re-parsing the JSON for every
# line is measurable; the stamp makes an edit invalidate it by itself.
_CACHE: dict[str, tuple[tuple[int, int] | None, dict[str, Any]]] = {}


def load(settings: Settings | None = None) -> dict[str, Any]:
    """Read the document, seeding it from the environment on first use."""
    settings = settings or get_settings()
    path = config_path(settings)
    try:
        stat = path.stat()
        stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None

    cached = _CACHE.get(str(path))
    if cached is not None and cached[0] == stamp:
        document = cached[1]
    else:
        document = _read(path, settings)
        _CACHE[str(path)] = (stamp, document)
    return _merge(document)


def _read(path: Path, settings: Settings) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # First run: the environment is the seed (see the module docstring).
        return _seed(_blank(), settings)
    except OSError as exc:
        log.warning("provider settings unreadable (%s) - using defaults", exc)
        return _seed(_blank(), settings)
    try:
        raw = json.loads(text)
    except ValueError as exc:
        log.warning("provider settings are not valid JSON (%s) - using defaults", exc)
        return _seed(_blank(), settings)
    if not isinstance(raw, dict):
        return _seed(_blank(), settings)
    document = _merge(raw)
    empty = not any(values.values() for values in document["providers"].values())
    if empty and not document["ocr"]["engine"]:
        # Everything empty: an empty file from an older build, seed it once.
        return _seed(document, settings)
    return document


def _seed(document: dict[str, Any], settings: Settings | None) -> dict[str, Any]:
    """Copy whatever the environment already provides into the document."""
    settings = settings or get_settings()
    seeded = _merge(document)
    for name in PROVIDERS:
        key = _env_value(name, settings)
        if key and not seeded["providers"][name]["api_key"]:
            seeded["providers"][name]["api_key"] = key
    return seeded


def save(patch: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    """Merge a patch into the document, validate it and write it atomically."""
    document = load(settings)
    providers = patch.get("providers")
    if isinstance(providers, dict):
        for name, values in providers.items():
            if name not in document["providers"] or not isinstance(values, dict):
                continue
            for key, value in values.items():
                if key not in document["providers"][name] or not isinstance(value, str):
                    continue
                document["providers"][name][key] = value.strip()
    llm = patch.get("llm")
    if isinstance(llm, dict) and "model" in llm:
        # Only a GGUF the catalogue can download: a typo here would otherwise
        # surface as "not installed" on the first translation instead of at save.
        model = str(llm.get("model", "")).strip()
        if model and model not in model_manager.llm_filenames():
            raise ProviderConfigError(f"Model dịch offline không hợp lệ: {model}")
        document["llm"]["model"] = model

    ocr = patch.get("ocr")
    if isinstance(ocr, dict):
        engine = str(ocr.get("engine", "")).strip().lower()
        if engine and engine not in OCR_ENGINES:
            raise ProviderConfigError(f"OCR engine không hợp lệ: {engine}")
        profile = str(ocr.get("profile", "")).strip().lower()
        if profile and profile not in OCR_PROFILES:
            raise ProviderConfigError(f"OCR profile không hợp lệ: {profile}")
        device = str(ocr.get("device", "")).strip().lower()
        if device and device not in OCR_DEVICES:
            raise ProviderConfigError(f"Thiết bị OCR không hợp lệ: {device}")
        if "engine" in ocr:
            document["ocr"]["engine"] = engine
        if "profile" in ocr:
            document["ocr"]["profile"] = profile
        if "device" in ocr:
            document["ocr"]["device"] = device
        if "region" in ocr:
            region = ocr.get("region")
            if isinstance(region, str) and not region.strip():
                document["ocr"]["region"] = ""  # back to position + band
            else:
                try:
                    document["ocr"]["region"] = format_region(_region_numbers(region))
                except ValueError as exc:
                    raise ProviderConfigError(f"Vùng quét không hợp lệ: {exc}") from exc
    audio = patch.get("audio")
    if isinstance(audio, dict) and "mode" in audio:
        mode = str(audio.get("mode", "")).strip().lower()
        if mode and mode not in {"fast", "clean"}:
            raise ProviderConfigError(f"Chế độ tách giọng không hợp lệ: {mode}")
        document["audio"]["mode"] = mode

    path = config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
    return document


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def _env_value(name: str, settings: Settings) -> str:
    stored = (getattr(settings, f"{name}_api_key", "") or "").strip()
    if stored:
        return stored
    for alias in _KEY_ENV_ALIASES.get(name, ()):
        value = (os.environ.get(alias) or "").strip()
        if value:
            return value
    return ""


def _env_names(name: str, slots: tuple[tuple[str, str, tuple[str, ...]], ...]) -> dict[str, str]:
    """Slot key -> environment variable that would override it, for the UI hint."""
    found: dict[str, str] = {}
    for key, _label, _presets in slots:
        for alias in _SLOT_ENV_ALIASES.get((name, key), ()):
            if (os.environ.get(alias) or "").strip():
                found[key] = alias
                break
    return found


def _region_numbers(value: Any) -> Region:
    """``"0,55,100,100"`` or ``[0, 55, 100, 100]`` -> four checked percentages."""
    if isinstance(value, dict):  # a client sending {"left": ..} is not supported
        raise ValueError("cần 4 số: trái, trên, phải, dưới")
    if isinstance(value, (list, tuple)):
        parts: list[Any] = list(value)
    else:
        parts = [part for part in str(value).replace(";", ",").split(",") if part.strip()]
    if len(parts) != 4:
        raise ValueError("cần 4 số: trái, trên, phải, dưới (% chiều rộng/cao)")
    try:
        numbers = [round(float(part), 1) for part in parts]
    except (TypeError, ValueError) as exc:
        raise ValueError("mỗi số phải là phần trăm 0-100") from exc
    left, top, right, bottom = numbers
    if not all(0.0 <= number <= 100.0 for number in numbers):
        raise ValueError("mỗi số phải nằm trong 0-100")
    if right - left < MIN_REGION_SPAN or bottom - top < MIN_REGION_SPAN:
        raise ValueError(f"vùng nhỏ hơn {MIN_REGION_SPAN:g}% mỗi chiều thì không đọc được dòng nào")
    return (left, top, right, bottom)


def parse_region(value: Any) -> Region | None:
    """Stored text -> numbers; empty means "derive from position + band"."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _region_numbers(value)


def format_region(region: Region) -> str:
    return ",".join(f"{value:g}" for value in region)


def resolved_region(settings: Settings) -> Region:
    """The rectangle the OCR really crops: the stored one, or the legacy band."""
    try:
        stored = parse_region(getattr(settings, "ocr_region", ""))
    except ValueError as exc:
        log.warning("stored OCR region ignored (%s) - falling back to position/band", exc)
        stored = None
    if stored is not None:
        return stored
    position = (getattr(settings, "ocr_position", "") or "bottom").strip().lower()
    if position == "full":
        return (0.0, 0.0, 100.0, 100.0)
    share = min(max(float(getattr(settings, "ocr_band", 0.45) or 0.45), 0.1), 1.0) * 100
    if position == "top":
        return (0.0, 0.0, 100.0, round(share, 1))
    return (0.0, round(100 - share, 1), 100.0, 100.0)


def overlay(settings: Settings | None = None) -> Settings:
    """Fold the stored document into a ``Settings`` copy for the services."""
    settings = settings or get_settings()
    document = load(settings)
    updates: dict[str, Any] = {}

    for name, values in document["providers"].items():
        if values.get("api_key"):
            updates[f"{name}_api_key"] = values["api_key"]
        for (provider, slot), field_name in _SLOT_FIELDS.items():
            if provider != name:
                continue
            value = values.get(slot, "")
            if value:
                updates[field_name] = value
        if values.get("base_url"):
            updates[f"{name}_base_url"] = values["base_url"]

    if document["llm"]["model"]:
        updates["llm_model_file"] = document["llm"]["model"]

    ocr = document["ocr"]
    if ocr["engine"]:
        updates["ocr_engine"] = ocr["engine"]
    if ocr["profile"]:
        updates["ocr_profile"] = ocr["profile"]
    if ocr["region"]:
        updates["ocr_region"] = ocr["region"]
    if ocr["device"]:
        updates["ocr_device"] = ocr["device"]

    if not updates:
        return settings
    return settings.model_copy(update=updates)


def audio_mode(settings: Settings | None = None) -> str:
    """``fast`` (transcribe the mix) or ``clean`` (separate the vocal first)."""
    mode = load(settings)["audio"]["mode"]
    return mode if mode in {"fast", "clean"} else "fast"


def ocr_profile(settings: Settings | None = None) -> str:
    profile = load(settings)["ocr"]["profile"]
    return profile if profile in OCR_PROFILES else "small"


def describe(settings: Settings | None = None) -> dict[str, Any]:
    """Everything the Settings panel renders, with no secret in clear text."""
    settings = settings or get_settings()
    document = load(settings)
    return {
        "path": str(config_path(settings)),
        "providers": [
            PROVIDERS[name].to_dict(document["providers"][name], settings) for name in PROVIDERS
        ],
        "llm": _describe_llm(settings, document),
        "ocr": _describe_ocr(settings, document),
        "audio": _describe_audio(settings),
    }


def _describe_llm(settings: Settings, document: dict[str, Any]) -> dict[str, Any]:
    """The offline GGUF choices, their install state and which one runs."""
    active = document["llm"]["model"] or settings.llm_model_file
    options = []
    for spec in model_manager.llm_specs():
        status = model_manager.status(spec, settings)
        filename = spec.files[0].filename if spec.files else ""
        options.append(
            {
                "key": spec.key,
                "label": spec.label,
                "description": spec.description,
                "filename": filename,
                "size_mb": spec.size_mb,
                "installed": status.downloaded,
                "missing_packages": list(status.missing_packages),
                "active": filename == active,
                "licence": spec.licence,
            }
        )
    return {
        "model": active,
        # False when the active GGUF is not on disk: translation would refuse.
        "ready": model_manager.llm_path(settings.model_copy(update={"llm_model_file": active})) is not None,
        "engine_default": settings.llm_model_file,
        "options": options,
    }


def _describe_ocr(settings: Settings, document: dict[str, Any]) -> dict[str, Any]:
    """Engine list + the two speed/accuracy profiles with their install state."""
    # Local import: ocr_service imports this module, so a module-level import
    # would be a cycle.
    from app.services import ocr_service  # noqa: PLC0415

    folder = ocr_service.model_root(settings)
    try:
        tiny_installed = any(folder.glob("*det_tiny*.onnx"))
    except OSError:
        tiny_installed = False
    region = resolved_region(settings.model_copy(update={"ocr_region": document["ocr"]["region"]}))
    device = document["ocr"]["device"] or (settings.ocr_device or "auto")
    return {
        "engine": document["ocr"]["engine"] or settings.ocr_engine,
        "profile": ocr_profile(settings),
        "engines": list(OCR_ENGINES),
        # Where the reader runs. `device_actual` is the honest half: asking for
        # a GPU without an onnxruntime GPU provider still reads on the CPU.
        "device": device,
        "device_actual": ocr_service.resolve_device(device),
        "devices": list(OCR_DEVICES),
        "onnx_providers": ocr_service.onnx_providers(),
        # Which ONNX Runtime wheel is installed and the command that swaps it:
        # CPU / DirectML / CUDA are one package with three builds, so "use the
        # GPU" is a reinstall, not an extra requirement.
        "onnx": ocr_service.runtime_info(),
        # The rectangle the OCR crops, already resolved: the panel draws this.
        "region": list(region),
        "region_stored": bool(document["ocr"]["region"]),
        "region_span": MIN_REGION_SPAN,
        "position": settings.ocr_position,
        "band": settings.ocr_band,
        "profiles": [
            {
                "name": "small",
                "label": "PP-OCRv6 Small — Chính xác",
                "speed": "Chuẩn",
                "detector": "det small · 9,9 MB",
                "recognizer": "rec small · 21 MB",
                "note": "Đi kèm sẵn, không cần tải. Đọc tốt phụ đề nhiều dấu.",
                "installed": True,
            },
            {
                "name": "tiny",
                "label": "PP-OCRv6 Tiny Det — Nhanh nhất",
                "speed": "Nhanh nhất",
                "detector": "det tiny · 9,9 MB",
                "recognizer": "rec small · 21 MB",
                "note": (
                    "Bộ dò nhỏ hơn cho tốc độ gần gấp đôi; nhận dạng giữ nguyên "
                    "bản small nên dấu tiếng Việt vẫn đủ."
                ),
                "installed": tiny_installed,
            },
        ],
        "model_dir": str(folder),
        "managed": ocr_service.managed_models(settings) is not None,
    }


def _describe_audio(settings: Settings) -> dict[str, Any]:
    """``fast`` vs CapCap's ``clean`` (vocal separation before recognition)."""
    key = "uvr-mdx-inst-hq3"
    try:
        status = model_manager.status(model_manager.require(key), settings)
        weights_ready = status.downloaded
        package_ready = not status.missing_packages
    except (model_manager.ModelError, OSError):
        weights_ready = package_ready = False
    clean_ready = weights_ready and package_ready
    return {
        "mode": audio_mode(settings),
        "modes": [
            {
                "name": "fast",
                "label": "Nhanh (khuyên dùng)",
                "note": "Nhận dạng trên nguyên bản âm thanh. Nhanh nhất, đủ tốt khi giọng rõ.",
                "ready": True,
            },
            {
                "name": "clean",
                "label": "Giọng sạch (tách nhạc nền)",
                "note": (
                    "Tách giọng khỏi nhạc bằng UVR-MDX rồi khử nhiễu trước khi nhận dạng. "
                    "Chậm hơn nhưng cứu những clip nhạc to, nhiều hiệu ứng."
                ),
                "ready": clean_ready,
            },
        ],
        "separator_model": key,
        "separator_installed": weights_ready,
        "package_installed": package_ready,
    }


# ---------------------------------------------------------------------------
# Connection test
# ---------------------------------------------------------------------------
@dataclass
class TestOutcome:
    ok: bool
    message: str
    models: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "message": self.message, "models": self.models, "detail": self.detail}


def _openai_models(base_url: str, key: str) -> list[str]:
    response = httpx.get(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    names = {str(row.get("id", "")).strip() for row in rows if isinstance(row, dict)}
    return sorted(name for name in names if name)


def _gemini_models(base_url: str, key: str) -> list[str]:
    from google import genai  # noqa: PLC0415 - optional heavy import
    from google.genai import types  # noqa: PLC0415

    # The SDK appends ``/v1beta`` itself, so a custom host needs the root URL.
    options = types.HttpOptions(base_url=base_url.rstrip("/") + "/") if base_url else None
    client = genai.Client(api_key=key, http_options=options)
    names: list[str] = []
    for model in client.models.list():
        name = str(getattr(model, "name", "") or "")
        names.append(name.removeprefix("models/"))
    return sorted(names)


def _voicestudio_status(settings: Settings, base_url: str, key: str) -> str:
    """Probe the local VoiceStudio backend; the key is optional on loopback."""
    from app.services import voicestudio_tts  # noqa: PLC0415 - keeps the import graph flat

    configured = settings.model_copy(update={"voicestudio_base_url": base_url, "voicestudio_api_key": key})
    return voicestudio_tts.reachable(configured)


def test_connection(
    name: str,
    settings: Settings | None = None,
    overrides: dict[str, str] | None = None,
) -> TestOutcome:
    """Probe one provider with the *stored* credentials and list its models.

    ``overrides`` lets the Settings panel test a key the user just typed,
    before it is written to disk.
    """
    settings = settings or get_settings()
    if name not in PROVIDERS:
        raise ProviderConfigError(f"Provider không hợp lệ: {name}")
    document = load(settings)
    values = {**document["providers"][name]}
    for key, value in (overrides or {}).items():
        if key in values and isinstance(value, str) and value.strip():
            values[key] = value.strip()
    key = values.get("api_key") or _env_value(name, settings)
    base_url = values.get("base_url") or PROVIDERS[name].base_url
    if name == "voicestudio":
        # A loopback VoiceStudio answers without any credential, so an empty key
        # is a valid setup rather than an error.
        try:
            return TestOutcome(True, _voicestudio_status(settings, base_url, key))
        except Exception as exc:  # noqa: BLE001 - connection errors are the answer
            return TestOutcome(False, str(exc))
    if not key:
        return TestOutcome(False, "Chưa có API key.")

    try:
        if name == "gemini":
            models = _gemini_models(base_url, key)
            return TestOutcome(True, f"Kết nối Gemini · {len(models)} model khả dụng", models)
        models = _openai_models(base_url, key)
        label = PROVIDERS[name].label
        return TestOutcome(True, f"Kết nối {label} · {len(models)} model khả dụng", models)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = {
            401: "API key không hợp lệ hoặc đã bị thu hồi.",
            403: "Key hợp lệ nhưng không có quyền dùng model này.",
            404: "Sai API URL hoặc model không tồn tại.",
            429: "Vượt hạn mức (rate limit) — thử lại sau ít phút.",
            503: "Máy chủ đang quá tải — thử lại sau.",
        }.get(status, "")
        detail = exc.response.text[:300]
        return TestOutcome(False, f"HTTP {status}. {hint}".strip(), detail=detail)
    except httpx.HTTPError as exc:
        return TestOutcome(False, f"Không kết nối được: {exc}")
    except Exception as exc:  # noqa: BLE001 - SDK errors are provider specific
        return TestOutcome(False, f"Kiểm tra thất bại: {exc}")


__all__ = [
    "CONFIG_VERSION",
    "OCR_ENGINES",
    "OCR_PROFILES",
    "PROVIDERS",
    "ProviderConfigError",
    "TestOutcome",
    "audio_mode",
    "config_path",
    "describe",
    "load",
    "ocr_profile",
    "overlay",
    "save",
    "test_connection",
]
