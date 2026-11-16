"""REST endpoints for the Performance Queue (Hàng đợi xử lý hàng loạt)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.queue_service import QueueMode, queue_manager

router = APIRouter(tags=["queue"])


class QueueAddRequest(BaseModel):
    video_paths: list[str] = Field(min_length=1)
    source_mode: Literal["audio", "ocr", "auto"] = "audio"
    target_language: str = "Vietnamese"
    translate_provider: str = "google"
    translate_preset: str = "general"
    tts_provider: str = "capcut"
    tts_voice: str | None = None
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"


class QueueStartRequest(BaseModel):
    mode: QueueMode = "process_and_export"
    item_ids: list[str] | None = None


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
        target_language=request.target_language,
        translate_provider=request.translate_provider,
        translate_preset=request.translate_preset,
        tts_provider=request.tts_provider,
        tts_voice=request.tts_voice,
        aspect=request.aspect,
    )


@router.post("/api/queue/start")
def start_queue(request: QueueStartRequest) -> dict:
    return queue_manager.start_batch(mode=request.mode, item_ids=request.item_ids)


@router.post("/api/queue/stop")
def stop_queue(request: QueueStopRequest) -> dict:
    return queue_manager.request_stop(immediate=request.immediate)


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
