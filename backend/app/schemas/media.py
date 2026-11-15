"""Request/response models for media probing and thumbnails."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProbeRequest(BaseModel):
    video_path: str = Field(description="Absolute Windows path of the source clip")


class ProbeResult(BaseModel):
    video_path: str
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    aspect: str


class ThumbnailRequest(BaseModel):
    video_path: str
    output_path: str | None = None
    at: float = Field(default=1.0, ge=0.0, description="Timestamp of the frame to grab")


class ThumbnailResult(BaseModel):
    video_path: str
    output_path: str


__all__ = ["ProbeRequest", "ProbeResult", "ThumbnailRequest", "ThumbnailResult"]
