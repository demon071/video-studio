"""Final render: framing, ASS burn-in, dub over the clip's own soundtrack."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from app.api.routes._common import run_job, with_suffix
from app.config import get_settings
from app.schemas.subtitle import RenderRequest, RenderResult
from app.services import media_processor

router = APIRouter(tags=["render"])


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
                keep_original_audio=request.keep_original_audio,
                crf=request.crf or settings.render_crf,
                preset=request.preset or settings.render_preset,
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


__all__ = ["router"]
