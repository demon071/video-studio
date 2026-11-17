"""VieNeu-TTS offline Vietnamese engine (ported from CapCap).

Uses ``vieneu`` (``Vieneu(mode="v3turbo", backend="onnx", precision="int8")``)
with CapCap's 20 preset Vietnamese voices and optional ``clone:<wav>`` support.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

DEFAULT_VOICE = "Ngọc Huyền"
SAMPLE_RATE = 48_000
OUTPUT_SAMPLE_RATE = 24_000

VIENEU_PRESET_VOICES: dict[str, dict[str, str]] = {
    "Ngọc Huyền": {"name": "Ngọc Huyền", "gender": "female", "desc": "Nữ · Miền Bắc · Giọng đọc tự nhiên, đời thường"},
    "Minh Đức": {"name": "Minh Đức", "gender": "male", "desc": "Nam · Miền Bắc · Phong cách tin tức, phóng sự"},
    "Phạm Tuyên": {"name": "Phạm Tuyên", "gender": "male", "desc": "Nam · Miền Bắc · Trầm ấm, đối thoại tự nhiên"},
    "Thái Sơn": {"name": "Thái Sơn", "gender": "male", "desc": "Nam · Miền Nam · Phong cách kể chuyện, podcast"},
    "Xuân Vĩnh": {"name": "Xuân Vĩnh", "gender": "male", "desc": "Nam · Miền Nam · Giọng ấm, dẫn chuyện tự nhiên"},
    "Thanh Bình": {"name": "Thanh Bình", "gender": "male", "desc": "Nam · Miền Bắc · Truyền cảm, kể chuyện, sách nói"},
    "Trúc Ly": {"name": "Trúc Ly", "gender": "female", "desc": "Nữ · Miền Bắc · Giọng trẻ, nhẹ nhàng, tự nhiên"},
    "Ngọc Linh": {"name": "Ngọc Linh", "gender": "female", "desc": "Nữ · Miền Bắc · Phong cách kể chuyện, diễn cảm"},
    "Đoan Trang": {"name": "Đoan Trang", "gender": "female", "desc": "Nữ · Miền Bắc · Trong trẻo, đối thoại tự nhiên"},
    "Mai Anh": {"name": "Mai Anh", "gender": "female", "desc": "Nữ · Miền Bắc · Dõng dạc, phong cách bản tin"},
    "Thục Đoan": {"name": "Thục Đoan", "gender": "female", "desc": "Nữ · Miền Nam · Ngọt ngào, kể chuyện, radio"},
    "Minh Triết": {"name": "Minh Triết", "gender": "male", "desc": "Nam · Miền Nam · Chững chạc, đọc tin tức, thời sự"},
    "Thùy Dung": {"name": "Thùy Dung", "gender": "female", "desc": "Nữ · Miền Nam · Lưu loát, đọc tin tức, phóng sự"},
    "Quang Sơn": {"name": "Quang Sơn", "gender": "male", "desc": "Nam · Miền Trung · Giọng miền Trung ấm áp, tự nhiên"},
    "Ngọc Trân": {"name": "Ngọc Trân", "gender": "female", "desc": "Nữ · Miền Trung · Dịu dàng, giọng Trung truyền cảm"},
    "Mỹ Duyên": {"name": "Mỹ Duyên", "gender": "female", "desc": "Nữ · Miền Nam · Đọc truyện, sâu lắng, audiobook"},
    "Quỳnh Anh": {"name": "Quỳnh Anh", "gender": "female", "desc": "Nữ · Miền Bắc · Đọc truyện, diễn cảm, tâm sự"},
    "Đức Trí": {"name": "Đức Trí", "gender": "male", "desc": "Nam · Miền Nam · Trầm hùng, đọc truyện, thuyết minh"},
    "Kim Thanh": {"name": "Kim Thanh", "gender": "female", "desc": "Nữ · Miền Nam · Diễn cảm, đọc truyện, tiểu thuyết"},
    "Adam": {"name": "Adam", "gender": "male", "desc": "Nam · Miền Nam · Giọng trẻ, năng động, tự nhiên"},
}

_LOCK = threading.Lock()
_MODEL: Any = None


class VieNeuError(RuntimeError):
    """Raised when VieNeu-TTS cannot synthesize."""


def resolve_voice(voice: str | None, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    chosen = (voice or settings.vieneu_voice or DEFAULT_VOICE).strip()
    if chosen.lower().startswith("vieneu:"):
        chosen = chosen.split(":", 1)[1].strip()
    return chosen or DEFAULT_VOICE


def list_voices() -> list[dict[str, str]]:
    """Return CapCap's 20 preset Vietnamese voices for VieNeu-TTS."""
    return [
        {
            "id": vid,
            "name": f"{meta['name']} ({meta['desc']})",
            "provider": "vieneu",
            "gender": meta["gender"],
            "language": "vi",
        }
        for vid, meta in VIENEU_PRESET_VOICES.items()
    ]


def _setup_hf_home(settings: Settings) -> None:
    local_vieneu = settings.models_path / "vieneu"
    if local_vieneu.is_dir():
        os.environ.setdefault("HF_HOME", str(local_vieneu))


def _load_model(settings: Settings) -> Any:
    global _MODEL
    with _LOCK:
        if _MODEL is not None:
            return _MODEL
        _setup_hf_home(settings)
        try:
            from vieneu import Vieneu
        except ImportError as exc:
            raise VieNeuError(
                "VieNeu-TTS chưa được cài đặt. Chạy: pip install vieneu"
            ) from exc

        cpu_cnt = os.cpu_count() or 4
        threads = max(1, min(cpu_cnt - 2, 10)) if cpu_cnt >= 8 else min(cpu_cnt, 4)
        precision = os.getenv("CAPCAP_VIENEU_PRECISION", "int8").strip().lower()
        if precision not in ("fp32", "int8"):
            precision = "int8"
        _MODEL = Vieneu(mode="v3turbo", backend="onnx", precision=precision, threads=threads)
        return _MODEL


def synthesize(
    text: str,
    output_path: str | Path,
    *,
    voice: str | None = None,
    settings: Settings | None = None,
) -> Path:
    """Synthesize ``text`` to a WAV file using VieNeu-TTS."""
    settings = settings or get_settings()
    chosen = resolve_voice(voice, settings)
    model = _load_model(settings)

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part.wav")

    if chosen.lower().startswith("clone:"):
        ref_path = Path(chosen.split(":", 1)[1].strip())
        if not ref_path.is_file():
            raise VieNeuError(f"Không tìm thấy file giọng mẫu để clone: {ref_path}")
        audio_data = model.infer(text.strip(), ref_audio=str(ref_path), ref_text="")
    else:
        preset = chosen if chosen in VIENEU_PRESET_VOICES else DEFAULT_VOICE
        audio_data = model.infer(text.strip(), voice=preset)

    arr = np.asarray(audio_data, dtype=np.float32)
    if arr.ndim > 1:
        arr = np.mean(arr, axis=1)

    try:
        sf.write(str(part), arr, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        if not part.is_file() or part.stat().st_size <= 44:
            raise VieNeuError("VieNeu-TTS không tạo được âm thanh hợp lệ.")
        os.replace(part, target)
        return target
    finally:
        if part.is_file():
            try:
                part.unlink()
            except OSError:
                pass


def unload() -> None:
    """Unload the cached VieNeu-TTS ONNX model and free RAM."""
    global _MODEL
    with _LOCK:
        if _MODEL is not None:
            _MODEL = None
            gc.collect()


__all__ = [
    "DEFAULT_VOICE",
    "VIENEU_PRESET_VOICES",
    "VieNeuError",
    "list_voices",
    "resolve_voice",
    "synthesize",
    "unload",
]
