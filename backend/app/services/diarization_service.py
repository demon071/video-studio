"""Offline speaker diarization via sherpa-onnx (ported from CapCap).

Extracts speaker turns ``(start, end, speaker)`` from a 16 kHz mono WAV using
the Sherpa-ONNX pyannote segmentation + 3D-Speaker ERes2Net embedding pair, and
assigns each subtitle segment its dominant speaker tag (``SPEAKER_00``,
``SPEAKER_01``, ...).
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.core.progress import NULL_REPORTER, Reporter
from app.schemas.subtitle import SubtitleSegment
from app.services import model_manager

log = logging.getLogger(__name__)

SPEC_KEY = "pyannote-sherpa-diarization"
SEGMENTATION_FILE = "model.int8.onnx"
EMBEDDING_FILE = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

# CapCap speaker palette (reserving cyan/teal for secondary subtitles TS2).
SPEAKER_PALETTE: tuple[str, ...] = (
    "#F59E0B",  # SPEAKER_00 - amber
    "#EC4899",  # SPEAKER_01 - pink
    "#8B5CF6",  # SPEAKER_02 - violet
    "#10B981",  # SPEAKER_03 - emerald
    "#3B82F6",  # SPEAKER_04 - blue
    "#F97316",  # SPEAKER_05 - orange
    "#EAB308",  # SPEAKER_06 - yellow
    "#EF4444",  # SPEAKER_07 - red
)


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


def speaker_color(speaker: str | None) -> str:
    """Stable UI color for a speaker ID (matches CapCap's speaker palette)."""
    if not speaker:
        return SPEAKER_PALETTE[0]
    digits = "".join(ch for ch in str(speaker) if ch.isdigit())
    if digits:
        return SPEAKER_PALETTE[int(digits) % len(SPEAKER_PALETTE)]
    return SPEAKER_PALETTE[sum(ord(ch) for ch in str(speaker)) % len(SPEAKER_PALETTE)]


def available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def folder(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    try:
        spec = model_manager.require(SPEC_KEY)
        return model_manager.model_dir(spec, settings)
    except model_manager.ModelError:
        return settings.models_path / "diarization"


def model_paths(settings: Settings | None = None) -> tuple[Path, Path]:
    settings = settings or get_settings()
    base = folder(settings)
    seg_env = os.environ.get("SPEAKER_DIARIZATION_SEGMENTATION_MODEL", "").strip()
    emb_env = os.environ.get("SPEAKER_DIARIZATION_EMBEDDING_MODEL", "").strip()
    seg = Path(seg_env) if seg_env else base / SEGMENTATION_FILE
    emb = Path(emb_env) if emb_env else base / EMBEDDING_FILE
    # Also check legacy flat `models/pyannote` folder if copied from CapCap.
    legacy = settings.models_path / "pyannote"
    if not seg.is_file() and (legacy / SEGMENTATION_FILE).is_file():
        seg = legacy / SEGMENTATION_FILE
    if not emb.is_file() and (legacy / EMBEDDING_FILE).is_file():
        emb = legacy / EMBEDDING_FILE
    return seg, emb


def installed(settings: Settings | None = None) -> bool:
    seg, emb = model_paths(settings)
    return seg.is_file() and emb.is_file()


def diarize(
    wav_path: str | Path,
    *,
    num_speakers: int = -1,
    threshold: float = 0.9,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> list[SpeakerTurn]:
    """Run Sherpa-ONNX offline speaker diarization on ``wav_path``."""
    source = Path(wav_path)
    if not source.is_file():
        raise RuntimeError(f"Audio file not found for diarization: {source}")
    try:
        import numpy as np  # noqa: PLC0415
        import sherpa_onnx  # noqa: PLC0415
        import soundfile as sf  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("Speaker diarization requires sherpa-onnx and soundfile") from exc

    seg_model, emb_model = model_paths(settings)
    missing = [str(p) for p in (seg_model, emb_model) if not p.is_file()]
    if missing:
        raise RuntimeError(
            "Speaker diarization models are missing (" + ", ".join(missing) + "). "
            "Download them in the Models tab first."
        )

    provider = str(os.environ.get("SPEAKER_DIARIZATION_PROVIDER", "cpu") or "cpu").strip().lower()
    threads = max(1, min(4, int(os.environ.get("SPEAKER_DIARIZATION_THREADS", "2") or 2)))

    segmentation = sherpa_onnx.OfflineSpeakerSegmentationModelConfig()
    segmentation.pyannote.model = str(seg_model)
    segmentation.provider = provider
    segmentation.num_threads = threads

    embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig()
    embedding.model = str(emb_model)
    embedding.provider = provider
    embedding.num_threads = threads

    clustering = sherpa_onnx.FastClusteringConfig(
        num_clusters=int(num_speakers) if int(num_speakers) >= 2 else -1,
        threshold=float(threshold),
    )
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=segmentation,
        embedding=embedding,
        clustering=clustering,
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("Invalid Sherpa-ONNX speaker diarization configuration")

    data, sample_rate = sf.read(str(source), dtype="float32", always_2d=True)
    if not len(data):
        return []
    audio = np.ascontiguousarray(data[:, 0], dtype=np.float32)

    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    target_sr = int(getattr(diarizer, "sample_rate", 16000) or 16000)
    if int(sample_rate) != target_sr:
        from scipy.signal import resample_poly  # noqa: PLC0415

        divisor = math.gcd(int(sample_rate), target_sr)
        audio = np.ascontiguousarray(
            resample_poly(audio, target_sr // divisor, int(sample_rate) // divisor),
            dtype=np.float32,
        )

    def _on_progress(done: int, total: int) -> int:
        if total > 0:
            report(
                stage="diarize",
                fraction=min(done / total, 0.99),
                message=f"phân vai người nói ({int(done * 100 / total)}%)",
            )
        return 0

    raw = diarizer.process(audio, callback=_on_progress).sort_by_start_time()
    turns = [
        SpeakerTurn(
            start=round(max(0.0, float(item.start)), 3),
            end=round(max(0.0, float(item.end)), 3),
            speaker=f"SPEAKER_{int(item.speaker):02d}",
        )
        for item in raw
        if float(item.end) - float(item.start) >= 0.05
    ]
    log.info("diarization found %s speaker turn(s) in %s", len(turns), source.name)
    return turns


def assign_speakers(
    segments: list[SubtitleSegment],
    turns: list[SpeakerTurn],
) -> list[SubtitleSegment]:
    """Tag each segment with the speaker whose turn overlaps it most (or is nearest)."""
    if not segments or not turns:
        return segments

    assigned: list[SubtitleSegment] = []
    for seg in segments:
        overlap_by_speaker: dict[str, float] = {}
        for turn in turns:
            overlap = max(0.0, min(seg.end, turn.end) - max(seg.start, turn.start))
            if overlap > 0.0:
                overlap_by_speaker[turn.speaker] = overlap_by_speaker.get(turn.speaker, 0.0) + overlap
        if overlap_by_speaker:
            best_speaker = max(overlap_by_speaker.items(), key=lambda item: item[1])[0]
        else:
            mid = (seg.start + seg.end) / 2.0
            nearest = min(
                turns,
                key=lambda t: min(abs(mid - t.start), abs(mid - t.end)),
            )
            if min(abs(mid - nearest.start), abs(mid - nearest.end)) <= 1.5:
                best_speaker = nearest.speaker
            else:
                best_speaker = seg.speaker
        assigned.append(seg.model_copy(update={"speaker": best_speaker}))
    return assigned


__all__ = [
    "EMBEDDING_FILE",
    "SEGMENTATION_FILE",
    "SPEAKER_PALETTE",
    "SPEC_KEY",
    "SpeakerTurn",
    "assign_speakers",
    "available",
    "diarize",
    "folder",
    "installed",
    "model_paths",
    "speaker_color",
]
