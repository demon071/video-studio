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


class MediaTrimRequest(BaseModel):
    video_path: str
    output_path: str
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    reencode: bool = False


class MediaConcatRequest(BaseModel):
    video_paths: list[str]
    output_path: str
    reencode: bool = False


class MediaDemuxRequest(BaseModel):
    video_path: str
    audio_output: str | None = None
    video_only_output: str | None = None


class MediaMuxRequest(BaseModel):
    video_path: str
    audio_path: str
    output_path: str
    replace_audio: bool = True
    audio_gain: float = Field(default=1.0, ge=0.0, le=3.0)


__all__ = [
    "MediaConcatRequest",
    "MediaDemuxRequest",
    "MediaMuxRequest",
    "MediaTrimRequest",
    "ProbeRequest",
    "ProbeResult",
    "ThumbnailRequest",
    "ThumbnailResult",
]
