"""CapCut hand-over: turn the open project into an editable desktop draft."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.routes._common import run_job
from app.schemas.capcut import CapCutExportRequest, CapCutExportResult, CapCutTargets
from app.schemas.subtitle import SubtitleStyle
from app.services import capcut_draft

router = APIRouter(tags=["export"])


@router.get("/api/export/capcut/targets", response_model=CapCutTargets)
def capcut_targets() -> CapCutTargets:
    """Where a draft would land, and whether CapCut is installed right now."""
    return CapCutTargets(**vars(capcut_draft.targets()))


@router.post("/api/export/capcut", response_model=CapCutExportResult)
def export_capcut(request: CapCutExportRequest) -> CapCutExportResult:
    source = Path(request.video_path)
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"Không tìm thấy video nguồn: {source}")

    def work(report) -> dict:
        spec = capcut_draft.DraftSpec(
            video_path=str(source),
            aspect=request.aspect,
            segments=request.segments,
            style=request.style or SubtitleStyle(),
            use_translation=request.use_translation,
            presentation_mode=request.presentation_mode,
            secondary_segments=request.secondary_segments,
            secondary_style=request.secondary_style,
            dub_track_path=request.dub_track_path,
            dub_segments=request.dub_segments,
            keep_original_audio=request.keep_original_audio,
            background_gain=request.background_gain,
            copy_video=request.copy_video,
        )
        result = capcut_draft.export(
            spec,
            root=Path(request.output_dir) if request.output_dir else None,
            name=request.draft_name,
            register=request.add_to_index,
            progress=lambda message, fraction: report(stage="capcut", fraction=fraction, message=message),
        )
        return vars(result)

    return CapCutExportResult(**run_job("capcut", work))


__all__ = ["router"]
