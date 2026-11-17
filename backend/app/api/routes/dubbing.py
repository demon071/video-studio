"""Dubbing: TTS per line, atempo sync, track assembly."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.routes._common import run_job, work_dir
from app.schemas.subtitle import (
    DubbingCueRequest,
    DubbingCueResult,
    DubbingRemixRequest,
    DubbingRemixResult,
    DubbingRequest,
    DubbingResult,
)
from app.services import capcut_client, elevenlabs_tts, piper_tts, tts_service, vieneu_tts

router = APIRouter(tags=["dubbing"])


@router.get("/api/dubbing/voices")
def list_voices(provider: str = "capcut", search: str = "", category: str = "") -> dict:
    """Return available TTS voices for CapCut, ElevenLabs, Piper, or VieNeu."""
    if provider == "elevenlabs":
        try:
            voices = elevenlabs_tts.list_voices()
            quota = elevenlabs_tts.get_quota()
            return {"provider": "elevenlabs", "voices": voices, "quota": quota}
        except Exception as exc:
            return {"provider": "elevenlabs", "voices": [], "error": str(exc)}
    if provider == "piper":
        return {"provider": "piper", "voices": piper_tts.list_voices()}
    if provider == "vieneu":
        return {"provider": "vieneu", "voices": vieneu_tts.list_voices()}
    voices = capcut_client.list_capcut_voices(search=search or None, category=category or None)
    return {"provider": "capcut", "voices": voices}


@router.post("/api/dubbing/synthesize", response_model=DubbingResult)
def synthesize(request: DubbingRequest) -> DubbingResult:
    """Speak every subtitle line and fit it exactly into its time slot."""
    folder = work_dir("dub")

    def work(report) -> dict:
        outcome = tts_service.dub(
            request.segments,
            work_dir=folder,
            provider=request.provider,
            voice=request.voice,
            rate=request.rate,
            use_translation=request.use_translation,
            timing_rewrite=request.timing_rewrite,
            trim_silence=request.trim_silence,
            language=request.language,
            video_path=request.video_path,
            speaker_voices=request.speaker_voices,
            report=report,
        )
        return DubbingResult(
            track_path=str(outcome.track_path),
            duration=outcome.duration,
            provider=outcome.provider,
            voice=outcome.voice,
            segments=outcome.reports,
            skipped=outcome.skipped,
            rewritten=outcome.rewritten,
        ).model_dump(mode="json")

    return DubbingResult(**run_job("dubbing", work))


@router.post("/api/dubbing/cue", response_model=DubbingCueResult)
def synthesize_cue(request: DubbingCueRequest) -> DubbingCueResult:
    """Re-speak one line: the subtitle was edited and its take is stale.

    The whole track comes in because a line may borrow the silence that follows
    it; only this one line is written, and the caller decides whether the take
    replaces the old one (``audition``) or is just handed back to be played.
    """
    if not any(item.index == request.index for item in request.segments):
        raise HTTPException(status_code=404, detail=f"Line {request.index} is not on the timeline")

    folder = work_dir("dub-cue")

    def work(report) -> dict:
        outcome = tts_service.cue(
            request.segments,
            request.index,
            work_dir=folder,
            provider=request.provider,
            voice=request.voice,
            rate=request.rate,
            use_translation=request.use_translation,
            timing_rewrite=request.timing_rewrite,
            audition=request.audition,
            language=request.language,
            report=report,
        )
        return DubbingCueResult(
            index=request.index,
            report=outcome.report,
            provider=outcome.provider,
            voice=outcome.voice,
            audition=request.audition,
        ).model_dump(mode="json")

    return DubbingCueResult(**run_job("dubbing", work))


@router.post("/api/dubbing/remix", response_model=DubbingRemixResult)
def remix(request: DubbingRemixRequest) -> DubbingRemixResult:
    """Re-assemble the track after one or more lines were re-spoken."""
    folder = work_dir("dub-remix")

    def work(report) -> dict:
        track = tts_service.remix(
            request.segments,
            output_path=folder / "dub_track.wav",
            duration=request.duration,
            report=report,
        )
        return DubbingRemixResult(
            track_path=str(track),
            duration=round(request.duration, 3),
            lines=len(request.segments),
        ).model_dump(mode="json")

    return DubbingRemixResult(**run_job("dubbing", work))


@router.post("/api/dubbing/preview")
def preview(text: str, provider: str | None = None, voice: str | None = None, rate: str | None = None) -> dict:
    """One-shot line for the voice picker (no timeline maths)."""
    if len(text.strip()) > 400:
        raise HTTPException(status_code=400, detail="Preview text is limited to 400 characters")
    try:
        return tts_service.preview(text, provider=provider, voice=voice, rate=rate)
    except tts_service.TtsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
