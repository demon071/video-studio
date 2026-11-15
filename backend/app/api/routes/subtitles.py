"""Subtitle creation: speech-to-text, translation and file export."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.routes._common import run_job, with_suffix, work_dir
from app.config import get_settings
from app.schemas.subtitle import (
    OcrFrameRequest,
    OcrFrameResult,
    OcrFrameSample,
    SubtitleExportRequest,
    SubtitleExportResult,
    TranscribeRequest,
    TranscribeResult,
    TranslateRequest,
    TranslateResult,
)
from app.services import media_processor, ocr_service, provider_config, stt_service, translate_service
from app.utils.subtitles import write_subtitles

router = APIRouter(tags=["subtitles"])


@router.post("/api/subtitles/transcribe", response_model=TranscribeResult)
def transcribe(request: TranscribeRequest) -> TranscribeResult:
    """Recognise speech and stream every line to the UI as it is decoded."""

    def work(report) -> dict:
        return stt_service.transcribe(request, report=report).model_dump(mode="json")

    return TranscribeResult(**run_job("transcribe", work))


@router.post("/api/subtitles/ocr-frame", response_model=OcrFrameResult)
def ocr_frame(request: OcrFrameRequest) -> OcrFrameResult:
    """Read one cue's frames again, so a line the engine got wrong can be fixed.

    Nothing is stored: the answer is handed back and the editor asks the user
    whether it should replace the text at that cue.
    """
    if not Path(request.video_path).is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {request.video_path}")

    def work(report) -> dict:
        settings = provider_config.overlay(get_settings())
        # The hardsubs are drawn in the clip's language, so an explicit language
        # also picks the recogniser - exactly as the full extraction pass does.
        language = (request.language or "").strip().lower()
        if language and language != "auto":
            settings = settings.model_copy(update={"ocr_language": language})
        reading = ocr_service.read_cue(
            request.video_path,
            start=request.start,
            end=request.end,
            work_dir=work_dir("ocr-frame"),
            samples=request.samples,
            report=report,
            settings=settings,
        )
        return OcrFrameResult(
            text=reading.text,
            candidates=reading.candidates,
            frames=[OcrFrameSample(time=when, text=text) for when, text in reading.frames],
            engine=reading.engine,
            device=reading.device,
            start=reading.start,
            end=reading.end,
            frame_path=str(reading.frame_path) if reading.frame_path else None,
            region=list(reading.region),
        ).model_dump(mode="json")

    return OcrFrameResult(**run_job("ocr-frame", work))


@router.post("/api/subtitles/translate", response_model=TranslateResult)
def translate(request: TranslateRequest) -> TranslateResult:
    """Translate line by line, keeping the original timestamps intact."""

    def work(report) -> dict:
        outcome = translate_service.translate(
            request.segments,
            target_language=request.target_language,
            source_language=request.source_language,
            provider=request.provider,
            style=request.style,
            report=report,
        )
        return TranslateResult(
            target_language=request.target_language,
            provider=outcome.provider,
            model=outcome.model,
            segments=outcome.segments,
        ).model_dump(mode="json")

    return TranslateResult(**run_job("translate", work))


@router.post("/api/subtitles/export", response_model=SubtitleExportResult)
def export_subtitles(request: SubtitleExportRequest) -> SubtitleExportResult:
    """Write a .srt/.ass file sized for the requested frame."""
    settings = get_settings()
    width, height = media_processor.TARGET_ASPECTS.get(request.aspect, (1080, 1920))
    target = with_suffix(request.output_path, f".{request.fmt}")

    try:
        path = write_subtitles(
            request.segments,
            target,
            fmt=request.fmt,
            width=width,
            height=height,
            font=request.font or settings.subtitle_font,
            use_translation=request.use_translation,
            style=request.style,
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot write {target}: {exc}") from exc

    return SubtitleExportResult(
        output_path=str(Path(path)),
        fmt=request.fmt,
        lines=len(request.segments),
    )


__all__ = ["router"]
