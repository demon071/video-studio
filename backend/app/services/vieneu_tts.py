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


def _prepare_text(text: str) -> str:
    cleaned = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if cleaned and cleaned[-1] not in ".!?,;:…":
        cleaned = f"{cleaned}."
    return cleaned


def _to_mono_float32(audio_data: Any) -> Any:
    import numpy as np

    arr = np.asarray(audio_data, dtype=np.float32)
    if arr.ndim > 1:
        arr = np.mean(arr, axis=1)
    return arr.reshape(-1)


def _find_speech_bursts(wav: Any, sr: int) -> list[tuple[int, int]]:
    """Return ``(start_ms, end_ms)`` speech islands separated by silence gaps."""
    import numpy as np
    from vieneu_utils.core_utils import _BURST_MIN_GAP_MS, _BURST_MIN_MS, _BURST_THRESH_DB

    hop = max(1, int(sr * 0.010))
    n = len(wav) // hop
    if n == 0:
        return []
    env = np.sqrt((wav[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
    peak = float(env.max())
    if peak <= 1e-6:
        return []
    on = env > peak * (10 ** (_BURST_THRESH_DB / 20))
    min_gap = max(1, _BURST_MIN_GAP_MS // 10)
    min_len = max(1, _BURST_MIN_MS // 10)
    bursts: list[tuple[int, int]] = []
    start: int | None = None
    last_on: int | None = None
    for i, active in enumerate(on):
        if active:
            if start is None:
                start = i
            elif last_on is not None and i - last_on > min_gap:
                bursts.append((start, last_on))
                start = i
            last_on = i
    if start is not None and last_on is not None:
        bursts.append((start, last_on))
    return [(a * 10, (b + 1) * 10) for a, b in bursts if (b - a + 1) >= min_len]


def _trim_repeated_tail(wav: Any, sr: int, bursts: list[tuple[int, int]], syllables: int) -> Any:
    """Trim duplicated trailing utterance at the silence gap after the first reading."""
    import numpy as np

    if len(bursts) < 2:
        return wav
    first_dur_ms = bursts[0][1] - bursts[0][0]
    if syllables <= 1 or first_dur_ms >= 220:
        end_ms, next_ms = bursts[0][1], bursts[1][0]
    elif len(bursts) >= 3:
        end_ms, next_ms = bursts[1][1], bursts[2][0]
    else:
        return wav

    cut_ms = min((end_ms + next_ms) // 2, end_ms + 80)
    cut_idx = min(len(wav), max(1, int(cut_ms * sr / 1000)))
    if cut_idx >= len(wav):
        return wav
    trimmed = wav[:cut_idx].copy()
    fade = min(len(trimmed), int(sr * 0.020))
    if fade > 0:
        trimmed[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return trimmed


def _infer_clean(model: Any, spoken: str, infer_kwargs: dict[str, Any], sr: int) -> Any:
    """Run ``model.infer`` and guard short chunks against autoregressive double-reading."""
    arr = _to_mono_float32(model.infer(spoken, **infer_kwargs))
    try:
        from vieneu_utils.core_utils import babble_prefer, babble_suspect, max_expected_frames
        from vieneu_utils.phonemize_text import phonemize_text_with_emotions
    except ImportError:
        return arr

    phonemes = phonemize_text_with_emotions(spoken)
    cap_frames = min(300, max_expected_frames(phonemes))
    best = babble_suspect(arr, sr, phonemes, cap_frames)
    if not best[0]:
        return arr

    for alt_temp in (0.75, 0.5):
        cand_opts = {**infer_kwargs, "temperature": alt_temp}
        cand_wav = _to_mono_float32(model.infer(spoken, **cand_opts))
        cand = babble_suspect(cand_wav, sr, phonemes, cap_frames)
        if babble_prefer(cand, best):
            arr, best = cand_wav, cand
        if not best[0]:
            return arr

    bursts = _find_speech_bursts(arr, sr)
    if len(bursts) >= 2:
        trimmed = _trim_repeated_tail(arr, sr, bursts, best[1])
        if len(trimmed) < len(arr):
            log.info(
                "VieNeu trimmed repeated short-chunk tail: '%s' %.2fs -> %.2fs (bursts=%s)",
                spoken,
                len(arr) / sr,
                len(trimmed) / sr,
                bursts,
            )
            return trimmed
    return arr


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
    spoken = _prepare_text(text)
    if not spoken:
        raise VieNeuError("Nội dung câu cần đọc bị trống.")

    try:
        import numpy as np  # noqa: F401
        import soundfile as sf
    except ImportError as exc:
        raise VieNeuError("VieNeu-TTS chưa có numpy/soundfile (cần gói AI).") from exc

    sr = int(getattr(model, "sample_rate", SAMPLE_RATE))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part.wav")

    infer_kwargs: dict[str, Any] = {
        "temperature": 0.6,
        "top_k": 20,
        "repetition_penalty": 1.35,
        "apply_watermark": False,
    }
    if chosen.lower().startswith("clone:"):
        ref_path = Path(chosen.split(":", 1)[1].strip())
        if not ref_path.is_file():
            raise VieNeuError(f"Không tìm thấy file giọng mẫu để clone: {ref_path}")
        infer_kwargs["ref_audio"] = str(ref_path)
        infer_kwargs["ref_text"] = ""
    else:
        infer_kwargs["voice"] = chosen if chosen in VIENEU_PRESET_VOICES else DEFAULT_VOICE

    arr = _infer_clean(model, spoken, infer_kwargs, sr)

    try:
        sf.write(str(part), arr, sr, format="WAV", subtype="PCM_16")
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
