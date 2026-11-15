"""Media inspection and stills for the editor's source bin."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.routes._common import run_job, with_suffix
from app.schemas.media import ProbeRequest, ProbeResult, ThumbnailRequest, ThumbnailResult
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


__all__ = ["router"]
