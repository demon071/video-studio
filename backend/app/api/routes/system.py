"""Health probe and hardware report."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter

from app import APP_VERSION
from app.config import Settings, ensure_data_dirs, get_settings, resolve_ffmpeg, resolve_ffprobe
from app.core import hardware
from app.services import ai_pack, provider_config

router = APIRouter(tags=["system"])

STARTED_AT = time.monotonic()


def _tts_voice(settings: Settings) -> str:
    """The voice the current provider would use, without loading anything."""
    provider = (settings.tts_provider or "capcut").lower()
    if provider == "omnivoice":
        return settings.omnivoice_voice
    if provider == "voicestudio":
        return settings.voicestudio_voice
    return settings.capcut_voice


@router.get("/api/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "version": APP_VERSION,
        "device": settings.device,
        "ffmpeg": resolve_ffmpeg(settings) is not None,
        "uptime_s": round(time.monotonic() - STARTED_AT, 1),
        # Which binary is answering and whether it carries the AI stack. Lets
        # the UI (and support) tell a core engine from an AI-pack one.
        "runtime": ai_pack.summary_for_health(settings),
    }


@router.get("/api/system/hardware")
def hardware_report() -> dict[str, Any]:
    """Everything the UI needs to explain the current compute plan."""
    settings = get_settings()
    profile = hardware.detect()
    folders = ensure_data_dirs(settings)
    ffmpeg = resolve_ffmpeg(settings)
    ffprobe = resolve_ffprobe(settings)

    return {
        "profile": profile.to_dict(),
        "preset": profile.preset,
        "device": "cuda" if profile.has_cuda else "cpu",
        "whisper": {"model": profile.whisper_model, "compute_type": profile.whisper_compute_type},
        # The GGUF the translator would load now, not the engine default: the
        # offline model is a choice in providers.json.
        "llm": {"model": provider_config.overlay(settings).llm_model_file, "gpu_layers": profile.llm_gpu_layers},
        "tts": {"provider": settings.tts_provider, "voice": _tts_voice(settings)},
        "paths": {
            "data": str(folders["root"]),
            "models": str(settings.models_path),
            "renders": str(folders["renders"]),
            "temp": str(folders["temp"]),
        },
        "ffmpeg": str(ffmpeg) if ffmpeg else None,
        "ffprobe": str(ffprobe) if ffprobe else None,
        "api_keys": {
            "groq": bool(settings.groq_api_key or ""),
            "openai": bool(settings.openai_api_key or ""),
            "gemini": bool(settings.gemini_api_key or ""),
        },
    }


__all__ = ["STARTED_AT", "router"]
