"""REST endpoints for the Performance Queue (Hàng đợi xử lý hàng loạt)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.services.queue_service import QueueMode, queue_manager

router = APIRouter(tags=["queue"])


class QueueAddRequest(BaseModel):
    video_paths: list[str] = Field(min_length=1)
    source_mode: Literal["audio", "ocr", "auto"] = "audio"
    stt_provider: Literal["local", "sensevoice", "ocr", "capcut"] | None = None
    target_language: str = "vi"
    translate_provider: str = "google"
    translate_preset: str = "general"
    tts_provider: str = "capcut"
    tts_voice: str | None = None
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    bgm_mode: Literal["keep", "strip", "mute", "custom"] = "keep"
    bgm_path: str | None = None
    subtitle_style: dict[str, Any] | None = None


class QueueStartRequest(BaseModel):
    mode: QueueMode = "process_and_export"
    item_ids: list[str] | None = None
    subtitle_style: dict[str, Any] | None = None


class QueueStopRequest(BaseModel):
    immediate: bool = False


@router.get("/api/queue")
def get_queue() -> dict:
    return queue_manager.snapshot()


@router.post("/api/queue/add")
def add_to_queue(request: QueueAddRequest) -> dict:
    return queue_manager.add_items(
        request.video_paths,
        source_mode=request.source_mode,
        stt_provider=request.stt_provider,
        target_language=request.target_language,
        translate_provider=request.translate_provider,
        translate_preset=request.translate_preset,
        tts_provider=request.tts_provider,
        tts_voice=request.tts_voice,
        aspect=request.aspect,
        bgm_mode=request.bgm_mode,
        bgm_path=request.bgm_path,
        subtitle_style=request.subtitle_style,
    )


@router.post("/api/queue/start")
def start_queue(request: QueueStartRequest) -> dict:
    return queue_manager.start_batch(
        mode=request.mode,
        item_ids=request.item_ids,
        subtitle_style=request.subtitle_style,
    )


@router.post("/api/queue/stop")
def stop_queue(request: QueueStopRequest) -> dict:
    return queue_manager.request_stop(immediate=request.immediate)


@router.get("/api/queue/{item_id}/preview")
def preview_queue_item(item_id: str) -> dict:
    try:
        return queue_manager.get_preview(item_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/queue/{item_id}/save")
def save_queue_item(item_id: str, payload: dict[str, Any] = Body(default_factory=dict)) -> dict:
    try:
        return queue_manager.save_item_state(item_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/queue/{item_id}")
def remove_from_queue(item_id: str) -> dict:
    return queue_manager.remove_item(item_id)


@router.post("/api/queue/{item_id}/clean")
def clean_item_data(item_id: str) -> dict:
    return queue_manager.clean_video_data(item_id)


@router.post("/api/queue/clear-completed")
def clear_completed() -> dict:
    return queue_manager.clear_completed()


__all__ = ["router"]
