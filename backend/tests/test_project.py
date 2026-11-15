"""Project save / open / session autosave.

The contract that matters: a project written today must reopen with its
segments, its settings and a dub track that outlives the temp folder.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.schemas.project import PROJECT_VERSION, ProjectFile, ProjectSource
from app.schemas.subtitle import DubbingResult, SubtitleSegment
from app.services import project_service


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=str(tmp_path))


def _session_project(tmp_path: Path) -> tuple[Settings, ProjectFile]:
    settings = _settings(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake video")
    track = settings.temp_path / "dub-1a2b3c4d" / "dub.wav"
    track.parent.mkdir(parents=True, exist_ok=True)
    track.write_bytes(b"fake voice" * 16)

    project = ProjectFile(
        name="Đi vào bẫy",
        source=ProjectSource(path=str(video), name="clip.mp4", duration=42.5, has_audio=True),
        segments=[
            SubtitleSegment(index=1, start=0.0, end=2.5, text="hello", translation="xin chào"),
        ],
        dub=DubbingResult(
            track_path=str(track),
            duration=42.5,
            provider="capcut",
            voice="BV421_vivn_streaming",
            segments=[],
        ),
    )
    return settings, project


def test_save_lands_in_the_projects_folder(tmp_path: Path) -> None:
    settings, project = _session_project(tmp_path)

    envelope = project_service.save_project(project, settings=settings)

    assert envelope.path
    saved = Path(envelope.path)
    assert saved.is_file()
    assert saved.parent == project_service.projects_dir(settings)
    assert saved.suffix == ".svaproj"
    # The name is slugified for the file but kept verbatim in the document.
    assert saved.stem == "Đi vào bẫy"
    assert envelope.project is not None
    assert envelope.project.name == "Đi vào bẫy"
    assert envelope.project.saved_at
    assert envelope.project.origin == str(saved)
    assert envelope.project.version == PROJECT_VERSION
    assert envelope.missing == []


def test_save_adopts_the_dub_track_out_of_temp(tmp_path: Path) -> None:
    settings, project = _session_project(tmp_path)
    original = Path(project.dub.track_path)

    envelope = project_service.save_project(project, settings=settings)
    assert envelope.project is not None
    adopted = Path(envelope.project.dub.track_path)

    assert adopted != original
    assert adopted.parent == project_service.audio_dir(settings)
    assert adopted.read_bytes() == original.read_bytes()

    # A temp cleanup between sessions must not break the saved project.
    for leftover in original.parent.parent.iterdir():
        for child in leftover.iterdir():
            child.unlink()
        leftover.rmdir()

    reopened = project_service.open_project(saved_path(envelope), settings=settings)
    assert reopened.missing == []
    assert reopened.project is not None
    assert reopened.project.segments[0].translation == "xin chào"


def saved_path(envelope) -> str:
    assert envelope.path
    return envelope.path


def test_two_dub_runs_never_share_one_track_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    adopted: list[Path] = []
    for run in ("dub-aaaaaaaa", "dub-bbbbbbbb"):
        track = settings.temp_path / run / "dub_track.wav"
        track.parent.mkdir(parents=True, exist_ok=True)
        track.write_bytes(run.encode())
        envelope = project_service.save_project(
            ProjectFile(
                name=f"p-{run}",
                dub=DubbingResult(
                    track_path=str(track), duration=1.0, provider="capcut", voice="v", segments=[]
                ),
            ),
            settings=settings,
        )
        assert envelope.project is not None and envelope.project.dub is not None
        adopted.append(Path(envelope.project.dub.track_path))

    assert adopted[0] != adopted[1]
    assert adopted[0].read_bytes() == b"dub-aaaaaaaa"
    assert adopted[1].read_bytes() == b"dub-bbbbbbbb"


def test_save_keeps_an_explicit_path(tmp_path: Path) -> None:
    settings, project = _session_project(tmp_path)
    target = tmp_path / "elsewhere" / "my-edit"

    envelope = project_service.save_project(project, str(target), settings=settings)

    assert envelope.path is not None
    assert Path(envelope.path) == (tmp_path / "elsewhere" / "my-edit.svaproj")
    assert Path(envelope.path).is_file()


def test_open_reports_a_missing_video_and_dub(tmp_path: Path) -> None:
    settings, project = _session_project(tmp_path)
    envelope = project_service.save_project(project, settings=settings)
    assert envelope.project is not None

    # Point the document at a clip and a voice that do not exist.
    stale = envelope.project.model_copy(deep=True)
    stale.source = ProjectSource(path=str(tmp_path / "gone.mp4"))
    stale.dub = DubbingResult(
        track_path=str(tmp_path / "gone.wav"),
        duration=1.0,
        provider="capcut",
        voice="v",
        segments=[],
    )
    project_service.save_project(stale, saved_path(envelope), settings=settings)

    reopened = project_service.open_project(saved_path(envelope), settings=settings)

    assert reopened.missing == ["video", "dub"]


def test_open_of_a_browser_import_is_not_missing_anything(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    project = ProjectFile(source=ProjectSource(path="", name="pasted.mp4"))

    envelope = project_service.save_project(project, settings=settings)

    assert envelope.missing == []


def test_open_of_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        project_service.open_project(tmp_path / "nope.svaproj", settings=_settings(tmp_path))


def test_open_rejects_a_foreign_document(tmp_path: Path) -> None:
    target = tmp_path / "other.svaproj"
    target.write_text(json.dumps({"format": "someone-elses-editor"}), encoding="utf-8")

    with pytest.raises(project_service.ProjectError, match="not a ShortVideoAI project"):
        project_service.open_project(target, settings=_settings(tmp_path))


def test_open_rejects_a_newer_version(tmp_path: Path) -> None:
    target = tmp_path / "future.svaproj"
    target.write_text(
        json.dumps({"format": "shortvideoai-project", "version": PROJECT_VERSION + 1}),
        encoding="utf-8",
    )

    with pytest.raises(project_service.ProjectError, match="newer version"):
        project_service.open_project(target, settings=_settings(tmp_path))


def test_open_tolerates_unknown_keys(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    envelope = project_service.save_project(ProjectFile(), settings=settings)
    assert envelope.path is not None

    payload = json.loads(Path(envelope.path).read_text(encoding="utf-8"))
    payload["effect_stack"] = [{"name": "glow"}]
    Path(envelope.path).write_text(json.dumps(payload), encoding="utf-8")

    reopened = project_service.open_project(envelope.path, settings=settings)
    assert reopened.project is not None


def test_session_round_trip_then_clear(tmp_path: Path) -> None:
    settings, project = _session_project(tmp_path)
    assert project_service.read_session(settings) is None

    stored = project_service.write_session(project, settings=settings)
    assert stored.project is not None
    assert stored.path is None

    resumed = project_service.read_session(settings)
    assert resumed is not None
    assert resumed.project is not None
    assert resumed.project.name == "Đi vào bẫy"
    assert resumed.project.dub is not None
    assert Path(resumed.project.dub.track_path).is_file()

    assert project_service.clear_session(settings) is True
    assert project_service.read_session(settings) is None
    assert project_service.clear_session(settings) is False


def test_a_damaged_session_is_ignored_instead_of_breaking_startup(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    project_service.session_path(settings).parent.mkdir(parents=True, exist_ok=True)
    project_service.session_path(settings).write_text("{not json", encoding="utf-8")

    assert project_service.read_session(settings) is None


def test_list_projects_is_newest_first(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = project_service.save_project(ProjectFile(name="old"), settings=settings)
    second = project_service.save_project(ProjectFile(name="new"), settings=settings)
    assert first.path and second.path
    # Force a distinct mtime: the same second is plausible on a fast disk.
    os.utime(first.path, (1, 1))
    stamp = time.time() + 5
    os.utime(second.path, (stamp, stamp))

    listing = project_service.list_projects(settings)

    assert [row.name for row in listing.projects] == ["new", "old"]
    assert listing.folder == str(project_service.projects_dir(settings))

    Path(second.path).write_text("garbage", encoding="utf-8")
    damaged = project_service.list_projects(settings).projects[0]
    assert damaged.damaged is True


def test_api_save_then_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, project = _session_project(tmp_path)
    monkeypatch.setattr(project_service, "get_settings", lambda: settings)

    with TestClient(app) as client:
        payload = {"project": project.model_dump(mode="json")}
        saved = client.post("/api/project/save", json=payload)
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["missing"] == []

        opened = client.post("/api/project/open", json={"path": body["path"]})
        assert opened.status_code == 200, opened.text
        assert opened.json()["project"]["name"] == "Đi vào bẫy"

        assert client.post("/api/project/open", json={"path": "Z:/nope.svaproj"}).status_code == 404

        listed = client.get("/api/project/list")
        assert listed.status_code == 200
        assert listed.json()["projects"][0]["path"] == body["path"]

        dropped = client.delete("/api/project/file", params={"path": body["path"]})
        assert dropped.status_code == 200, dropped.text
        assert dropped.json()["cleared"] is True
        assert client.post("/api/project/open", json={"path": body["path"]}).status_code == 404
        assert client.get("/api/project/list").json()["projects"] == []

        session = client.put("/api/project/session", json=payload)
        assert session.status_code == 200, session.text
        resumed = client.get("/api/project/session").json()
        assert resumed["project"]["name"] == "Đi vào bẫy"

        assert client.delete("/api/project/session").json()["cleared"] is True
        assert client.get("/api/project/session").json()["project"] is None
