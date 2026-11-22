"""Final render: framing, ASS burn-in, dub over the clip's own soundtrack."""

from __future__ import annotations

from pathlib import Path
import csv
import shutil
import time
import zipfile

from fastapi import APIRouter, HTTPException

from typing import Literal
from pydantic import BaseModel, Field

from app.api.routes._common import run_job, with_suffix, work_dir
from app.config import get_settings
from app.schemas.subtitle import (
    DubbingSegmentReport,
    RenderRequest,
    RenderResult,
    SubtitleSegment,
    SubtitleStyle,
)
from app.services import media_processor
from app.utils import subtitles as subtitle_utils

router = APIRouter(tags=["render"])


class AudioExportRequest(BaseModel):
    output_path: str
    mode: Literal["voice", "bgm", "mix"] = "mix"
    dub_track_path: str | None = None
    background_path: str | None = None
    video_path: str | None = None
    background_gain: float = Field(default=0.35, ge=0.0, le=2.0)
    voice_gain: float = Field(default=1.0, ge=0.0, le=3.0)


class ResourcePackageRequest(BaseModel):
    """Export a complete CapCap resource bundle (.zip or folder): SRT, ASS, TTS WAV clips + index.csv, audio stems."""

    output_path: str = Field(description="Target .zip file or directory path")
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    segments: list[SubtitleSegment] = Field(default_factory=list)
    secondary_segments: list[SubtitleSegment] = Field(default_factory=list)
    presentation_mode: Literal["none", "single", "bilingual"] = "single"
    style: SubtitleStyle | None = None
    secondary_style: SubtitleStyle | None = None
    dub_track_path: str | None = None
    dub_segments: list[DubbingSegmentReport] = Field(default_factory=list)
    background_path: str | None = None


@router.post("/api/render", response_model=RenderResult)
def render(request: RenderRequest) -> RenderResult:
    settings = get_settings()
    output = with_suffix(request.output_path, ".mp4")

    def work(report) -> dict:
        started = time.monotonic()
        report(stage="render", fraction=0.0, message=f"encoding {request.aspect}")
        try:
            produced = media_processor.render_final(
                request.video_path,
                output,
                aspect=request.aspect,
                subtitle_path=request.subtitle_path,
                dub_track_path=request.dub_track_path,
                background_path=request.background_path,
                background_gain=request.background_gain,
                voice_gain=request.voice_gain,
                keep_original_audio=request.keep_original_audio,
                crf=request.crf or settings.render_crf,
                preset=request.preset or settings.render_preset,
                quality_profile=request.quality_profile,
                use_nvenc=request.use_nvenc,
                flip_horizontal=request.flip_horizontal,
                scale_mode=request.scale_mode,
                brightness=request.brightness,
                contrast=request.contrast,
                saturation=request.saturation,
                warps=request.warps,
                layers=request.layers,
                video_clips=request.video_clips,
                start_time=request.start_time,
                sample_duration=request.sample_duration,
                progress=lambda value: report(fraction=value, message="encoding"),
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        info = media_processor.probe_video(produced)
        return RenderResult(
            output_path=str(produced),
            duration=round(info.duration, 3),
            size_bytes=produced.stat().st_size,
            width=info.width,
            height=info.height,
            elapsed_s=round(time.monotonic() - started, 2),
        ).model_dump(mode="json")

    return RenderResult(**run_job("render", work))


@router.post("/api/render/fast-preview", response_model=RenderResult)
def fast_preview(request: RenderRequest) -> RenderResult:
    """Render a quick 5-second sample preview around ``start_time`` (CapCap Fast Preview)."""
    folder = work_dir("fast-preview")
    output = folder / "fast_preview_5s.mp4"
    sample_req = request.model_copy(
        update={
            "output_path": str(output),
            "sample_duration": request.sample_duration or 5.0,
            "quality_profile": request.quality_profile or "low",
        }
    )
    return render(sample_req)


@router.post("/api/render/export-audio")
def export_audio(request: AudioExportRequest) -> dict:
    """Export standalone audio: Voice only, BGM only, or Mixed audio."""
    def work(report) -> dict:
        report(stage="render", fraction=0.1, message=f"exporting audio ({request.mode})")
        try:
            produced = media_processor.export_audio_only(
                request.output_path,
                mode=request.mode,
                dub_track_path=request.dub_track_path,
                background_path=request.background_path,
                video_path=request.video_path,
                background_gain=request.background_gain,
                voice_gain=request.voice_gain,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="audio exported")
        return {
            "output_path": str(produced),
            "mode": request.mode,
            "size_bytes": produced.stat().st_size,
        }

    return run_job("render", work)


@router.post("/api/render/export-package")
def export_resource_package(request: ResourcePackageRequest) -> dict:
    """Export a CapCap Resource Package (.zip or directory) with SRT, ASS, per-cue TTS WAVs + index.csv, and dub track."""

    def work(report) -> dict:
        report(stage="package", fraction=0.1, message="building resource package")
        target = Path(request.output_path)
        is_zip = target.suffix.lower() == ".zip"
        stage_dir = work_dir("resource-pack") / target.stem if is_zip else target
        stage_dir.mkdir(parents=True, exist_ok=True)
        files_written: list[str] = []

        if request.segments:
            orig_srt = stage_dir / "subtitles_original.srt"
            subtitle_utils.write_subtitles(request.segments, orig_srt, fmt="srt", use_translation=False)
            files_written.append(orig_srt.name)

            if any((s.translation or "").strip() for s in request.segments):
                vi_srt = stage_dir / "subtitles_translated.srt"
                subtitle_utils.write_subtitles(request.segments, vi_srt, fmt="srt", use_translation=True)
                files_written.append(vi_srt.name)

            width, height = media_processor.TARGET_ASPECTS.get(request.aspect, (1080, 1920))
            ass_file = stage_dir / "subtitles_styled.ass"
            subtitle_utils.write_subtitles(
                request.segments,
                ass_file,
                fmt="ass",
                width=width,
                height=height,
                use_translation=any((s.translation or "").strip() for s in request.segments),
                style=request.style,
                presentation_mode=request.presentation_mode,
                secondary_segments=request.secondary_segments,
                secondary_style=request.secondary_style,
            )
            files_written.append(ass_file.name)

        if request.dub_track_path and Path(request.dub_track_path).is_file():
            dub_dst = stage_dir / "voice_track.wav"
            shutil.copyfile(request.dub_track_path, dub_dst)
            files_written.append(dub_dst.name)

        if request.background_path and Path(request.background_path).is_file():
            bg_src = Path(request.background_path)
            bg_dst = stage_dir / f"background_audio{bg_src.suffix or '.wav'}"
            shutil.copyfile(bg_src, bg_dst)
            files_written.append(bg_dst.name)

        tts_clips_copied = 0
        if request.dub_segments:
            tts_dir = stage_dir / "tts_segments"
            tts_dir.mkdir(parents=True, exist_ok=True)
            csv_path = tts_dir / "index.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["index", "start_s", "end_s", "duration_s", "ratio", "filename", "text"])
                for seg_rep in request.dub_segments:
                    if seg_rep.clip_path and Path(seg_rep.clip_path).is_file():
                        clip_name = f"line_{seg_rep.index:03d}.wav"
                        shutil.copyfile(seg_rep.clip_path, tts_dir / clip_name)
                        tts_clips_copied += 1
                        writer.writerow([
                            seg_rep.index,
                            f"{seg_rep.start:.3f}",
                            f"{seg_rep.end:.3f}",
                            f"{seg_rep.target_duration:.3f}",
                            f"{seg_rep.applied_ratio:.2f}",
                            clip_name,
                            seg_rep.spoken or seg_rep.source or "",
                        ])
            files_written.append("tts_segments/index.csv")

        if is_zip:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for p in stage_dir.rglob("*"):
                    if p.is_file():
                        zf.write(p, p.relative_to(stage_dir))
            size_bytes = target.stat().st_size
        else:
            size_bytes = sum(p.stat().st_size for p in stage_dir.rglob("*") if p.is_file())

        report(fraction=1.0, message="resource package exported")
        return {
            "output_path": str(target),
            "files": files_written,
            "tts_clips": tts_clips_copied,
            "size_bytes": size_bytes,
        }

    return run_job("render", work)


__all__ = ["router"]
