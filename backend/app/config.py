"""Application settings and Windows folder layout.

Everything that the engine writes lives under ``%APPDATA%\\ShortVideoAI`` so the
app works identically in development and inside the packaged installer (where
the install directory is read-only for a per-user NSIS install).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import APP_ID, APP_NAME

# .../backend  (the directory that also holds requirements.txt)
BASE_DIR = Path(__file__).resolve().parent.parent

# Load API keys / overrides that do not use the SVA_ prefix.
load_dotenv(BASE_DIR / ".env")


def roaming_appdata() -> Path:
    """Return ``%APPDATA%`` with a fallback that never crashes on odd shells."""
    base = os.environ.get("APPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Roaming"


def default_data_dir() -> Path:
    return roaming_appdata() / APP_NAME


class Settings(BaseSettings):
    """Environment-driven configuration (see backend/.env.example)."""

    model_config = SettingsConfigDict(
        env_prefix="SVA_",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8756
    log_level: str = "INFO"
    packaged: bool = False
    parent_pid: int | None = None

    @field_validator("parent_pid", mode="before")
    @classmethod
    def _blank_pid(cls, value: object) -> object:
        """`SVA_PARENT_PID=` (empty) must mean "no parent", not a parse error."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # --- Storage ---
    data_dir: str = ""          # empty -> %APPDATA%\ShortVideoAI
    ffmpeg_dir: str = ""        # empty -> PATH lookup
    models_dir: str = ""        # empty -> <data_dir>\models
    # Where the Models panel fetches the optional AI pack from. Empty -> the UI
    # asks for a local .zip instead.
    ai_pack_url: str = ""

    # --- Hardware ---
    device: str = "auto"        # auto | cuda | cpu | directml
    gpu_preset: str = "max"     # max | safe | cpu  (drives model sizing in hardware.py)
    cpu_threads: int = 0
    whisper_model: str = ""     # empty -> derived from gpu_preset
    whisper_compute_type: str = ""  # empty -> derived from gpu_preset
    vram_reserve_gb: float = 0.8  # headroom kept free on the GPU

    # --- Pipeline defaults ---
    # stt: local | sensevoice | groq | openai | capcut | bilibili | ocr
    stt_provider: str = "local"
    # translate: local | gemini | openai | google | bing
    translate_provider: str = "local"
    # tts: capcut | omnivoice | voicestudio
    tts_provider: str = "capcut"
    max_tempo_ratio: float = 1.35
    max_concurrent_jobs: int = 1         # serialises GPU stages
    source_language: str = "auto"
    target_language: str = "vi"

    # --- Whisper ASR cleanup (ported from CapCap asr_options.py) ---
    asr_dedup_enabled: bool = True
    asr_dedup_similarity: float = 0.82
    asr_hallucination_filter: bool = True
    asr_hallucination_silence: float = 3.0

    # --- OCR subtitle extraction ---
    ocr_engine: str = "rapidocr"         # rapidocr | winocr | paddleocr | easyocr
    ocr_profile: str = "small"           # small (chính xác) | tiny (nhanh)
    ocr_fps: float = 3.0                 # frames sampled per second
    ocr_position: str = "bottom"         # bottom | top | full
    ocr_band: float = 0.45               # share of the frame height that is scanned
    # Explicit scan rectangle "left,top,right,bottom" in percent of the frame.
    # Empty falls back to ocr_position + ocr_band; the Settings panel writes it
    # so a logo or a channel bug outside the subtitle line is never read as text.
    ocr_region: str = ""
    # auto = use the GPU when the installed onnxruntime has a GPU provider
    # (CUDA build or Windows DirectML) and fall back to CPU otherwise. Reading
    # frames is the slow half of OCR: a GPU makes a 40-minute episode minutes
    # instead of tens of minutes.
    ocr_device: str = "auto"             # auto | gpu | cpu
    ocr_language: str = "auto"           # auto (Chinese hardsubs) | vi | en | ja | ko

    # --- Models ---
    llm_model_file: str = "qwen2.5-3b-instruct-q4_k_m.gguf"
    llm_context: int = 8192
    llm_gpu_layers: int = -1             # -1 -> clamp from free VRAM
    gemini_model: str = "gemini-2.5-flash"
    openai_chat_model: str = "gpt-4.1-mini"
    groq_stt_model: str = "whisper-large-v3-turbo"
    openai_stt_model: str = "whisper-1"

    # --- TTS ---
    tts_rate: str = "+0%"
    # CapCut voice *type* (see capcut_client.CAPCUT_VOICES), not a display name.
    capcut_voice: str = "BV421_vivn_streaming"
    capcut_backend: str = "web"          # web | pc (native sscronet.dll)
    capcut_pc_path: str = ""
    # ElevenLabs TTS (ported from CapCap)
    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "eleven_turbo_v2_5"
    elevenlabs_voice: str = ""
    # Edge TTS & Piper TTS
    edge_voice: str = "vi-VN-HoaiMyNeural"
    piper_voice: str = "vi_VN-vais1000-medium"
    # DeepSeek & Ollama translation providers
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_chat_model: str = "deepseek-chat"
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_chat_model: str = "qwen2.5:7b"
    # OmniVoice (k2-fsa) runs in-process on torch. The voice is a small grammar:
    # `auto`, `design:<attributes>` or `clone:<path to a 3-10 s sample>`.
    omnivoice_model: str = "k2-fsa/OmniVoice"
    omnivoice_device: str = "auto"       # auto | cuda | cpu
    omnivoice_steps: int = 32            # diffusion steps; 16 is the fast take
    # Language tag for generate() ("vi", "en", ...): it drops the model's
    # guesswork on homographs, so the default is the app's own language.
    omnivoice_language: str = "vi"
    omnivoice_voice: str = "auto"
    omnivoice_reference_text: str = ""   # transcript of the clone sample, optional
    # VoiceStudio (debpalash) speaks through a local OmniVoice server on its
    # OpenAI-compatible routes; a loopback server needs no key.
    voicestudio_base_url: str = "http://127.0.0.1:3900/v1"
    voicestudio_model: str = "tts-1"
    voicestudio_voice: str = "alloy"
    voicestudio_api_key: str = ""

    # --- Render ---
    render_crf: int = 20
    render_preset: str = "medium"
    subtitle_font: str = "Arial"
    subtitle_font_size: int = 18

    # --- Online provider keys -------------------------------------------------
    # Accepted as SVA_*_API_KEY or as the conventional unsuffixed variable
    # (GROQ_API_KEY ...) that the providers' own SDKs look for.
    groq_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    # Overridable endpoints: a proxy, a regional host or an OpenAI-compatible
    # gateway. The Settings panel writes these, so they must exist as fields.
    gemini_base_url: str = "https://generativelanguage.googleapis.com/"
    openai_base_url: str = "https://api.openai.com/v1"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    @property
    def cache_path(self) -> Path:
        return self.data_path / Folders.CACHE

    @property
    def renders_path(self) -> Path:
        return self.data_path / Folders.RENDERS

    @property
    def temp_path(self) -> Path:
        return self.data_path / Folders.TEMP

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else default_data_dir()

    @property
    def models_path(self) -> Path:
        return Path(self.models_dir) if self.models_dir else self.data_path / "models"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class Folders:
    """Canonical sub-folders of the data directory."""

    MODELS = "models"
    CACHE = "cache"
    PROJECTS = "projects"
    RENDERS = "renders"
    LOGS = "logs"
    TEMP = "temp"

    ALL = (MODELS, CACHE, PROJECTS, RENDERS, LOGS, TEMP)


def api_key(provider: str, settings: Settings | None = None) -> str:
    """Resolve an online-provider key from settings, then the raw environment."""
    settings = settings or get_settings()
    value = (getattr(settings, f"{provider}_api_key", "") or "").strip()
    return value or (os.environ.get(f"{provider.upper()}_API_KEY") or "").strip()


def hf_token() -> str:
    return (os.environ.get("HF_TOKEN") or "").strip()


def ensure_data_dirs(settings: Settings | None = None) -> dict[str, Path]:
    """Create the data tree once per launch and return the resolved paths."""
    settings = settings or get_settings()
    root = settings.data_path
    created: dict[str, Path] = {"root": root}
    for name in Folders.ALL:
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        created[name] = folder
    # Keep the vocal-isolation weights apart from the Whisper/LLM cache.
    (settings.models_path).mkdir(parents=True, exist_ok=True)
    (settings.models_path / "separator").mkdir(parents=True, exist_ok=True)
    return created


def _winget_media_tool(tool: str) -> list[Path]:
    """ffmpeg/ffprobe shims installed by `winget install Gyan.FFmpeg`.

    Winget appends to the *user* PATH, which a process started before the
    install (or by the Tauri sidecar) will not see. Globbing the package folder
    keeps the engine working without a reboot.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    root = Path(local) / "Microsoft" / "WinGet"
    found = [root / "Links" / f"{tool}.exe"]
    packages = root / "Packages"
    if packages.is_dir():
        found.extend(sorted(packages.glob(f"Gyan.FFmpeg*/**/bin/{tool}.exe")))
    return found


def resolve_media_tool(tool: str, settings: Settings | None = None) -> Path | None:
    """Locate a bundled or system `ffmpeg` / `ffprobe` executable."""
    settings = settings or get_settings()
    candidates: list[Path] = []

    if settings.ffmpeg_dir:
        candidates.append(Path(settings.ffmpeg_dir) / f"{tool}.exe")

    # Packaged layout: the Tauri sidecar supervisor passes SVA_FFMPEG_DIR, but
    # also look next to the frozen executable (<app>\engine\ffmpeg\ffmpeg.exe)
    # and inside the PyInstaller bundle root as a fallback.
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "ffmpeg" / f"{tool}.exe")
        candidates.append(exe_dir.parent / "ffmpeg" / f"{tool}.exe")
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir) / "ffmpeg" / f"{tool}.exe")

    # Development layout: <repo>\resources\ffmpeg, then the engine's own folder.
    candidates.append(Path(os.path.abspath(".")) / "ffmpeg" / f"{tool}.exe")
    candidates.append(BASE_DIR.parent / "resources" / "ffmpeg" / f"{tool}.exe")

    candidates.extend(_winget_media_tool(tool))

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    from shutil import which

    found = which(tool)
    return Path(found) if found else None


def resolve_ffmpeg(settings: Settings | None = None) -> Path | None:
    return resolve_media_tool("ffmpeg", settings)


def resolve_ffprobe(settings: Settings | None = None) -> Path | None:
    return resolve_media_tool("ffprobe", settings)


__all__ = [
    "APP_ID",
    "APP_NAME",
    "BASE_DIR",
    "Folders",
    "Settings",
    "api_key",
    "default_data_dir",
    "ensure_data_dirs",
    "get_settings",
    "hf_token",
    "resolve_ffmpeg",
    "resolve_ffprobe",
    "resolve_media_tool",
    "roaming_appdata",
]
