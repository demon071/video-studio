"""Contract of the CapCut hand-over.

The draft itself is written by :mod:`app.services.capcut_draft`; these models
only describe what the editor sends and what the panel shows back.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.subtitle import DubbingSegmentReport, SubtitleSegment, SubtitleStyle


class CapCutExportRequest(BaseModel):
    """Everything a `.svaproj` holds, laid out the way CapCut wants it."""

    video_path: str = Field(description="Absolute path of the source clip")
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    segments: list[SubtitleSegment] = Field(default_factory=list, description="Caption cues")
    use_translation: bool = Field(default=True, description="Caption the translation, like the ASS burn")
    style: SubtitleStyle | None = Field(default=None, description="Caption look; None keeps the default")
    dub_track_path: str | None = Field(default=None, description="Assembled dub track, used as a fallback")
    dub_segments: list[DubbingSegmentReport] = Field(
        default_factory=list,
        description="Per-line takes: one editable audio clip per cue instead of one long track",
    )
    keep_original_audio: bool = Field(default=True)
    background_gain: float = Field(default=0.35, ge=0.0, le=1.0)
    copy_video: bool = Field(
        default=False,
        description="Copy the clip into the draft (self-contained, but doubles the disk use)",
    )
    draft_name: str | None = Field(default=None, description="Folder name; None uses the clip name")
    output_dir: str | None = Field(
        default=None,
        description="CapCut drafts folder; None detects the installed CapCut and falls back to the data dir",
    )
    add_to_index: bool = Field(
        default=True,
        description="Append the draft to CapCut's project index so it appears in the start screen",
    )


class CapCutExportResult(BaseModel):
    draft_path: str
    draft_name: str
    width: int
    height: int
    duration: float
    tracks: int
    captions: int
    dub_lines: int
    media_files: int
    registered: bool
    index_backup: str | None = None
    warnings: list[str] = Field(default_factory=list)


class CapCutTargets(BaseModel):
    """What the export button needs to know before it offers itself."""

    drafts_root: str | None = Field(default=None, description="CapCut's own drafts folder, when installed")
    exists: bool
    fallback_root: str = Field(description="Where the draft goes when CapCut is not installed")
    capcut_version: str | None = None
    capcut_running: bool = False
    drafts: int = Field(default=0, description="Projects already listed in CapCut's index")


__all__ = ["CapCutExportRequest", "CapCutExportResult", "CapCutTargets"]
