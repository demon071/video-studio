"""Speech-to-text & subtitle extraction (ported from CapCap v9.0.1).

Supports CapCap's four subtitle extraction engines:
* ``local``      - Faster-Whisper (standard + BatchedInferencePipeline on CUDA)
* ``sensevoice`` - SenseVoice ONNX (sherpa-onnx)
* ``capcut``     - CapCut Cloud / CapCut PC (sscronet.dll)
* ``ocr``        - Hardsub OCR (RapidOCR GPU/CPU + Windows Media OCR)
"""

from __future__ import annotations

import hashlib
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.core import hardware
from app.core.progress import NULL_REPORTER, Cancelled, Reporter
from app.schemas.subtitle import SubtitleSegment, TranscribeRequest, TranscribeResult
from app.services import (
    asr_options,
    capcut_client,
    diarization_service,
    model_manager,
    ocr_service,
    provider_config,
    sensevoice_stt,
    subtitle_timeline,
)
from app.services.media_processor import (
    MediaError,
    extract_audio,
    probe_duration,
    run_ffmpeg,
    separate_vocals,
)

log = logging.getLogger(__name__)

# Engines that take an audio file and answer with (start, end, text) tuples.
CLOUD_ENGINES = {
    "capcut": capcut_client.transcribe,
}


class SttError(RuntimeError):
    """Raised when a transcription provider fails."""


@dataclass(frozen=True)
class SttOutcome:
    language: str
    duration: float
    segments: list[SubtitleSegment]
    provider: str
    model: str
    device: str


# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------
_MODEL_LOCK = threading.Lock()
_MODELS: dict[tuple[str, str, str], Any] = {}


def unload() -> None:
    """Drop cached Whisper weights so the VRAM is free for the LLM stage."""
    with _MODEL_LOCK:
        _MODELS.clear()
    hardware.release_vram()
    log.debug("whisper cache cleared")


def loaded() -> list[str]:
    """Diagnostics for the WebSocket handshake."""
    with _MODEL_LOCK:
        return ["|".join(key) for key in _MODELS]


def _load_model(reference: str, device: str, compute_type: str, download_root: Path):
    hardware.ensure_cuda_dlls()
    if device == "cuda" and not hardware.ctranslate2_cuda_ready():
        log.warning("CUDA 12 cublas DLLs missing for ctranslate2 - falling back to CPU int8")
        device, compute_type = "cpu", "int8"
    key = (reference, device, compute_type)
    with _MODEL_LOCK:
        cached = _MODELS.get(key)
        if cached is not None:
            return cached
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SttError(
                "Offline transcription needs faster-whisper "
                "(pip install faster-whisper). Use provider=groq for the cloud path."
            ) from exc

        log.info("loading whisper %s on %s (%s)", reference, device, compute_type)
        model = WhisperModel(
            reference,
            device=device,
            compute_type=compute_type,
            download_root=str(download_root),
            cpu_threads=hardware.cpu_threads(),
            num_workers=1,
        )
        _MODELS[key] = model
        return model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _cached_wav(source: Path, settings: Settings) -> Path:
    """Extract a reusable 16 kHz mono WAV keyed by path+mtime+size."""
    stat = source.stat()
    signature = f"{source}|{stat.st_mtime_ns}|{stat.st_size}".encode()
    digest = hashlib.sha1(signature).hexdigest()[:16]  # noqa: S324 - not security
    target = settings.cache_path / "audio" / f"{digest}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target
    return extract_audio(source, target)


def _clean_key(source: Path) -> str:
    """Key the separated-vocal cache on the source file, not just its name."""
    stat = source.stat()
    return hashlib.sha1(  # noqa: S324 - not security
        f"{source}|{stat.st_mtime_ns}|{stat.st_size}|clean-v1".encode()
    ).hexdigest()[:16]


def _cleaned_wav(source: Path, settings: Settings, report: Reporter) -> Path:
    """Vocal stem only, denoised and loudness-normalised -> 16 kHz mono WAV.

    This is the reference projects' "cleaner voice" trick (CapCap's *Cleaner
    voice* audio mode): separating the vocal keeps the background music and the
    effects from dragging Whisper into hallucinating lyrics, so the lines land
    on the speech instead of on the soundtrack.
    """
    digest = _clean_key(source)
    target = settings.cache_path / "vocals" / f"{digest}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target

    report(stage="separate", fraction=0.0, message="tách giọng khỏi nhạc nền")
    vocals, _bed = separate_vocals(
        source,
        settings.temp_path / "separate" / digest,
        settings=settings,
    )

    report(stage="separate", fraction=0.7, message="khử nhiễu giọng tách được")
    target.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(vocals),
            # afftdn tames the separation artefacts, loudnorm evens out the
            # level so quiet lines are not lost by the VAD.
            "-af",
            "afftdn,loudnorm=I=-16:LRA=11:TP=-1.5",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        timeout=1800,
    )
    return target


def _waveform(wav: Path) -> Any:
    """Read the WAV into a float32 mono array for faster-whisper.

    faster-whisper decodes file inputs with PyAV (``av.open(...,
    metadata_errors=...)``), a keyword PyAV >= 14 removed. Feeding the samples
    directly keeps the engine independent of the PyAV version installed for
    audio-separator, and skips a needless decode pass.
    """
    try:
        import numpy as np  # noqa: PLC0415
        import soundfile as sf  # noqa: PLC0415

        data, rate = sf.read(str(wav), dtype="float32", always_2d=True)
        if rate != 16_000:
            log.warning("unexpected sample rate %s, letting faster-whisper decode", rate)
            return str(wav)
        mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
        arr = np.ascontiguousarray(mono, dtype=np.float32)
        # CapCap quiet-audio auto-gain normalization: boost whispered/low-volume
        # tracks (RMS < -30 dBFS) up to +12 dB peak-limited to 0.95 so Silero VAD
        # and Whisper do not drop quiet dialogue lines.
        if arr.size > 0:
            rms = float(np.sqrt(np.mean(np.square(arr))))
            peak = float(np.max(np.abs(arr)))
            if 1e-4 < rms < 0.032 and peak > 1e-4:
                target_gain = min(0.045 / rms, 0.95 / peak, 4.0)
                if target_gain > 1.15:
                    log.info("Auto-normalizing quiet audio for ASR (rms=%.4f, gain=%.2fx)", rms, target_gain)
                    arr = np.clip(arr * target_gain, -0.97, 0.97).astype(np.float32)
        return arr
    except Exception as exc:  # noqa: BLE001 - soundfile missing or odd container
        log.warning("waveform read failed (%s), falling back to file decoding", exc)
        return str(wav)


def _segments_from_cues(cues: list[subtitle_timeline.Cue]) -> list[SubtitleSegment]:
    """Convert the cleaned timeline into the contract the API returns."""
    result: list[SubtitleSegment] = []
    for position, cue in enumerate(cues, start=1):
        s = round(cue.start, 3)
        e = round(max(cue.end, cue.start + 0.2), 3)
        result.append(
            SubtitleSegment(
                index=position,
                start=s,
                end=e,
                text=cue.text,
                no_speech_prob=cue.no_speech_prob,
                original_start=s,
                original_end=e,
            )
        )
    return result


def _cues_from_remote(data: dict[str, Any], fallback_duration: float) -> list[subtitle_timeline.Cue]:
    words: list[tuple[float, float, str]] = []
    for item in data.get("words") or []:
        token = (item.get("word") or item.get("text") or "").strip()
        if not token:
            continue
        try:
            start = float(item.get("start") or 0.0)
            end = float(item.get("end") or 0.0)
        except (TypeError, ValueError):
            continue
        words.append((start, end, token))

    raw_segments = data.get("segments") or []
    cues: list[subtitle_timeline.Cue] = []
    for item in raw_segments:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start + 0.5)
        cues.append(
            subtitle_timeline.Cue(
                start=start,
                end=max(end, start + 0.2),
                text=text,
                words=[
                    word for word in words if word[0] >= start - 0.05 and word[1] <= end + 0.05
                ],
                no_speech_prob=item.get("no_speech_prob"),
            )
        )
    if not cues:
        text = (data.get("text") or "").strip()
        if not text:
            raise SttError("The transcription API returned no text")
        cues.append(
            subtitle_timeline.Cue(
                start=0.0, end=max(fallback_duration, 0.5), text=text, words=words
            )
        )
    return cues


def _words_of(raw: Any) -> list[tuple[float, float, str]]:
    """Word timings, which let the cleaner split a long cue at a real boundary."""
    words: list[tuple[float, float, str]] = []
    for word in getattr(raw, "words", None) or []:
        if isinstance(word, tuple):  # already normalised by ``_transcribe_iter``
            words.append(word)
            continue
        start = getattr(word, "start", None)
        end = getattr(word, "end", None)
        text = (getattr(word, "word", "") or "").strip()
        if start is None or end is None or not text:
            continue
        words.append((float(start), float(end), text))
    return words


# Recognition options borrowed from CapCap's `app/whisper_processor.py`, which is
# tuned for the same material (vertical short form with music and overlapping
# dialogue):
#   * ``word_timestamps`` lets the cleaner split a cue on a word boundary,
#   * the repetition penalty stops the "same sentence until the end of the clip"
#     failure mode on instrumental intros,
#   * ``hallucination_silence_threshold`` needs word timestamps to do anything.
_WHISPER_OPTIONS: dict[str, Any] = {
    "beam_size": 5,
    "word_timestamps": True,
    "hallucination_silence_threshold": 2.0,
    "repetition_penalty": 1.05,
    "condition_on_previous_text": False,  # short clips loop badly with it on
    "temperature": 0.0,
    # Voice activity is planned here; see ``_speech_windows``.
    "vad_filter": False,
}

# Silero VAD, with CapCap's numbers: a 0.35 threshold keeps whispered lines that
# the stock 0.5 drops. It only decides *which parts* of the file are decoded.
_VAD_OPTIONS: dict[str, Any] = {
    "threshold": 0.35,
    "min_speech_duration_ms": 100,
    "min_silence_duration_ms": 300,
    "speech_pad_ms": 200,
}

SAMPLE_RATE = 16000
WINDOW_SECONDS = 25.0   # one whisper window is 30 s; leave room for the tail

# Whisper's own detection reads the first 30 s of the file, which on these
# episodes is the music intro: "002 - Tập 002" came back as *ru* at 0.45 and
# the whole 53-line transcript was then written in Russian. Speech slices are
# voted on instead, and a slice only decides when it is sure of itself.
DETECT_SEGMENTS = 3
DETECT_THRESHOLD = 0.6


@dataclass(frozen=True)
class _Line:
    """One recognised line, already shifted onto the original timeline."""

    start: float
    end: float
    text: str
    words: list[tuple[float, float, str]]
    no_speech_prob: float | None


@dataclass(frozen=True)
class _DecodeInfo:
    language: str | None
    language_probability: float | None
    duration: float


def _plan_windows(islands: list[tuple[float, float]], total: float) -> list[tuple[float, float]]:
    """Cut the clip into windows of at most WINDOW_SECONDS, covering all of it.

    VAD decides *where* to cut, never what to skip. Silero reliably misses
    dialogue spoken over music: an 18 s stretch of real lines ("你等我" /
    "对不起" / "灵溪她出事了") was dropped this way, and so was a whole scene
    after the 4-minute mark. A window costs the same whether its middle is
    speech or not, so the only plan without a hole in it covers every frame.
    Cuts land on a pause whenever one is available, so a word is not split.
    """
    if total <= 0:
        return []
    cuts = [0.0]
    for left, _right in islands:
        if left - cuts[-1] >= WINDOW_SECONDS / 2:
            cuts.append(min(left, total))
    cuts.append(total)

    windows: list[tuple[float, float]] = []
    for start, end in zip(cuts, cuts[1:], strict=False):
        while end - start > WINDOW_SECONDS:
            windows.append((start, start + WINDOW_SECONDS))
            start += WINDOW_SECONDS
        if end > start:
            windows.append((start, end))
    return [(max(0.0, round(left, 3)), round(min(right, total), 3)) for left, right in windows]


def _speech_windows(audio: Any, *, vad: bool, total: float) -> list[tuple[float, float]]:
    """Where to decode, decided by VAD without touching the audio itself.

    faster-whisper's own VAD does not just skip silence: it *concatenates* the
    speech islands and drops the pauses. On drama audio the model then emits an
    early end-of-text at the first splice - a 29 s window holding six lines came
    back as one - so an 80 s opening lost every line of dialogue in it. Planning
    the windows here keeps the wave untouched and loses nothing.
    """
    whole = [(0.0, total)]
    if not vad or total <= 0:
        return whole
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps  # noqa: PLC0415
    except ImportError:  # pragma: no cover - faster-whisper is required here
        return whole

    try:
        stamps = get_speech_timestamps(audio, VadOptions(**_VAD_OPTIONS))
    except Exception as exc:  # noqa: BLE001 - a broken ONNX build must not stop STT
        log.warning("Silero VAD unavailable (%s), decoding the whole clip", exc)
        return whole
    islands = [(stamp["start"] / SAMPLE_RATE, stamp["end"] / SAMPLE_RATE) for stamp in stamps]
    windows = _plan_windows(islands, total)
    log.info("VAD found %s speech islands in %.1fs -> %s windows", len(islands), total, len(windows))
    return windows


def _detect_language(model: Any, audio: Any) -> tuple[str | None, float | None]:
    """The language of the clip, voted on over several slices that carry speech.

    ``detect_language`` with VAD skips the intro and reads ``DETECT_SEGMENTS``
    windows of up to 30 s each: a slice only settles the question when its
    probability clears ``DETECT_THRESHOLD``, otherwise the most frequent answer
    wins. A wheel without that API (or a waveform read as a file path) leaves
    the decision to the first window, as before.
    """
    if not hasattr(audio, "shape"):
        return None, None
    try:
        from faster_whisper.vad import VadOptions  # noqa: PLC0415
    except ImportError:  # pragma: no cover - faster-whisper always ships it
        return None, None
    vad = VadOptions(**_VAD_OPTIONS)
    try:
        language, probability, _all = model.detect_language(
            audio,
            vad_filter=True,
            vad_parameters=vad,
            language_detection_segments=DETECT_SEGMENTS,
            language_detection_threshold=DETECT_THRESHOLD,
        )
    except TypeError:  # older wheels: no vote, no VAD, first 30 s only
        try:
            language, probability, _all = model.detect_language(audio)
        except Exception as exc:  # noqa: BLE001 - a guess is not worth a crash
            log.warning("language detection failed (%s)", exc)
            return None, None
    except Exception as exc:  # noqa: BLE001 - decoding must go on regardless
        log.warning("language detection failed (%s)", exc)
        return None, None
    log.info("language vote over %s slices: %s (%.2f)", DETECT_SEGMENTS, language, probability)
    return language, probability


def _transcribe_iter(
    model: Any, audio: Any, *, language: str | None, vad: bool
) -> tuple[Any, _DecodeInfo]:
    """Return ``(segment_iterator, info)``, decoding one speech window at a time.

    ``info`` (language, duration) is available immediately; the segments arrive
    lazily, shifted back onto the original timeline.
    """
    total = len(audio) / SAMPLE_RATE if hasattr(audio, "__len__") else 0.0
    windows = _speech_windows(audio, vad=vad, total=total)

    def decode(window: tuple[float, float], spoken: str | None):
        start, end = window
        return model.transcribe(
            audio[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)],
            language=spoken,
            **_WHISPER_OPTIONS,
        )

    probability = None
    if language is None:
        language, probability = _detect_language(model, audio)
    first, first_info = decode(windows[0], language)
    detected = getattr(first_info, "language", None) or language
    if probability is None:
        probability = getattr(first_info, "language_probability", None)
    info = _DecodeInfo(
        language=detected,
        language_probability=probability,
        duration=total,
    )

    def stream():
        for position, window in enumerate(windows):
            start = window[0]
            if position == 0:
                segments = first
            else:
                segments, _ = decode(window, detected)
            for raw in segments:
                words = [
                    (start + left, start + right, text) for left, right, text in _words_of(raw)
                ]
                yield _Line(
                    start=start + float(raw.start),
                    end=start + float(raw.end),
                    text=(raw.text or "").strip(),
                    words=words,
                    no_speech_prob=getattr(raw, "no_speech_prob", None),
                )

    return stream(), info


# ---------------------------------------------------------------------------
# Local provider
# ---------------------------------------------------------------------------
def _transcribe_local(
    wav: Path,
    request: TranscribeRequest,
    *,
    report: Reporter,
    settings: Settings,
) -> SttOutcome:
    profile = hardware.detect()
    device = "cuda" if profile.has_cuda else "cpu"

    model_ref = (request.model or profile.whisper_model or "small").strip()

    # Pull the checkpoint through the managed downloader first so a first run
    # shows real progress instead of a motionless "loading" for several minutes.
    spec_key = f"whisper-{model_ref}"
    spec = model_manager.MODEL_INDEX.get(spec_key)
    if spec is not None and not model_manager.status(spec, settings).downloaded:
        report(stage="download", fraction=0.0, message=f"downloading whisper {model_ref}")
        try:
            model_manager.download(spec_key, report=report, settings=settings)
        except model_manager.ModelError as exc:
            raise SttError(str(exc)) from exc

    reference, download_root = model_manager.whisper_reference(model_ref, settings)

    if model_ref == profile.whisper_model:
        compute_type = profile.whisper_compute_type
    else:
        compute_type = "float16" if device == "cuda" else "int8"

    report(stage="transcribe", fraction=0.0, message=f"loading whisper {model_ref} on {device}")
    model = _load_model(reference, device, compute_type, download_root)

    duration = probe_duration(wav)
    language = None if request.language in {"", "auto"} else request.language
    opts = asr_options.normalize_asr_options(
        {
            "dedup_enabled": request.dedup_enabled,
            "dedup_similarity": request.dedup_similarity,
            "hallucination_filter_enabled": request.hallucination_filter_enabled,
            "hallucination_silence_threshold": request.hallucination_silence_threshold,
        }
    )

    audio = _waveform(wav)
    time_offset = 0.0
    if (request.start_time is not None or request.end_time is not None) and hasattr(audio, "__len__"):
        s_sec = max(0.0, float(request.start_time or 0.0))
        e_sec = min(float(request.end_time), duration) if request.end_time is not None else duration
        if e_sec > s_sec:
            audio = audio[int(s_sec * SAMPLE_RATE) : int(e_sec * SAMPLE_RATE)]
            time_offset = s_sec

    report(stage="transcribe", fraction=0.02, message="transcribing")
    iterator, info = _transcribe_iter(model, audio, language=language, vad=request.vad)

    detected = getattr(info, "language", None) or language or "auto"
    probability = getattr(info, "language_probability", None)
    if probability is not None:
        report(message=f"language {detected} ({probability * 100:.0f}%)")

    total = float(getattr(info, "duration", 0.0) or duration or 1.0)
    cues: list[subtitle_timeline.Cue] = []
    for raw in iterator:
        text = (raw.text or "").strip()
        if not text:
            continue
        words = [(time_offset + l, time_offset + r, w) for l, r, w in _words_of(raw)]
        cue = subtitle_timeline.Cue(
            start=time_offset + float(raw.start),
            end=time_offset + float(raw.end),
            text=text,
            words=words,
            no_speech_prob=getattr(raw, "no_speech_prob", None),
        )
        cues.append(cue)
        report(
            fraction=min(float(raw.end) / total, 0.999),
            message=f"line {len(cues)}",
            segment=SubtitleSegment(
                index=len(cues),
                start=round(cue.start, 3),
                end=round(max(cue.end, cue.start + 0.2), 3),
                text=text,
                no_speech_prob=cue.no_speech_prob,
            ),
        )

    if not cues:
        raise SttError("No speech detected in this clip")

    if request.polish:
        cues = subtitle_timeline.polish(
            cues,
            dedup_enabled=opts["dedup_enabled"],
            dedup_similarity=opts["dedup_similarity"],
        )
        report(stage="transcribe", fraction=0.999, message=f"{len(cues)} lines after cleanup")
    segments = _segments_from_cues(cues)
    if not segments:
        raise SttError("No speech detected in this clip")

    return SttOutcome(
        language=detected,
        duration=round(duration, 3),
        segments=segments,
        provider="local",
        model=model_ref,
        device=device,
    )


# ---------------------------------------------------------------------------
# SenseVoice (sherpa-onnx) - offline, Chinese drama
# ---------------------------------------------------------------------------
def _transcribe_sensevoice(
    wav: Path,
    request: TranscribeRequest,
    *,
    report: Reporter,
    settings: Settings,
) -> SttOutcome:
    """Chinese drama on CPU, with punctuation and without a language guess."""
    if not sensevoice_stt.available():
        raise SttError(
            "SenseVoice needs the sherpa-onnx package: pip install sherpa-onnx "
            "(or pick Whisper in the AI panel)"
        )

    language = sensevoice_stt.language_code(request.language) or "auto"
    if language not in sensevoice_stt.LANGUAGES:
        raise SttError(
            f"SenseVoice only reads {', '.join(sorted(sensevoice_stt.LANGUAGES - {'auto'}))}. "
            "Pick Whisper for this language."
        )

    spec = model_manager.require(sensevoice_stt.SPEC_KEY)
    if not sensevoice_stt.installed(settings):
        report(stage="download", fraction=0.0, message="downloading SenseVoice")
        try:
            model_manager.download(spec.key, report=report, settings=settings)
        except model_manager.ModelError as exc:
            raise SttError(str(exc)) from exc
    directory = sensevoice_stt.folder(settings)

    audio = _waveform(wav)
    total = len(audio) / SAMPLE_RATE if hasattr(audio, "__len__") else probe_duration(wav)
    # The recogniser reads at most 30 s of audio per stream and does not chunk by
    # itself, so the windows are planned here; with VAD off they are just 25 s.
    windows = (
        _speech_windows(audio, vad=True, total=total) if request.vad else _plan_windows([], total)
    )

    report(stage="transcribe", fraction=0.0, message="loading SenseVoice")
    try:
        recognizer = sensevoice_stt.load(directory, language)
    except (OSError, ValueError) as exc:
        raise SttError(f"SenseVoice could not load: {exc}") from exc

    report(stage="transcribe", fraction=0.02, message="transcribing")
    cues, heard = sensevoice_stt.read(recognizer, audio, windows, report=report)
    if not cues:
        raise SttError("No speech detected in this clip")

    if request.polish:
        cues = subtitle_timeline.polish(
            cues,
            dedup_enabled=request.dedup_enabled,
            dedup_similarity=request.dedup_similarity,
        )
        report(stage="transcribe", fraction=0.999, message=f"{len(cues)} lines after cleanup")
    segments = _segments_from_cues(cues)
    if not segments:
        raise SttError("No speech detected in this clip")

    return SttOutcome(
        language=heard or (language if language != "auto" else "auto"),
        duration=round(total, 3),
        segments=segments,
        provider="sensevoice",
        model="int8",
        device="cpu",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def _job_key(source: Path) -> str:
    """Stable folder name for one source file (frames, chunks, uploads)."""
    stat = source.stat()
    signature = f"{source}|{stat.st_mtime_ns}|{stat.st_size}".encode()
    return hashlib.sha1(signature).hexdigest()[:16]  # noqa: S324 - not security


def _outcome_from_lines(
    lines: list[tuple[float, float, str]],
    *,
    request: TranscribeRequest,
    report: Reporter,
    provider: str,
    model: str,
    device: str,
    duration: float,
    language: str,
) -> SttOutcome:
    """Shared tail of the capcut/bilibili/ocr routes."""
    if not lines:
        raise SttError("No speech detected in this clip")

    cues = [subtitle_timeline.Cue(start=start, end=end, text=text) for start, end, text in lines]
    if request.polish:
        cues = subtitle_timeline.polish(cues)
        report(stage="transcribe", fraction=0.98, message=f"{len(cues)} lines after cleanup")
    segments = _segments_from_cues(cues)
    if not segments:
        raise SttError("No speech detected in this clip")

    for position, segment in enumerate(segments, start=1):
        report(fraction=0.9 + 0.09 * position / len(segments), segment=segment)

    return SttOutcome(
        language=language,
        duration=round(duration, 3),
        segments=segments,
        provider=provider,
        model=model,
        device=device,
    )


def _transcribe_cloud(
    wav: Path,
    request: TranscribeRequest,
    *,
    provider: str,
    report: Reporter,
    settings: Settings,
) -> SttOutcome:
    """CapCut: upload the audio and read the utterances back."""
    engine = CLOUD_ENGINES[provider]
    try:
        duration = probe_duration(wav)
        lines = engine(
            wav,
            work_dir=settings.temp_path / provider / _job_key(wav),
            report=report,
            settings=settings,
        )
    except capcut_client.CapCutError as exc:
        raise SttError(str(exc)) from exc
    except MediaError as exc:
        raise SttError(str(exc)) from exc

    return _outcome_from_lines(
        lines,
        request=request,
        report=report,
        provider=provider,
        model=provider,
        device="cloud",
        duration=duration,
        language=request.language or settings.source_language,
    )


def _transcribe_ocr(
    source: Path,
    request: TranscribeRequest,
    *,
    report: Reporter,
    settings: Settings,
) -> SttOutcome:
    """Read the burned-in subtitles instead of listening to the audio."""
    # The hardsubs are drawn in the clip's own language, so an explicit source
    # language also selects the OCR recogniser ("Tự nhận" keeps the setting).
    language = (request.language or "").strip().lower()
    if language and language != "auto":
        settings = settings.model_copy(update={"ocr_language": language})
    try:
        duration = probe_duration(source)
        kwargs: dict[str, Any] = {
            "work_dir": settings.temp_path / "ocr" / _job_key(source),
            "report": report,
            "settings": settings,
        }
        if request.start_time is not None or request.end_time is not None:
            kwargs["start_time"] = float(request.start_time or 0.0)
            kwargs["end_time"] = request.end_time
        lines = ocr_service.transcribe(source, **kwargs)
    except ocr_service.OcrError as exc:
        raise SttError(str(exc)) from exc
    except MediaError as exc:
        raise SttError(str(exc)) from exc

    return _outcome_from_lines(
        lines,
        request=request,
        report=report,
        provider="ocr",
        model=f"ocr:{settings.ocr_engine}",
        # The reader logs the device it really landed on; this is the plan, and
        # it is "cpu" unless an onnxruntime GPU provider is installed.
        device=ocr_service.resolve_device(settings.ocr_device),
        duration=duration,
        language=request.language or settings.ocr_language,
    )


def transcribe(
    request: TranscribeRequest,
    *,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> TranscribeResult:
    settings = provider_config.overlay(settings or get_settings())
    source = Path(request.video_path)
    if not source.is_file():
        raise SttError(f"Video not found: {source}")

    provider = (request.provider or settings.stt_provider or "local").lower()
    mode = request.audio_mode or provider_config.audio_mode(settings)

    if provider == "ocr":
        outcome = _transcribe_ocr(source, request, report=report, settings=settings)
    else:
        if mode == "clean":
            try:
                wav = _cleaned_wav(source, settings, report)
                report(stage="audio", fraction=1.0, message=f"vocal stem ready: {wav.name}")
            except Cancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("vocal separation unavailable (%s) - using the full mix", exc)
                report(stage="audio", fraction=0.0, message="không tách được giọng, dùng âm gốc")
                mode = "fast"
        if mode != "clean":
            report(stage="audio", fraction=0.0, message="extracting 16 kHz audio")
            try:
                wav = _cached_wav(source, settings)
            except MediaError as exc:
                raise SttError(str(exc)) from exc
            report(stage="audio", fraction=1.0, message=f"audio ready: {wav.name}")

        if provider == "local":
            outcome = _transcribe_local(wav, request, report=report, settings=settings)
        elif provider == "sensevoice":
            outcome = _transcribe_sensevoice(wav, request, report=report, settings=settings)
        elif provider in CLOUD_ENGINES:
            outcome = _transcribe_cloud(wav, request, provider=provider, report=report, settings=settings)
        else:
            raise SttError(f"Unsupported STT provider: {provider}")

        if request.diarize:
            try:
                turns = diarization_service.diarize(
                    wav,
                    num_speakers=request.num_speakers,
                    report=report,
                    settings=settings,
                )
                if turns:
                    tagged = diarization_service.assign_speakers(outcome.segments, turns)
                    outcome = SttOutcome(
                        language=outcome.language,
                        duration=outcome.duration,
                        segments=tagged,
                        provider=outcome.provider,
                        model=outcome.model,
                        device=outcome.device,
                    )
            except Exception as exc:  # noqa: BLE001 - diarization is optional
                log.warning("speaker diarization skipped (%s)", exc)

    summary = f"{len(outcome.segments)} lines"
    report(stage="done", fraction=1.0, message=summary)
    log.info(
        "transcribed %s: %s lines via %s/%s (%s)",
        source.name,
        len(outcome.segments),
        outcome.provider,
        outcome.model,
        outcome.device,
    )
    return TranscribeResult(
        language=outcome.language,
        duration=outcome.duration,
        segments=outcome.segments,
        provider=outcome.provider,
        model=outcome.model,
        device=outcome.device,
    )


def check_cancelled(report: Reporter) -> None:
    """Let long loops notice a cancel request without importing the job layer."""
    try:
        report(emit=True)
    except Cancelled:
        raise


__all__ = ["SttError", "SttOutcome", "check_cancelled", "loaded", "transcribe", "unload"]
