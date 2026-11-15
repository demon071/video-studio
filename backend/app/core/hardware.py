"""Hardware probing and model planning.

The engine must run on a 4 GB laptop GPU and on a CPU-only desktop without any
manual configuration. Everything here is derived from three facts:

* is CUDA available (``torch.cuda``),
* how much VRAM is *currently* free (not total, other apps matter),
* which preset the user picked (``SVA_GPU_PRESET``: max | safe | cpu).

The result is a :class:`HardwareProfile` that services consume instead of
calling torch themselves. It is cached, but :func:`vram_snapshot` can be called
at any time to re-plan before a heavy stage.
"""

from __future__ import annotations

import gc
import logging
import os
import platform
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from app.config import Settings, get_settings, resolve_ffmpeg, resolve_ffprobe

log = logging.getLogger(__name__)

# The two offline translators: Qwen2.5-3B-Instruct has 36 transformer blocks
# (2.0 GB, Q4_K_M), Gemma 3 4B has 34 (2.4 GB). A block of the larger build plus
# its KV cache headroom costs ~75-80 MB, so plan on the worst case: over-counting
# only loads fewer layers onto the card, under-counting is a CUDA OOM mid-job.
QWEN_3B_LAYERS = 36
LAYER_VRAM_MB = 80

# Whisper checkpoints: (model, compute_type, approx VRAM in GB for fp16).
WHISPER_PLANS = {
    "max": {"cuda": ("medium", "float16", 1.9), "cpu": ("small", "int8", 0.0)},
    "safe": {"cuda": ("small", "float16", 1.0), "cpu": ("small", "int8", 0.0)},
    "cpu": {"cpu": ("small", "int8", 0.0)},
}


@dataclass(frozen=True)
class HardwareProfile:
    device: str = "cpu"
    gpu_name: str | None = None
    vram_total_gb: float = 0.0
    vram_free_gb: float = 0.0
    cuda_version: str | None = None
    torch_version: str | None = None
    cpu_count: int = 0
    ram_total_gb: float = 0.0
    platform: str = ""
    python: str = ""
    ffmpeg: str | None = None
    ffprobe: str | None = None
    preset: str = "max"
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"
    llm_gpu_layers: int = 0
    separator_device: str = "cpu"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def has_cuda(self) -> bool:
        return self.device == "cuda"


def _import_torch():
    """Import torch lazily: the engine must start even without the AI stack."""
    try:
        import torch  # noqa: PLC0415 - deliberate lazy import

        return torch
    except Exception as exc:  # noqa: BLE001
        log.warning("torch unavailable (%s) - falling back to CPU planning", exc)
        return None


def vram_snapshot() -> tuple[float, float]:
    """Return ``(free_gb, total_gb)`` for device 0, ``(0, 0)`` without CUDA."""
    torch = _import_torch()
    if torch is None or not torch.cuda.is_available():
        return 0.0, 0.0
    try:
        free, total = torch.cuda.mem_get_info(0)
        return free / 1024**3, total / 1024**3
    except Exception:  # noqa: BLE001
        props = torch.cuda.get_device_properties(0)
        total_gb = props.total_memory / 1024**3
        return total_gb * 0.7, total_gb


def release_vram() -> None:
    """Drop cached allocations so the next stage can use the memory.

    Called between pipeline stages (whisper -> LLM -> separator). Without this a
    medium Whisper model keeps ~2 GB reserved and the LLM cannot be offloaded.
    """
    gc.collect()
    torch = _import_torch()
    if torch is None or not torch.cuda.is_available():
        return
    try:
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
    except Exception as exc:  # noqa: BLE001
        log.debug("vram release failed: %s", exc)


def _ram_total_gb() -> float:
    try:
        import psutil  # noqa: PLC0415

        return round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:  # noqa: BLE001
        return 0.0


def _plan_llm_layers(device: str, free_gb: float, reserve_gb: float) -> tuple[int, str | None]:
    if device != "cuda":
        return 0, "LLM runs on CPU (no CUDA device)"

    usable = free_gb - reserve_gb
    if usable < 1.2:
        return 0, f"LLM on CPU: only {free_gb:.1f} GB VRAM free"
    if usable >= 3.4:
        return -1, None  # -1 = offload every layer

    layers = int(min(QWEN_3B_LAYERS, usable * 1024 / LAYER_VRAM_MB))
    return max(layers, 0), f"LLM partially offloaded ({layers}/{QWEN_3B_LAYERS} layers)"


def _plan_whisper(settings: Settings, device: str, free_gb: float) -> tuple[str, str, str | None]:
    preset = settings.gpu_preset if settings.gpu_preset in WHISPER_PLANS else "max"
    table = WHISPER_PLANS[preset]
    key = "cuda" if device == "cuda" and "cuda" in table else "cpu"
    model, compute_type, need_gb = table[key]

    note: str | None = None
    if key == "cuda" and need_gb and free_gb < need_gb + settings.vram_reserve_gb:
        # Max preset chosen but another app is holding the GPU: degrade instead
        # of throwing CUDA OOM mid-transcription.
        model, compute_type = "small", "float16"
        note = f"whisper degraded to small: {free_gb:.1f} GB free < {need_gb:.1f} GB needed"

    if settings.whisper_model:
        model = settings.whisper_model
    if settings.whisper_compute_type:
        compute_type = settings.whisper_compute_type

    return model, compute_type, note


@lru_cache(maxsize=1)
def detect() -> HardwareProfile:
    """Probe the machine once per process (call :func:`detect.cache_clear()`)."""
    settings = get_settings()
    notes: list[str] = []

    torch = _import_torch()
    torch_version = getattr(torch, "__version__", None) if torch else None
    cuda_version = getattr(getattr(torch, "version", None), "cuda", None) if torch else None

    device = "cpu"
    gpu_name: str | None = None
    vram_total = vram_free = 0.0

    requested = (settings.device or "auto").lower()
    if torch is not None and requested in {"auto", "cuda"} and torch.cuda.is_available():
        device = "cuda"
        gpu_name = torch.cuda.get_device_name(0)
        vram_free, vram_total = vram_snapshot()
    elif requested == "cuda":
        notes.append("SVA_DEVICE=cuda requested but CUDA is unavailable - using CPU")
    elif requested == "directml":
        notes.append("DirectML requested but not implemented in this build - using CPU")

    whisper_model, whisper_compute, whisper_note = _plan_whisper(settings, device, vram_free)
    if whisper_note:
        notes.append(whisper_note)

    llm_layers, llm_note = _plan_llm_layers(device, vram_free, settings.vram_reserve_gb)
    if llm_note:
        notes.append(llm_note)

    # Vocal separation is memory hungry but latency tolerant: keep it on CPU
    # unless the GPU has plenty of room left after Whisper.
    separator_device = "cuda" if device == "cuda" and vram_free >= settings.vram_reserve_gb + 2.0 else "cpu"

    profile = HardwareProfile(
        device=device,
        gpu_name=gpu_name,
        vram_total_gb=round(vram_total, 2),
        vram_free_gb=round(vram_free, 2),
        cuda_version=cuda_version,
        torch_version=torch_version,
        cpu_count=os.cpu_count() or 1,
        ram_total_gb=_ram_total_gb(),
        platform=f"{platform.system()} {platform.release()}",
        python=platform.python_version(),
        ffmpeg=str(resolve_ffmpeg(settings) or "") or None,
        ffprobe=str(resolve_ffprobe(settings) or "") or None,
        preset=settings.gpu_preset,
        whisper_model=whisper_model,
        whisper_compute_type=whisper_compute,
        llm_gpu_layers=llm_layers,
        separator_device=separator_device,
        notes=notes,
    )

    log.info(
        "hardware: device=%s gpu=%s vram=%.1f/%.1f GB preset=%s whisper=%s/%s llm_layers=%s",
        profile.device,
        profile.gpu_name or "-",
        profile.vram_free_gb,
        profile.vram_total_gb,
        profile.preset,
        profile.whisper_model,
        profile.whisper_compute_type,
        profile.llm_gpu_layers,
    )
    return profile


def cpu_threads(settings: Settings | None = None) -> int:
    """Threads reserved for AI work (0 means "let the runtime decide")."""
    settings = settings or get_settings()
    if settings.cpu_threads > 0:
        return settings.cpu_threads
    return max((os.cpu_count() or 4) - 2, 1)


__all__ = [
    "QWEN_3B_LAYERS",
    "HardwareProfile",
    "cpu_threads",
    "detect",
    "release_vram",
    "vram_snapshot",
]
