"""VoiceStudio: dubbing through a local OmniVoice server over its HTTP API.

`VoiceStudio <https://github.com/debpalash/voicestudio>`_ is the desktop app the
OmniVoice authors' model ships in; its backend answers the OpenAI speech routes
(``POST /v1/audio/speech``, ``GET /v1/audio/voices``) on ``127.0.0.1:3900``. That
makes the integration a plain HTTPS call - no torch, no weights inside this app -
and the same client also drives a Docker/remote backend on another machine.

Loopback callers need no credential; a backend started with
``OMNIVOICE_API_KEY`` (or a Docker/remote one) wants ``Authorization: Bearer``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:3900/v1"
DEFAULT_MODEL = "tts-1"
DEFAULT_VOICE = "alloy"
# A line on a CPU-only backend can take a while; the connect timeout stays short
# so a closed server fails immediately instead of hanging the whole dubbing job.
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)


class VoiceStudioError(RuntimeError):
    """Raised when the VoiceStudio backend cannot speak."""


def base_url(settings: Settings) -> str:
    """The stored endpoint, normalised to the ``.../v1`` root."""
    raw = (settings.voicestudio_base_url or DEFAULT_URL).strip()
    if not raw:
        return DEFAULT_URL
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"http://{raw}"
    raw = raw.rstrip("/")
    # "http://host:3900" carries no path: the routes live under /v1.
    if raw.count("/") == 2:
        raw = f"{raw}/v1"
    return raw


def api_key(settings: Settings) -> str:
    stored = (settings.voicestudio_api_key or "").strip()
    if stored:
        return stored
    return (os.environ.get("OMNIVOICE_API_KEY") or os.environ.get("VOICESTUDIO_API_KEY") or "").strip()


def _headers(settings: Settings) -> dict[str, str]:
    key = api_key(settings)
    return {"Authorization": f"Bearer {key}"} if key else {}


def resolve_voice(voice: str | None, settings: Settings) -> str:
    chosen = (voice or "").strip()
    if chosen.lower().startswith("voicestudio:"):
        chosen = chosen.split(":", 1)[-1].strip()
    return chosen or (settings.voicestudio_voice or "").strip() or DEFAULT_VOICE


def _url(settings: Settings, path: str) -> str:
    return f"{base_url(settings)}/{path.lstrip('/')}"


def _failure(response: httpx.Response) -> VoiceStudioError:
    """Turn a status code into something a user can act on."""
    hint = {
        401: "API key không hợp lệ — kiểm tra OMNIVOICE_API_KEY của server.",
        403: "Server từ chối (API key hoặc mạng chia sẻ).",
        404: "Sai URL hoặc server thiếu route /audio/speech — cập nhật VoiceStudio.",
        422: "Server không nhận giọng đọc này — chọn một giọng có trong VoiceStudio.",
        503: "Model chưa sẵn sàng trên server — mở VoiceStudio tải model rồi thử lại.",
    }.get(response.status_code, "")
    detail = response.text[:300].strip()
    return VoiceStudioError(f"VoiceStudio trả về HTTP {response.status_code}. {hint} {detail}".strip())


def synthesize(
    text: str,
    target: str | Path,
    *,
    voice: str | None = None,
    settings: Settings | None = None,
) -> Path:
    """Speak ``text`` into ``target`` (WAV) and return the written path."""
    settings = settings or get_settings()
    spoken = (text or "").strip()
    if not spoken:
        raise VoiceStudioError("No text to speak")

    speaker = resolve_voice(voice, settings)
    payload: dict[str, Any] = {
        "model": (settings.voicestudio_model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "voice": speaker,
        "input": spoken,
        "response_format": "wav",
    }
    try:
        response = httpx.post(
            _url(settings, "audio/speech"),
            json=payload,
            headers=_headers(settings),
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise VoiceStudioError(
            f"Không kết nối được VoiceStudio ở {base_url(settings)} ({exc}). "
            "Mở app VoiceStudio hoặc chạy Docker rồi thử lại."
        ) from exc

    if response.status_code >= 400:
        raise _failure(response)

    body = response.content
    if len(body) < 512:
        raise VoiceStudioError("VoiceStudio produced no audio")
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(body)
    except OSError as exc:
        raise VoiceStudioError(f"VoiceStudio could not write {path.name}: {exc}") from exc
    return path


def _voice_names(payload: Any) -> list[str]:
    """Read the ids out of ``/audio/voices`` whatever wrapper it uses."""
    rows: Any = payload
    if isinstance(payload, dict):
        for key in ("voices", "data", "items"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        else:
            rows = []
    names: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str):
                names.append(row.strip())
            elif isinstance(row, dict):
                for key in ("id", "voice_id", "name", "voice"):
                    value = row.get(key)
                    if isinstance(value, str) and value.strip():
                        names.append(value.strip())
                        break
    return [name for name in names if name]


def _fetch_voices(settings: Settings) -> list[str]:
    try:
        response = httpx.get(_url(settings, "audio/voices"), headers=_headers(settings), timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        raise VoiceStudioError(
            f"Không kết nối được VoiceStudio ở {base_url(settings)} ({exc}). "
            "Mở app VoiceStudio hoặc chạy Docker rồi thử lại."
        ) from exc
    if response.status_code >= 400:
        raise _failure(response)
    try:
        return _voice_names(response.json())
    except ValueError as exc:
        raise VoiceStudioError("VoiceStudio trả về danh sách giọng không đọc được") from exc


def voices(settings: Settings | None = None) -> list[str]:
    """The voices the backend offers, best effort (empty when it cannot answer)."""
    try:
        return _fetch_voices(settings or get_settings())
    except VoiceStudioError as exc:
        log.warning("VoiceStudio voice list unavailable (%s)", exc)
        return []


def reachable(settings: Settings | None = None) -> str:
    """One-line summary for the Settings panel's connection test."""
    names = _fetch_voices(settings or get_settings())
    if not names:
        return "Kết nối được nhưng chưa đọc được danh sách giọng."
    listed = ", ".join(names[:5])
    more = "" if len(names) <= 5 else f" (+{len(names) - 5})"
    return f"VoiceStudio · {len(names)} giọng: {listed}{more}"


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_URL",
    "DEFAULT_VOICE",
    "VoiceStudioError",
    "api_key",
    "base_url",
    "reachable",
    "resolve_voice",
    "synthesize",
    "voices",
]
