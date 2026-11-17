"""Project file contract: everything needed to reopen an editing session.

The JSON written by :mod:`app.services.project_service` is a plain document so
a project survives engine upgrades: unknown keys are ignored on load and the
``version`` field gates future migrations.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.subtitle import DubbingResult, SubtitleSegment, SubtitleStyle

PROJECT_FORMAT = "shortvideoai-project"
PROJECT_VERSION = 1


class ProjectSource(BaseModel):
    """The clip being edited, as probed when it was imported."""

    path: str = Field(description="Absolute path; empty when the clip came from a browser file input")
    name: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    aspect: str = "9:16"


class ProjectSettings(BaseModel):
    """Provider and voice choices that belong to the project, not the machine."""

    stt_provider: str = "local"
    whisper_size: str = "medium"
    source_language: str = "auto"
    target_language: str = "vi"
    translate_provider: str = "local"
    translate_style: str = ""
    use_translation_for_dub: bool = True
    tts_provider: str = "capcut"
    tts_voice: str = ""
    tts_rate: str = "+0%"
    keep_original_audio: bool = True
    duck_gain: float = Field(default=0.35, ge=0.0, le=1.0)


class ProjectView(BaseModel):
    """Where the user left the editor."""

    playhead: float = 0.0
    preview_subtitles: bool = True
    preview_dub: bool = True
    table_collapsed: bool = False
    timeline_collapsed: bool = False


class ProjectFile(BaseModel):
    format: Literal["shortvideoai-project"] = PROJECT_FORMAT
    version: int = PROJECT_VERSION
    name: str = ""
    saved_at: str | None = Field(default=None, description="ISO timestamp, stamped by the engine")
    origin: str | None = Field(
        default=None,
        description="The .svaproj this document was last written to (stamped by the engine)",
    )
    source: ProjectSource | None = None
    aspect: str = "9:16"
    segments: list[SubtitleSegment] = Field(default_factory=list)
    dub: DubbingResult | None = None
    subtitle_style: SubtitleStyle = Field(
        default_factory=SubtitleStyle,
        description="Caption look, so a reopened project burns in the same subtitles",
    )
    settings: ProjectSettings = Field(default_factory=ProjectSettings)
    view: ProjectView = Field(default_factory=ProjectView)


class ProjectSaveRequest(BaseModel):
    project: ProjectFile
    path: str | None = Field(
        default=None,
        description="Target .svaproj file; empty writes to <data>/projects/<name>.svaproj",
    )


class ProjectOpenRequest(BaseModel):
    path: str


class ProjectEnvelope(BaseModel):
    """Reply for save / open / session: the file on disk plus what is missing."""

    path: str | None = None
    project: ProjectFile | None = None
    missing: list[str] = Field(
        default_factory=list,
        description="Assets the project points at that no longer exist: 'video', 'dub'",
    )


class ProjectDeleteResult(BaseModel):
    cleared: bool = True


class ProjectSummary(BaseModel):
    """One row of the "recent projects" list (cheap: no segments are returned)."""

    path: str
    name: str
    saved_at: str | None = None
    modified: float = Field(default=0.0, description="mtime in epoch seconds, for sorting")
    size_bytes: int = 0
    damaged: bool = Field(default=False, description="The file exists but could not be parsed")
    cached: bool = Field(default=False, description="True for unsaved autosave/cache snapshots")


class ProjectListResult(BaseModel):
    projects: list[ProjectSummary] = Field(default_factory=list)
    cached_projects: list[ProjectSummary] = Field(default_factory=list)
    folder: str = ""


__all__ = [
    "PROJECT_FORMAT",
    "PROJECT_VERSION",
    "ProjectDeleteResult",
    "ProjectEnvelope",
    "ProjectFile",
    "ProjectListResult",
    "ProjectOpenRequest",
    "ProjectSaveRequest",
    "ProjectSettings",
    "ProjectSource",
    "ProjectSummary",
    "ProjectView",
]
