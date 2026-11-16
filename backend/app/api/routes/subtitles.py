"""Subtitle creation: speech-to-text, translation and file export."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.api.routes._common import run_job, with_suffix, work_dir
from app.config import get_settings
from app.schemas.subtitle import (
    ContextAnalyzeRequest,
    ContextAnalyzeResult,
    OcrFrameRequest,
    OcrFrameResult,
    OcrFrameSample,
    OcrRegionRequest,
    OcrRegionResult,
    SrtImportRequest,
    SubtitleExportRequest,
    SubtitleExportResult,
    SubtitleSegment,
    TranscribeRequest,
    TranscribeResult,
    TranslateRequest,
    TranslateResult,
)
from app.services import media_processor, ocr_service, provider_config, stt_service, translate_service
from app.utils.subtitles import parse_srt, write_subtitles

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


@router.get("/api/subtitles/presets")
def get_translation_presets() -> dict:
    """Return CapCap's genre translation presets."""
    return {"presets": translate_service.list_presets()}


@router.post("/api/subtitles/analyze-context", response_model=ContextAnalyzeResult)
def analyze_context(request: ContextAnalyzeRequest) -> ContextAnalyzeResult:
    """Analyze dialogue cues to extract character profiles and two-way pronoun rules."""

    def work(_report) -> dict:
        context, provider, model, count = translate_service.analyze_dialogue_context(
            request.segments,
            source_language=request.source_language,
            target_language=request.target_language,
            provider=request.provider,
            user_guidance=request.user_guidance,
            existing_context=request.existing_context,
        )
        return ContextAnalyzeResult(
            context=context,
            provider=provider,
            model=model,
            sampled_cues=count,
        ).model_dump(mode="json")

    return ContextAnalyzeResult(**run_job("analyze-context", work))


@router.post("/api/subtitles/import-srt", response_model= list[SubtitleSegment])
def import_srt(request: SrtImportRequest) -> list[SubtitleSegment]:
    """Import an external .srt file or raw SRT content into SubtitleSegment items."""
    content = request.content or ""
    if request.srt_path:
        path = Path(request.srt_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"SRT file not found: {request.srt_path}")
        try:
            content = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="replace")
    segments = parse_srt(content)
    if not segments:
        raise HTTPException(status_code=400, detail="No valid subtitle cues found in SRT")
    return segments


@router.post("/api/subtitles/ocr-region", response_model=OcrRegionResult)
def ocr_region(request: OcrRegionRequest) -> OcrRegionResult:
    """Standalone OCR Translator: read and optionally translate text from any rectangle on a video frame."""
    if not Path(request.video_path).is_file():
        raise HTTPException(status_code=404, detail=f"Video not found: {request.video_path}")
    coords = request.region if len(request.region) == 4 else [0.0, 55.0, 100.0, 100.0]
    rect = (float(coords[0]), float(coords[1]), float(coords[2]), float(coords[3]))

    def work(_report) -> dict:
        text, engine, device = ocr_service.extract_region_text(
            request.video_path,
            when=request.time,
            region=rect,
            work_dir=work_dir("ocr-region"),
            language=request.language,
        )
        translation = ""
        if text and request.translate_to:
            try:
                outcome = translate_service.translate(
                    [SubtitleSegment(index=1, start=0.0, end=2.0, text=text)],
                    target_language=request.translate_to,
                    source_language=request.language,
                    provider=request.translate_provider,
                )
                if outcome.segments:
                    translation = outcome.segments[0].translation or ""
            except Exception:  # noqa: BLE001
                translation = ""
        return OcrRegionResult(
            text=text,
            translation=translation,
            engine=engine,
            device=device,
        ).model_dump(mode="json")

    return OcrRegionResult(**run_job("ocr-region", work))


@router.post("/api/subtitles/translate", response_model=TranslateResult)
def translate(request: TranslateRequest) -> TranslateResult:
    """Translate line by line, keeping the original timestamps intact."""

    def work(report) -> dict:
        kwargs: dict = {
            "target_language": request.target_language,
            "source_language": request.source_language,
            "provider": request.provider,
            "style": request.style,
            "report": report,
        }
        if request.preset is not None:
            kwargs["preset"] = request.preset
        if request.custom_prompt is not None:
            kwargs["custom_prompt"] = request.custom_prompt
        if request.auto_context:
            kwargs["auto_context"] = True
        if request.context_guidance is not None:
            kwargs["context_guidance"] = request.context_guidance
        outcome = translate_service.translate(request.segments, **kwargs)
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
            presentation_mode=request.presentation_mode,
            secondary_segments=request.secondary_segments,
            secondary_style=request.secondary_style,
        )
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Cannot write {target}: {exc}") from exc

    return SubtitleExportResult(
        output_path=str(Path(path)),
        fmt=request.fmt,
        lines=len(request.segments),
    )


__all__ = ["router"]
