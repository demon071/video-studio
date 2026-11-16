"""Media inspection and stills for the editor's source bin."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.routes._common import run_job, with_suffix
from app.schemas.media import (
    MediaConcatRequest,
    MediaDemuxRequest,
    MediaMuxRequest,
    MediaTrimRequest,
    ProbeRequest,
    ProbeResult,
    ThumbnailRequest,
    ThumbnailResult,
)
from app.services import media_processor

router = APIRouter(tags=["media"])


@router.post("/api/media/probe", response_model=ProbeResult)
def probe(request: ProbeRequest) -> ProbeResult:
    source = Path(request.video_path)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {source}")
    try:
        info = media_processor.probe_video(source)
    except media_processor.MediaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    ratio = info.width / info.height if info.height else 0.0
    if ratio > 1.5:
        aspect = "16:9"
    elif ratio < 0.8:
        aspect = "9:16"
    else:
        aspect = "1:1"

    return ProbeResult(
        video_path=str(source),
        duration=round(info.duration, 3),
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        aspect=aspect,
    )


@router.post("/api/media/thumbnail", response_model=ThumbnailResult)
def thumbnail(request: ThumbnailRequest) -> ThumbnailResult:
    source = Path(request.video_path)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {source}")

    output = Path(request.output_path) if request.output_path else source.with_name(f"{source.stem}.thumb.jpg")

    def work(report) -> dict:
        report(stage="thumbnail", fraction=0.0, message="grabbing still")
        try:
            produced = media_processor.generate_thumbnail(source, with_suffix(output, ".jpg"), at=request.at)
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="thumbnail ready")
        return ThumbnailResult(video_path=str(source), output_path=str(produced)).model_dump(mode="json")

    return ThumbnailResult(**run_job("thumbnail", work))


@router.post("/api/media/tools/trim")
def trim_tool(request: MediaTrimRequest) -> dict:
    """Cut a segment [start, end] from a video file."""
    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Cắt video...")
        try:
            produced = media_processor.trim_media(
                request.video_path,
                request.output_path,
                start=request.start,
                end=request.end,
                reencode=request.reencode,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã cắt xong video")
        return {"output_path": str(produced)}

    return run_job("media_tools", work)


@router.post("/api/media/tools/concat")
def concat_tool(request: MediaConcatRequest) -> dict:
    """Concatenate multiple video files in order."""
    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Ghép video...")
        try:
            produced = media_processor.concat_media(
                request.video_paths,
                request.output_path,
                reencode=request.reencode,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã ghép xong video")
        return {"output_path": str(produced)}

    return run_job("media_tools", work)


@router.post("/api/media/tools/demux")
def demux_tool(request: MediaDemuxRequest) -> dict:
    """Extract audio stream and/or mute video stream."""
    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Tách âm thanh / video...")
        try:
            outputs = media_processor.extract_streams(
                request.video_path,
                audio_output=request.audio_output,
                video_only_output=request.video_only_output,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã tách xong")
        return outputs

    return run_job("media_tools", work)


@router.post("/api/media/tools/mux")
def mux_tool(request: MediaMuxRequest) -> dict:
    """Mux audio into video (replace or mix)."""
    def work(report) -> dict:
        report(stage="media_tools", fraction=0.1, message="Ghép âm thanh vào video...")
        try:
            produced = media_processor.mux_audio_video(
                request.video_path,
                request.audio_path,
                request.output_path,
                replace_audio=request.replace_audio,
                audio_gain=request.audio_gain,
            )
        except media_processor.MediaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report(fraction=1.0, message="Đã ghép xong")
        return {"output_path": str(produced)}

    return run_job("media_tools", work)


__all__ = ["router"]
