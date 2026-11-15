"""FFmpeg media engine.

Every audio/video primitive the pipeline needs lives here and nowhere else:

* ``extract_audio``          video -> 16 kHz mono WAV (Whisper input)
* ``separate_vocals``        MDX-Net ONNX separation, keeps the music bed
* ``stretch_to_fit``         atempo time-compression so a TTS line fits [start, end]
* ``build_dub_track``        timestamp-aligned assembly of all TTS clips
* ``mix_background``         duck a bed under the dub (sidechain compression)
* ``render_final``           scale/crop, burn .ass, mux audio, H.264 + AAC

All calls are synchronous subprocess invocations; the API layer runs them in a
worker thread (``anyio.to_thread``) so the event loop stays responsive.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings, resolve_ffmpeg, resolve_ffprobe
from app.services import model_manager

log = logging.getLogger(__name__)

# Keep console windows from flashing in front of the Tauri window.
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_STARTUPINFO = None
if os.name == "nt":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW

ProgressFn = Callable[[float], None]  # 0.0 .. 1.0

TARGET_ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


class MediaError(RuntimeError):
    """Raised when FFmpeg (or a media dependency) fails."""


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    duration: float
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


@dataclass(frozen=True)
class SyncReport:
    """Result of fitting one TTS clip into a subtitle slot."""

    index: int
    start: float
    end: float
    target_duration: float
    tts_duration: float
    applied_ratio: float
    clamped: bool
    overflow_s: float
    output_path: Path


# ---------------------------------------------------------------------------
# Binary resolution / process plumbing
# ---------------------------------------------------------------------------
def ffmpeg_path() -> str:
    path = resolve_ffmpeg()
    if not path:
        raise MediaError(
            "ffmpeg.exe not found. Install it (winget install Gyan.FFmpeg) "
            "or set SVA_FFMPEG_DIR."
        )
    return str(path)


def ffprobe_path() -> str:
    path = resolve_ffprobe()
    if not path:
        raise MediaError("ffprobe.exe not found next to ffmpeg.")
    return str(path)


def _run(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    progress: ProgressFn | None = None,
    duration: float | None = None,
    cwd: str | Path | None = None,
) -> str:
    """Run ffmpeg/ffprobe and return stdout.

    When ``progress`` is given, ``-progress pipe:1 -nostats`` is appended and the
    key=value stream is parsed into a 0..1 fraction of ``duration``.
    """
    cmd = list(args)
    if progress is not None:
        cmd = [*cmd[:-1], "-progress", "pipe:1", "-nostats", cmd[-1]]

    log.debug("exec: %s", " ".join(cmd))
    started = time.monotonic()

    process = subprocess.Popen(  # noqa: S603 - args are built internally
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        creationflags=_CREATE_NO_WINDOW,
        startupinfo=_STARTUPINFO,
    )

    stdout_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        stdout_lines.append(line)
        if progress is None or not duration:
            continue
        match = re.match(r"out_time_ms=(\d+)", line.strip())
        if match:
            seconds = int(match.group(1)) / 1_000_000
            progress(min(seconds / duration, 0.999))

    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        process.kill()
        raise MediaError(f"FFmpeg timed out after {timeout}s") from None

    if process.returncode != 0:
        tail = "\n".join((stderr or "").strip().splitlines()[-12:])
        raise MediaError(f"FFmpeg failed (exit {process.returncode}):\n{tail}")

    if progress is not None:
        progress(1.0)
    log.debug("done in %.2fs", time.monotonic() - started)
    return "".join(stdout_lines)


def _base(*, quiet: bool = True) -> list[str]:
    return [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *(["-loglevel", "error"] if quiet else [])]


def run_ffmpeg(args: Sequence[str], *, timeout: float | None = None) -> str:
    """Run ffmpeg with the project's quiet defaults and return its stdout.

    Services that only need one conversion (the cloud uploaders, the OCR frame
    sampler) use this instead of re-implementing the process plumbing.
    """
    return _run([*_base(), *args], timeout=timeout)


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def _probe_json(path: str | Path) -> dict:
    result = _run(
        [
            ffprobe_path(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    import json  # noqa: PLC0415 - tiny local import keeps module import cheap

    return json.loads(result or "{}")


def probe_duration(path: str | Path) -> float:
    data = _probe_json(path)
    duration = data.get("format", {}).get("duration")
    if duration is not None:
        return float(duration)
    for stream in data.get("streams", []):
        if stream.get("duration"):
            return float(stream["duration"])
    raise MediaError(f"Cannot read duration of {path}")


def probe_audio(path: str | Path) -> AudioInfo:
    data = _probe_json(path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            return AudioInfo(
                path=Path(path),
                duration=float(data.get("format", {}).get("duration") or 0.0),
                sample_rate=int(stream.get("sample_rate") or 0),
                channels=int(stream.get("channels") or 0),
            )
    raise MediaError(f"No audio stream in {path}")


def probe_video(path: str | Path) -> VideoInfo:
    data = _probe_json(path)
    width = height = 0
    fps = 0.0
    has_audio = False
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not width:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            rate = stream.get("avg_frame_rate") or "0/1"
            try:
                numerator, denominator = rate.split("/")
                fps = float(numerator) / float(denominator or 1)
            except (ValueError, ZeroDivisionError):
                fps = 0.0
        elif stream.get("codec_type") == "audio":
            has_audio = True

    if not width or not height:
        raise MediaError(f"No video stream in {path}")

    return VideoInfo(
        path=Path(path),
        duration=float(data.get("format", {}).get("duration") or 0.0),
        width=width,
        height=height,
        fps=round(fps, 3),
        has_audio=has_audio,
    )


# ---------------------------------------------------------------------------
# Extraction & separation
# ---------------------------------------------------------------------------
def extract_audio(
    video_path: str | Path,
    output_path: str | Path | None = None,
    *,
    sample_rate: int = 16_000,
    mono: bool = True,
) -> Path:
    """Extract a Whisper-ready WAV (16 kHz mono by default)."""
    source = Path(video_path)
    if not source.is_file():
        raise MediaError(f"Source not found: {source}")

    target = Path(output_path) if output_path else source.with_suffix(".wav")
    target.parent.mkdir(parents=True, exist_ok=True)

    filters = []
    if mono:
        filters.append("pan=mono|c0=0.5*c0+0.5*c1" if _is_stereo(source) else "anull")

    _run(
        [
            *_base(),
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1" if mono else "2",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            *(["-af", ",".join(filters)] if filters else []),
            str(target),
        ]
    )
    return target


def _is_stereo(path: Path) -> bool:
    try:
        return probe_audio(path).channels >= 2
    except MediaError:
        return False


def separate_vocals(
    audio_path: str | Path,
    output_dir: str | Path,
    *,
    model: str = "UVR-MDX-NET-Inst_HQ_3.onnx",
    device: str = "cpu",
    settings: Settings | None = None,
) -> tuple[Path, Path]:
    """Split a mix into ``(vocals, instrumental)`` with audio-separator (MDX-Net).

    Used twice: the instrumental stem keeps the background music alive while a
    dub plays on top, and the vocal stem alone is what the *clean* audio mode
    sends to Whisper.
    """
    try:
        from audio_separator.separator import Separator  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise MediaError(
            "Vocal isolation needs audio-separator (pip install audio-separator). "
            "Dubbing still works, the music bed just cannot be preserved."
        ) from exc

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = settings or get_settings()
    # audio-separator looks for the .onnx directly inside model_file_dir and
    # downloads it again otherwise, so point at the managed sub-folder.
    managed = model_manager.model_dir(model_manager.require("uvr-mdx-inst-hq3"), settings)
    model_file_dir = managed if (managed / model).is_file() else settings.models_path / "separator"
    separator = Separator(
        output_dir=str(output_dir),
        output_format="wav",
        model_file_dir=str(model_file_dir),
        use_autocast=True,
    )
    separator.load_model(model_filename=model)
    if device == "cuda":
        try:
            separator.onnx_execution_provider = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        except Exception:  # noqa: BLE001 - older API builds
            log.debug("could not set CUDA provider for separator")

    stems = separator.separate(str(audio_path))
    vocals_path = instrumental_path = None
    for stem in stems:
        stem_path = output_dir / Path(stem).name
        lowered = stem_path.name.lower()
        if "vocal" in lowered:
            vocals_path = stem_path
        elif "instrument" in lowered or "no_vocal" in lowered:
            instrumental_path = stem_path

    if not instrumental_path or not vocals_path:
        raise MediaError(f"Separation produced unexpected stems: {stems}")

    return vocals_path, instrumental_path


# ---------------------------------------------------------------------------
# atempo time-stretching
# ---------------------------------------------------------------------------
def atempo_chain(ratio: float) -> str:
    """Build an ``atempo`` chain for ratios outside the 0.5-2.0 single-stage range.

    ffmpeg's atempo maps each factor onto a filter instance; stacking them keeps
    pitch intact (WSOLA) instead of resampling.
    """
    if ratio <= 0:
        raise ValueError("ratio must be > 0")

    remaining = ratio
    factors: list[float] = []
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.5f}" for factor in factors)


def stretch_to_fit(
    source_audio: str | Path,
    output_path: str | Path,
    *,
    index: int,
    start: float,
    end: float,
    max_ratio: float | None = None,
    sample_rate: int = 24_000,
) -> SyncReport:
    """Compress a TTS clip so it lands inside ``[start, end]``.

    ``R = T_tts / T_orig`` is the required speed factor. It is clamped to
    ``max_ratio`` (default ``SVA_MAX_TEMPO_RATIO`` = 1.35) because beyond that the
    voice starts to sound sped up; a clamped clip is additionally trimmed so it can
    never bleed into the next subtitle.
    """
    settings = get_settings()
    max_ratio = max_ratio or settings.max_tempo_ratio
    source = Path(source_audio)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    target_duration = max(end - start, 0.05)
    tts_duration = probe_duration(source)
    ratio = tts_duration / target_duration if target_duration else 1.0
    clamped = ratio > max_ratio
    applied = min(ratio, max_ratio) if ratio > 1.0 else 1.0
    # Speech that survives the speed-up but still does not fit is trimmed, so
    # the UI can warn "this line will be cut" instead of silently losing words.
    fitted = tts_duration / applied if ratio > 1.02 else tts_duration
    overflow = max(fitted - target_duration, 0.0)

    filters: list[str] = []
    if ratio > 1.02:
        filters.append(atempo_chain(applied))
    # Guarantee the clip never crosses into the next subtitle line.
    filters.append(f"atrim=0:{target_duration:.3f}")
    filters.append("asetpts=PTS-STARTPTS")
    filters.append(f"aresample={sample_rate}")
    filters.append("aformat=channel_layouts=mono")

    _run(
        [
            *_base(),
            "-i",
            str(source),
            "-af",
            ",".join(filters),
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )

    return SyncReport(
        index=index,
        start=start,
        end=end,
        target_duration=round(target_duration, 3),
        tts_duration=round(tts_duration, 3),
        applied_ratio=round(applied, 4),
        clamped=clamped,
        overflow_s=round(overflow, 3),
        output_path=target,
    )


# ---------------------------------------------------------------------------
# Track assembly
# ---------------------------------------------------------------------------
def build_dub_track(
    clips: Sequence[tuple[float, str | Path]],
    output_path: str | Path,
    *,
    total_duration: float,
    sample_rate: int = 24_000,
    progress: ProgressFn | None = None,
) -> Path:
    """Place every TTS clip at its timeline offset and mix them into one WAV.

    ``clips`` is ``[(start_seconds, wav_path), ...]``. A silent bed of
    ``total_duration`` is mixed in so the result always covers the whole video.
    """
    if not clips:
        raise MediaError("No TTS clips to assemble")

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    inputs: list[str] = []
    for _, clip in clips:
        inputs.extend(["-i", str(clip)])
    inputs.extend(["-f", "lavfi", "-t", f"{total_duration:.3f}", "-i", f"anullsrc=r={sample_rate}:cl=mono"])

    filter_parts: list[str] = []
    labels: list[str] = []
    for position, (start, _) in enumerate(clips):
        delay_ms = max(int(round(start * 1000)), 0)
        filter_parts.append(f"[{position}:a]adelay={delay_ms}:all=1[a{position}]")
        labels.append(f"[a{position}]")

    silent_index = len(clips)
    mix_inputs = "".join(labels) + f"[{silent_index}:a]"
    filter_parts.append(
        f"{mix_inputs}amix=inputs={len(clips) + 1}:normalize=0:dropout_transition=0,"
        f"alimiter=limit=0.97,atrim=0:{total_duration:.3f}[out]"
    )

    _run(
        [
            *_base(),
            *inputs,
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[out]",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        progress=progress,
        duration=total_duration,
    )
    return target


def mix_background(
    dub_path: str | Path,
    background_path: str | Path,
    output_path: str | Path,
    *,
    background_gain: float = 0.35,
    sample_rate: int = 48_000,
) -> Path:
    """Duck a bed (original track or a separated stem) under the dub, then mix."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    filter_complex = (
        f"[0:a]aresample={sample_rate},volume={background_gain:.3f}[bg];"
        f"[bg][1:a]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=280:makeup=1[ducked];"
        f"[ducked][1:a]amix=inputs=2:normalize=0:dropout_transition=0,"
        f"alimiter=limit=0.97[a]"
    )

    _run(
        [
            *_base(),
            "-i",
            str(background_path),
            "-i",
            str(dub_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[a]",
            "-ar",
            str(sample_rate),
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(target),
        ]
    )
    return target


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def escape_filter_path(path: str | Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument.

    Inside ``subtitles=`` / ``ass=`` a Windows ``C:\\dir\\a.ass`` must become
    ``C\\:/dir/a.ass``. Callers are encouraged to pass a relative filename by
    using ``cwd`` instead, which avoids the whole problem.
    """
    text = str(path).replace("\\", "/")
    text = text.replace(":", "\\:").replace("'", r"\\'")
    return text


def render_final(
    video_path: str | Path,
    output_path: str | Path,
    *,
    aspect: str = "9:16",
    subtitle_path: str | Path | None = None,
    dub_track_path: str | Path | None = None,
    background_path: str | Path | None = None,
    background_gain: float = 0.35,
    keep_original_audio: bool = False,
    crf: int = 20,
    preset: str = "medium",
    progress: ProgressFn | None = None,
) -> Path:
    """Produce the final deliverable: framed video, burned subtitles, dub audio.

    A dub is laid *over* the clip, never instead of it: with
    ``keep_original_audio`` the clip's own soundtrack stays in the mix at
    ``background_gain`` and a sidechain compressor dips it further under every
    spoken line, so the music and room tone of the original survive.
    """
    source = Path(video_path)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    info = probe_video(source)
    duration = info.duration or probe_duration(source)

    inputs: list[str] = ["-i", str(source)]
    next_index = 1
    dub_index = bg_index = None
    if dub_track_path:
        inputs.extend(["-i", str(dub_track_path)])
        dub_index = next_index
        next_index += 1
    if background_path:
        inputs.extend(["-i", str(background_path)])
        bg_index = next_index
        next_index += 1

    # --- video chain -------------------------------------------------------
    width, height = TARGET_ASPECTS.get(aspect, (info.width, info.height))
    width -= width % 2
    height -= height % 2
    filters: list[str] = []
    if aspect in TARGET_ASPECTS:
        filters.append(f"scale={width}:{height}:force_original_aspect_ratio=increase")
        filters.append(f"crop={width}:{height}")
    filters.append("setsar=1")
    filters.append("format=yuv420p")

    work_dir = target.parent
    if subtitle_path:
        # Copy next to the output and reference it relatively: the most reliable
        # way to survive spaces, unicode and drive letters in a filter argument.
        sub_source = Path(subtitle_path)
        sub_name = f"{target.stem}.{sub_source.suffix.lstrip('.')}"
        sub_local = work_dir / sub_name
        if sub_source.resolve() != sub_local.resolve():
            shutil.copyfile(sub_source, sub_local)
        if sub_source.suffix.lower() == ".ass":
            filters.append(f"ass={sub_name}")
        else:
            filters.append(f"subtitles={sub_name}")

    # --- audio chain -------------------------------------------------------
    audio_map: str | None = None
    filter_graph = ",".join(filters)
    complex_parts: list[str] = []
    if dub_index is not None:
        # The bed is an explicit stem when one is supplied, otherwise the
        # clip's own soundtrack (input 0): the dub is mixed on top of it.
        bed = bg_index if bg_index is not None else (0 if keep_original_audio and info.has_audio else None)
        if bed is None:
            complex_parts.append(f"[{dub_index}:a]aresample=48000[aout]")
        else:
            gain = min(max(background_gain, 0.0), 1.0)
            complex_parts.append(
                f"[{bed}:a]aresample=48000,volume={gain:.3f}[bg];"
                f"[bg][{dub_index}:a]sidechaincompress=threshold=0.03:ratio=9:attack=15:release=280[duck];"
                f"[duck][{dub_index}:a]amix=inputs=2:normalize=0:dropout_transition=0,"
                f"alimiter=limit=0.97[aout]"
            )
        audio_map = "[aout]"
    elif keep_original_audio:
        audio_map = "0:a?"

    args: list[str] = [*_base(quiet=True), *inputs]
    if complex_parts:
        # Video filters and audio graph must live in one filter_complex when both
        # are needed, otherwise ffmpeg maps the raw streams.
        complex_parts.insert(0, f"[0:v]{filter_graph}[vout]")
        args.extend(["-filter_complex", ";".join(complex_parts)])
        args.extend(["-map", "[vout]"])
    else:
        args.extend(["-vf", filter_graph, "-map", "0:v:0"])

    if audio_map:
        args.extend(["-map", audio_map])
    elif not keep_original_audio:
        args.append("-an")

    args.extend(
        [
            "-t",
            f"{duration:.3f}",
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-preset",
            preset,
            "-pix_fmt",
            "yuv420p",
            *(["-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if audio_map else []),
            "-movflags",
            "+faststart",
            str(target),
        ]
    )

    _run(args, progress=progress, duration=duration, cwd=work_dir)
    return target


def generate_thumbnail(video_path: str | Path, output_path: str | Path, *, at: float = 1.0) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            *_base(),
            "-ss",
            f"{max(at, 0):.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-vf",
            "scale=480:-2",
            str(target),
        ]
    )
    return target


__all__ = [
    "AudioInfo",
    "MediaError",
    "SyncReport",
    "VideoInfo",
    "atempo_chain",
    "build_dub_track",
    "escape_filter_path",
    "extract_audio",
    "ffmpeg_path",
    "ffprobe_path",
    "generate_thumbnail",
    "mix_background",
    "probe_audio",
    "probe_duration",
    "probe_video",
    "render_final",
    "run_ffmpeg",
    "separate_vocals",
    "stretch_to_fit",
]
