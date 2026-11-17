"""Health probe and hardware report."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
            "openai": bool(settings.openai_api_key or ""),
            "gemini": bool(settings.gemini_api_key or ""),
        },
    }


class OpenPathRequest(BaseModel):
    path: str
    action: Literal["open", "reveal"] = "open"


@router.post("/api/system/open-path")
def open_system_path(body: OpenPathRequest) -> dict[str, Any]:
    """Open a rendered file in the default player or reveal it in the file manager."""
    raw = (body.path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Đường dẫn trống.")
    target = Path(raw).expanduser().resolve()
    if not target.exists():
        # Fall back to parent folder when the file itself was moved/deleted.
        parent = target.parent
        if body.action == "reveal" and parent.is_dir():
            target = parent
        else:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy tệp hoặc thư mục: {target}")

    try:
        if sys.platform == "win32":
            if body.action == "reveal" and target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(target if body.action == "open" or target.is_dir() else target.parent))
        elif sys.platform == "darwin":
            cmd = ["open", "-R", str(target)] if body.action == "reveal" and target.is_file() else ["open", str(target)]
            subprocess.Popen(cmd)
        else:
            dest = target if (body.action == "open" or target.is_dir()) else target.parent
            subprocess.Popen(["xdg-open", str(dest)])
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Không thể mở đường dẫn: {exc}") from exc

    return {"ok": True, "path": str(target), "action": body.action}


__all__ = ["STARTED_AT", "router"]
