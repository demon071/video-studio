"""Project files: the ``.svaproj`` document and the rolling session snapshot.

A project is a plain JSON file, so a clip can be reopened later (or on another
machine when the media is reachable) without replaying transcription,
translation and dubbing. Two kinds of state live here:

* :func:`save_project` / :func:`open_project` - the document the user names
  (``anything.svaproj``, default ``<data>/projects/<name>.svaproj``);
* :func:`write_session` / :func:`read_session` - one snapshot of the editor in
  ``<data>/projects/_session.json``, rewritten on every change so that closing
  the window (or a crash) resumes where the user left off.

Dub tracks are assembled under ``<data>/temp/<run>/``, which is disposable, so
saving copies the track into ``<data>/projects/audio/`` first - otherwise the
project would reopen with a dangling voice-over.

Layout::

    %APPDATA%\\ShortVideoAI\\projects\\
      _session.json                    autosave of the editor in progress
      di-vao-bay.svaproj               named project
      audio\\dub-1a2b3c4d.wav          voice-over owned by the project store
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.config import Folders, Settings, get_settings
from app.schemas.project import (
    PROJECT_FORMAT,
    PROJECT_VERSION,
    ProjectEnvelope,
    ProjectFile,
    ProjectListResult,
    ProjectSummary,
)

log = logging.getLogger(__name__)

PROJECT_SUFFIX = ".svaproj"
SESSION_NAME = "_session.json"
AUDIO_FOLDER = "audio"

# Characters Windows rejects in a file name, plus control codes.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


class ProjectError(RuntimeError):
    """A project file could not be written, read or understood."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def projects_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.data_path / Folders.PROJECTS


def audio_dir(settings: Settings | None = None) -> Path:
    """Project-owned media (dub tracks) that survives a temp cleanup."""
    return projects_dir(settings) / AUDIO_FOLDER


def session_path(settings: Settings | None = None) -> Path:
    return projects_dir(settings) / SESSION_NAME


def _slug(name: str) -> str:
    """Turn a project name into a file name Windows accepts."""
    cleaned = _UNSAFE.sub("-", (name or "").strip()).strip(" .-")
    return (cleaned[:80] or "project").rstrip(" .")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _is_under(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except (OSError, ValueError):
        return False
    return True


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def _write_json(target: Path, payload: dict) -> None:
    """Write through a temp file so an interrupted save cannot truncate a project."""
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(f"{target.name}.tmp")
    scratch.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(scratch, target)


def _read_json(target: Path) -> ProjectFile:
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProjectError(f"Cannot read {target.name}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"{target.name} is not a valid project file: {exc}") from exc

    if not isinstance(raw, dict) or raw.get("format") != PROJECT_FORMAT:
        raise ProjectError(f"{target.name} is not a ShortVideoAI project")
    version = int(raw.get("version") or 0)
    if version > PROJECT_VERSION:
        raise ProjectError(
            f"This project was saved by a newer version (v{version}); update the app to open it"
        )

    try:
        return ProjectFile.model_validate(raw)
    except ValidationError as exc:
        raise ProjectError(
            f"{target.name} is damaged: {exc.error_count()} field(s) failed validation"
        ) from exc


def _missing_assets(project: ProjectFile) -> list[str]:
    """Assets the document points at that no longer exist on this machine."""
    missing: list[str] = []
    if project.source and project.source.path and not Path(project.source.path).is_file():
        missing.append("video")
    if project.dub and project.dub.track_path and not Path(project.dub.track_path).is_file():
        missing.append("dub")
    return missing


def _envelope(project: ProjectFile, path: Path | None) -> ProjectEnvelope:
    return ProjectEnvelope(
        path=str(path) if path else None,
        project=project,
        missing=_missing_assets(project),
    )


# ---------------------------------------------------------------------------
# Media owned by the project
# ---------------------------------------------------------------------------
def _persist_dub(project: ProjectFile, settings: Settings) -> None:
    """Copy the dub track out of the disposable temp folder, in place.

    ``tts_service`` writes the assembled voice-over under ``temp/<run>/`` which
    is cleared on the next launch, so the path stored in the document must point
    at the project store instead. A track that is already there (or already
    gone) is left alone.
    """
    if project.dub is None or not project.dub.track_path:
        return

    source = Path(project.dub.track_path)
    if not source.is_file():
        # Keep the stale path: `open_project` reports it as missing rather than
        # silently dropping the user's voice-over.
        return
    if _is_under(source, audio_dir(settings)):
        return

    folder = audio_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    # Every run writes `<temp>/dub-<uuid>/dub_track.wav`, so the file name alone
    # is not unique: the digest of the source path keeps two projects from
    # sharing (and overwriting) one track.
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:8]
    target = folder / f"{source.stem}-{digest}{source.suffix or '.wav'}"
    if not (target.is_file() and target.stat().st_size == source.stat().st_size):
        shutil.copyfile(source, target)
        log.debug("project: adopted dub track %s -> %s", source, target)
    project.dub.track_path = str(target)


# ---------------------------------------------------------------------------
# Named projects
# ---------------------------------------------------------------------------
def default_path(project: ProjectFile, settings: Settings | None = None) -> Path:
    """Where an unnamed save lands: ``<data>/projects/<name>.svaproj``."""
    stem = project.name or (project.source.name if project.source else "")
    return projects_dir(settings) / f"{_slug(stem)}{PROJECT_SUFFIX}"


def save_project(
    project: ProjectFile,
    path: str | Path | None = None,
    settings: Settings | None = None,
) -> ProjectEnvelope:
    """Write ``project`` to disk and return what was stored."""
    settings = settings or get_settings()
    project = project.model_copy(deep=True)
    _persist_dub(project, settings)

    if path:
        target = Path(path)
        if target.suffix.lower() != PROJECT_SUFFIX:
            target = target.with_suffix(PROJECT_SUFFIX)
    else:
        target = default_path(project, settings)

    project.name = project.name or target.stem
    project.version = PROJECT_VERSION
    project.saved_at = _now()
    project.origin = str(target)
    _write_json(target, project.model_dump(mode="json"))
    log.info("project: saved %s (%d segments)", target, len(project.segments))
    return _envelope(project, target)


def open_project(path: str | Path, settings: Settings | None = None) -> ProjectEnvelope:
    """Read a project file; raises :class:`FileNotFoundError` when it is gone."""
    settings = settings or get_settings()
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Project not found: {source}")
    return _envelope(_read_json(source), source)


def delete_project(path: str | Path) -> bool:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return False
    return True


def list_projects(settings: Settings | None = None) -> ProjectListResult:
    """Known saved projects and unsaved cached snapshots, newest first."""
    resolved = settings or get_settings()
    folder = projects_dir(resolved)
    rows: list[ProjectSummary] = []
    for file in folder.glob(f"*{PROJECT_SUFFIX}"):
        try:
            stat = file.stat()
        except OSError:
            continue
        name, saved_at, damaged = file.stem, None, False
        try:
            stored = _read_json(file)
            name = stored.name or file.stem
            saved_at = stored.saved_at
        except ProjectError as exc:
            damaged = True
            log.debug("project: %s", exc)
        rows.append(
            ProjectSummary(
                path=str(file),
                name=name,
                saved_at=saved_at,
                modified=stat.st_mtime,
                size_bytes=stat.st_size,
                damaged=damaged,
                cached=False,
            )
        )
    rows.sort(key=lambda row: row.modified, reverse=True)

    cached_rows: list[ProjectSummary] = []
    cdir = folder / "_cache"
    cache_candidates: list[Path] = []
    if cdir.is_dir():
        cache_candidates.extend(cdir.glob("*.svacache.json"))
    sess = session_path(resolved)
    if sess.is_file() and not cache_candidates:
        cache_candidates.append(sess)

    for file in cache_candidates:
        try:
            stat = file.stat()
            stored = _read_json(file)
        except (OSError, ProjectError):
            continue
        title = stored.name or (Path(stored.source.path).stem if stored.source and stored.source.path else "Bản nháp chưa lưu")
        if stored.segments:
            title = f"{title} ({len(stored.segments)} dòng)"
        cached_rows.append(
            ProjectSummary(
                path=str(file),
                name=title,
                saved_at=stored.saved_at,
                modified=stat.st_mtime,
                size_bytes=stat.st_size,
                damaged=False,
                cached=True,
            )
        )
    cached_rows.sort(key=lambda row: row.modified, reverse=True)
    return ProjectListResult(projects=rows, cached_projects=cached_rows, folder=str(folder))


# ---------------------------------------------------------------------------
# Session autosave
# ---------------------------------------------------------------------------
def _write_rolling_cache(project: ProjectFile, settings: Settings) -> None:
    """Keep one cached snapshot per source video/name so unsaved work stays recoverable."""
    if not (project.source or project.segments or project.dub):
        return
    import hashlib  # noqa: PLC0415

    key_src = (project.source.path if project.source and project.source.path else project.name) or "draft"
    digest = hashlib.sha1(key_src.encode("utf-8")).hexdigest()[:8]
    stem = _slug(Path(key_src).stem if project.source and project.source.path else (project.name or "draft"))
    cdir = projects_dir(settings) / "_cache"
    cdir.mkdir(parents=True, exist_ok=True)
    target = cdir / f"{stem}_{digest}.svacache.json"
    _write_json(target, project.model_dump(mode="json"))

    # Keep at most 20 recent cached projects.
    try:
        cached = sorted(cdir.glob("*.svacache.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in cached[20:]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def write_session(project: ProjectFile, settings: Settings | None = None) -> ProjectEnvelope:
    """Snapshot the editor so a restart can resume it."""
    settings = settings or get_settings()
    project = project.model_copy(deep=True)
    _persist_dub(project, settings)
    _write_json(session_path(settings), project.model_dump(mode="json"))
    _write_rolling_cache(project, settings)
    return ProjectEnvelope(path=None, project=project, missing=_missing_assets(project))


def read_session(settings: Settings | None = None) -> ProjectEnvelope | None:
    """The last autosave, or ``None`` when there is nothing to resume."""
    settings = settings or get_settings()
    path = session_path(settings)
    if not path.is_file():
        return None
    try:
        project = _read_json(path)
    except ProjectError as exc:
        # A half-written snapshot must never block startup.
        log.warning("project: discarding unreadable session snapshot (%s)", exc)
        return None
    return ProjectEnvelope(path=None, project=project, missing=_missing_assets(project))


def clear_session(settings: Settings | None = None) -> bool:
    try:
        session_path(settings).unlink()
    except FileNotFoundError:
        return False
    return True


__all__ = [
    "PROJECT_SUFFIX",
    "ProjectError",
    "audio_dir",
    "clear_session",
    "default_path",
    "delete_project",
    "list_projects",
    "open_project",
    "projects_dir",
    "read_session",
    "save_project",
    "session_path",
    "write_session",
]
