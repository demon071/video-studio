"""The stretches that carry sound but produced no subtitle line.

Two things go wrong on drama audio, and both leave a hole in the track:

* Whisper drops a whole window when the first second of it is music or an
  effect, so a scene can come back with every line of it missing;
* Silero VAD does not hear dialogue spoken *over* the soundtrack, so the
  window planner never even covered that stretch.

The reference project (vsub) handled the first case by listening to those
holes a second time. The same spans are exactly where a *burned-in* subtitle
can still be read when the audio cannot give it up: the picture still has it.
This module owns the measurement only - no audio, no OCR, just "here is loud
sound and here is nothing on screen to explain it".

Everything works on a 50 ms grid, which is finer than a subtitle cue and much
cheaper than per-sample work on a 45-minute episode. numpy is imported inside
the functions on purpose: it belongs to the AI stack, and an engine installed
without it must still start and simply skip this pass.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - annotations only
    import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CELLS = 20                      # 50 ms cells: the grid every span is measured on
LOUD_DB = -40.0                 # a cell above this counts as "something is playing"
SMOOTH_CELLS = 10               # 0.5 s of averaging, so a run of syllables is one island
CUE_PAD = 0.3                   # a line is drawn slightly before and after it is spoken
BRIDGE = 0.5                    # holes closer than this are one hole
MIN_GAP = 1.2                   # shorter than this cannot hide a whole line
MAX_GAP = 12.0                  # longer than this is scanned in pieces
PAD = 0.25                      # read a little around the hole: a line starts early


def load_waveform(path: str | Path) -> np.ndarray | None:
    """Mono float32 samples at 16 kHz, or ``None`` when the file cannot be read.

    soundfile ships with the AI stack (faster-whisper reads its wave the same
    way), and a remote provider run without it must still be able to skip the
    gap pass instead of failing a transcription that already succeeded.
    """
    try:
        import numpy as np  # noqa: PLC0415 - optional heavy import
        import soundfile as sf  # noqa: PLC0415
    except ImportError:
        log.warning("numpy/soundfile is not installed - gap fill skipped")
        return None
    try:
        data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001 - odd container, unreadable file
        log.warning("waveform read failed (%s) - gap fill skipped", exc)
        return None
    if not len(data):
        return None
    mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    if rate != SAMPLE_RATE:
        # Resampling here would need another dependency; the loudness of a
        # cell does not change with the sample rate, only its index does.
        mono = mono[:: max(int(round(rate / SAMPLE_RATE)), 1)]
    return np.ascontiguousarray(mono, dtype=np.float32)


def loud_cells(
    audio: np.ndarray,
    cells: int,
    *,
    sample_rate: int = SAMPLE_RATE,
    loud_db: float = LOUD_DB,
) -> np.ndarray:
    """Boolean mask, one entry per 50 ms cell, for "the track is playing here"."""
    import numpy as np  # noqa: PLC0415 - optional heavy import

    mask = np.zeros(cells, dtype=bool)
    window = max(int(sample_rate // CELLS), 1)
    frames = len(audio) // window
    if frames <= 0:
        return mask
    block = np.asarray(audio[: frames * window], dtype=np.float32).reshape(frames, window)
    rms = np.sqrt(np.mean(np.square(block), axis=1)) + 1e-9
    loud = 20.0 * np.log10(rms) > loud_db
    if frames >= SMOOTH_CELLS:
        kernel = np.ones(SMOOTH_CELLS, dtype=np.float32) / SMOOTH_CELLS
        loud = np.convolve(loud.astype(np.float32), kernel, mode="same") > 0.5
    keep = min(frames, cells)
    mask[:keep] = loud[:keep]
    return mask


def _runs(mask: Any, bridge: int) -> list[tuple[int, int]]:
    """Index runs of ``True``, joining runs separated by less than ``bridge`` cells.

    Subtitle lines are drawn with a short gap between them; without bridging,
    one hole comes back as a dozen fragments of 0.3 s each and none of them is
    long enough to be worth scanning.
    """
    runs: list[tuple[int, int]] = []
    count = len(mask)
    index = 0
    while index < count:
        if not mask[index]:
            index += 1
            continue
        end = index
        while end < count and (mask[end] or mask[end : end + bridge].any()):
            end += 1
        runs.append((index, end))
        index = end
    return runs


def unread_windows(
    audio: np.ndarray,
    cues: list[tuple[float, float]],
    *,
    sample_rate: int = SAMPLE_RATE,
    total: float | None = None,
    loud_db: float = LOUD_DB,
    min_gap: float = MIN_GAP,
    max_gap: float = MAX_GAP,
    pad: float = PAD,
    cue_pad: float = CUE_PAD,
) -> list[tuple[float, float]]:
    """Spans of loud audio that no cue covers, in playing order.

    ``cues`` are the lines already recognised, as ``(start, end)`` pairs. A cue
    covers itself plus ``cue_pad`` on both sides: the waveform of a line starts
    a beat before its text does, so without that margin every single subtitle
    boundary would be reported as a hole of its own.
    """
    import numpy as np  # noqa: PLC0415 - optional heavy import

    if not len(audio):
        return []
    span = float(total) if total else len(audio) / max(sample_rate, 1)
    cells = int(span * CELLS) + 1
    if cells <= 1:
        return []

    candidate = loud_cells(audio, cells, sample_rate=sample_rate, loud_db=loud_db)
    covered = np.zeros(cells, dtype=bool)
    for start, end in cues:
        left = max(int((float(start) - cue_pad) * CELLS), 0)
        right = min(int((float(end) + cue_pad) * CELLS) + 1, cells)
        if right > left:
            covered[left:right] = True

    windows: list[tuple[float, float]] = []
    for begin, finish in _runs(candidate & ~covered, max(int(BRIDGE * CELLS), 1)):
        start = max(begin / CELLS - pad, 0.0)
        end = min(finish / CELLS + pad, span)
        if end - start < min_gap:
            continue
        # A long stretch is scanned in equal pieces, never as one window: an
        # OCR read has to land on a single cue, and a 40 s window lands on
        # fifteen of them. Equal pieces also mean no orphan tail of 0.2 s.
        pieces = max(math.ceil((end - start) / max_gap), 1)
        step = (end - start) / pieces
        for index in range(pieces):
            left = start + index * step
            right = end if index == pieces - 1 else start + (index + 1) * step
            windows.append((round(left, 3), round(right, 3)))
    return windows


__all__ = ["CELLS", "MAX_GAP", "MIN_GAP", "SAMPLE_RATE", "load_waveform", "loud_cells", "unread_windows"]
