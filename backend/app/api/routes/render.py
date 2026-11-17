"""Final render: framing, ASS burn-in, dub over the clip's own soundtrack."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from typing import Literal
from pydantic import BaseModel, Field

from app.api.routes._common import run_job, with_suffix, work_dir
from app.config import get_settings
from app.schemas.subtitle import RenderRequest, RenderResult
from app.services import media_processor

router = APIRouter(tags=["render"])


class AudioExportRequest(BaseModel):
    output_path: str
    mode: Literal["voice", "bgm", "mix"] = "mix"
    dub_track_path: str | None = None
    background_path: str | None = None
    video_path: str | None = None
    background_gain: float = Field(default=0.35, ge=0.0, le=2.0)
    voice_gain: float = Field(default=1.0, ge=0.0, le=3.0)


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


__all__ = ["router"]
