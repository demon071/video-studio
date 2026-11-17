"""Piper TTS offline ONNX engine (ported from CapCap).

Uses ``piper-tts`` (optional package) and local ``.onnx`` voice models placed
under ``models/piper/`` (or ``models/piper-en/``). If ``vietnormalizer`` is
installed, Vietnamese text is normalized before synthesis.
"""

from __future__ import annotations

import logging
import os
import threading
import wave
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

DEFAULT_VOICE = "vi_VN-vais1000-medium"

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {}


class PiperError(RuntimeError):
    """Raised when Piper TTS cannot synthesize."""


def _model_dirs(settings: Settings) -> tuple[Path, ...]:
    root = settings.models_path
    return (root / "piper", root / "piper-en")


def resolve_voice(voice: str | None, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    chosen = (voice or settings.piper_voice or DEFAULT_VOICE).strip()
    if chosen.lower().startswith("piper:"):
        chosen = chosen.split(":", 1)[1].strip()
    return chosen or DEFAULT_VOICE


def _find_model_path(voice: str, settings: Settings) -> Path:
    candidate = Path(voice)
    if candidate.is_file():
        return candidate
    stem = candidate.stem if candidate.suffix.lower() == ".onnx" else voice
    for folder in _model_dirs(settings):
        for name in (f"{stem}.onnx", voice):
            path = folder / name
            if path.is_file():
                return path
    expected = settings.models_path / "piper" / f"{stem}.onnx"
    raise PiperError(
        f"Không tìm thấy model Piper '{voice}'. Hãy đặt file .onnx (và .onnx.json) vào {expected}."
    )


def list_voices(settings: Settings | None = None) -> list[dict[str, str]]:
    """Return local Piper ``.onnx`` voice models found under ``models/piper*``."""
    settings = settings or get_settings()
    voices: list[dict[str, str]] = []
    seen: set[str] = set()
    for folder in _model_dirs(settings):
        if not folder.is_dir():
            continue
        lang = "en" if folder.name == "piper-en" else "vi"
        for onnx in sorted(folder.glob("*.onnx")):
            vid = onnx.stem
            if vid in seen:
                continue
            seen.add(vid)
            voices.append(
                {
                    "id": vid,
                    "name": vid,
                    "provider": "piper",
                    "language": lang,
                    "path": str(onnx),
                }
            )
    return voices


def _normalize_text(text: str) -> str:
    try:
        from vietnormalizer.normalizer import VietnameseNormalizer

        return VietnameseNormalizer().normalize(text) or text
    except Exception:
        return text


def _load_voice(model_path: Path) -> Any:
    key = str(model_path.resolve())
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        try:
            from piper.voice import PiperVoice
        except ImportError as exc:
            raise PiperError(
                "Piper TTS chưa được cài đặt. Chạy: pip install piper-tts"
            ) from exc
        voice = PiperVoice.load(key)
        _CACHE[key] = voice
        return voice


def synthesize(
    text: str,
    output_path: str | Path,
    *,
    voice: str | None = None,
    speed: float = 1.0,
    settings: Settings | None = None,
) -> Path:
    """Synthesize ``text`` to a WAV file using Piper TTS."""
    settings = settings or get_settings()
    chosen = resolve_voice(voice, settings)
    model_path = _find_model_path(chosen, settings)
    piper_voice = _load_voice(model_path)

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part.wav")

    try:
        from piper.config import SynthesisConfig
    except ImportError as exc:
        raise PiperError("Piper TTS chưa được cài đặt. Chạy: pip install piper-tts") from exc

    rate = min(max(float(speed or 1.0), 0.5), 2.0)
    syn_config = SynthesisConfig(length_scale=1.0 / rate)
    spoken = _normalize_text(text.strip())

    try:
        with wave.open(str(part), "wb") as wav_file:
            piper_voice.synthesize_wav(spoken, wav_file, syn_config=syn_config)
        if not part.is_file() or part.stat().st_size <= 44:
            raise PiperError("Piper TTS không tạo được âm thanh hợp lệ.")
        os.replace(part, target)
        return target
    finally:
        if part.is_file():
            try:
                part.unlink()
            except OSError:
                pass


def unload() -> None:
    """Release cached Piper ONNX voice models."""
    with _LOCK:
        _CACHE.clear()


__all__ = [
    "DEFAULT_VOICE",
    "PiperError",
    "list_voices",
    "resolve_voice",
    "synthesize",
    "unload",
]
