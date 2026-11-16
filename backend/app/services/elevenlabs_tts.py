"""ElevenLabs studio TTS engine (ported from CapCap).

Supports:
* ``eleven_turbo_v2_5``, ``eleven_flash_v2_5``, ``eleven_v4``, ``eleven_v4_turbo``, ``eleven_v3``
* Account subscription tier & character quota inspection
* Voice catalog fetching with disk cache
* Speech synthesis into MP3/WAV
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings, api_key, get_settings

log = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # Rachel
DEFAULT_MODEL = "eleven_turbo_v2_5"

ELEVENLABS_MODELS: tuple[tuple[str, str], ...] = (
    ("eleven_turbo_v2_5", "Turbo v2.5 — Ổn định & tự nhiên (Khuyên dùng)"),
    ("eleven_flash_v2_5", "Flash v2.5 — Siêu nhanh & tiết kiệm quota"),
    ("eleven_v4", "Eleven v4 — Flagship 90+ ngôn ngữ"),
    ("eleven_v4_turbo", "Eleven v4 Turbo — Tốc độ cao"),
    ("eleven_v3", "Eleven v3 — Biểu cảm nâng cao (Hỗ trợ tiếng Thái)"),
)


class ElevenLabsError(RuntimeError):
    """Raised when ElevenLabs TTS fails."""


def _key(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    return (
        api_key("elevenlabs", settings)
        or settings.elevenlabs_api_key
        or ""
    ).strip()


def resolve_voice(voice: str | None, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    chosen = (voice or settings.elevenlabs_voice or DEFAULT_VOICE).replace("elevenlabs:", "").strip()
    return chosen or DEFAULT_VOICE


def fetch_subscription(key: str | None = None, settings: Settings | None = None) -> dict[str, Any]:
    """Retrieve ElevenLabs account tier and remaining character quota."""
    token = (key or _key(settings)).strip()
    if not token:
        return {}
    headers = {"xi-api-key": token}
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(f"{ELEVENLABS_BASE}/user/subscription", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("tier"):
                    return {
                        "tier": str(data.get("tier") or "free"),
                        "character_count": int(data.get("character_count") or 0),
                        "character_limit": int(data.get("character_limit") or 0),
                        "status": str(data.get("status") or "active"),
                    }
    except Exception as exc:  # noqa: BLE001
        log.warning("ElevenLabs subscription check failed: %s", exc)
    return {}


def list_voices(key: str | None = None, settings: Settings | None = None) -> dict[str, Any]:
    """Return available ElevenLabs voices and account quota, cached for 1 hour."""
    settings = settings or get_settings()
    token = (key or _key(settings)).strip()
    if not token:
        return {"voices": [], "subscription": {}, "models": [{"id": m, "label": lbl} for m, lbl in ELEVENLABS_MODELS]}

    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    cache_file = settings.cache_path / f"elevenlabs_voices_{fingerprint}.json"
    if cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - float(cached.get("fetched_at", 0)) < 3600:
                return cached
        except Exception:  # noqa: BLE001
            pass

    subscription = fetch_subscription(token, settings)
    is_free = str(subscription.get("tier", "free")).lower() == "free"
    voices: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(f"{ELEVENLABS_BASE}/voices", headers={"xi-api-key": token})
            resp.raise_for_status()
            payload = resp.json()
            for item in payload.get("voices") or []:
                if not isinstance(item, dict) or not item.get("voice_id"):
                    continue
                category = str(item.get("category") or "premade").lower()
                paid_only = category in {"professional", "famous"}
                voices.append(
                    {
                        "id": str(item["voice_id"]),
                        "name": str(item.get("name") or item["voice_id"]),
                        "category": category,
                        "labels": item.get("labels") or {},
                        "preview_url": item.get("preview_url") or "",
                        "paid_only": paid_only,
                        "enabled": not (is_free and paid_only),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        raise ElevenLabsError(f"Cannot fetch ElevenLabs voices: {exc}") from exc

    result = {
        "fetched_at": time.time(),
        "subscription": subscription,
        "voices": voices,
        "models": [{"id": m, "label": lbl} for m, lbl in ELEVENLABS_MODELS],
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return result


def synthesize(
    text: str,
    target: Path,
    *,
    voice: str | None = None,
    settings: Settings | None = None,
) -> Path:
    """Synthesize ``text`` through ElevenLabs and write MP3 bytes to ``target``."""
    settings = settings or get_settings()
    token = _key(settings)
    if not token:
        raise ElevenLabsError("ELEVENLABS_API_KEY is not configured (add it in Settings)")

    spoken = (text or "").strip()
    if not spoken:
        raise ElevenLabsError("No text to synthesize")

    voice_id = resolve_voice(voice, settings)
    model_id = (settings.elevenlabs_model or DEFAULT_MODEL).strip()
    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": token,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    body = {
        "text": spoken,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                raise ElevenLabsError(f"ElevenLabs HTTP {resp.status_code}: {resp.text[:300]}")
            audio = resp.content
    except httpx.HTTPError as exc:
        raise ElevenLabsError(f"ElevenLabs request failed: {exc}") from exc

    if len(audio) < 256:
        raise ElevenLabsError("ElevenLabs returned empty audio")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(audio)
    return target


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_VOICE",
    "ELEVENLABS_MODELS",
    "ElevenLabsError",
    "fetch_subscription",
    "list_voices",
    "resolve_voice",
    "synthesize",
]
