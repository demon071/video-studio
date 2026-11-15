"""CapCut hand-over: caption geometry, draft layout and the project index."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings, resolve_ffmpeg
from app.schemas.subtitle import DubbingSegmentReport, SubtitleSegment, SubtitleStyle
from app.services import capcut_draft

FFMPEG = resolve_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Keep the index backup inside the test folder, never in the real cache."""
    settings = Settings(data_dir=str(tmp_path / "data"), models_dir=str(tmp_path / "models"))
    monkeypatch.setattr(capcut_draft, "get_settings", lambda: settings)
    return settings


def _clip(path: Path, seconds: float = 2.0) -> Path:
    """A tiny clip that already has the 9:16 shape the draft will ask for."""
    assert FFMPEG is not None
    subprocess.run(  # noqa: S603 - fixed arguments
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s=180x320:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    return path


def _take(path: Path, seconds: float = 0.6) -> Path:
    assert FFMPEG is not None
    subprocess.run(  # noqa: S603 - fixed arguments
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=660:duration={seconds}",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )
    return path


def _report(index: int, start: float, end: float, clip: Path) -> DubbingSegmentReport:
    return DubbingSegmentReport(
        index=index,
        start=start,
        end=end,
        target_duration=end - start,
        tts_duration=end - start,
        applied_ratio=1.0,
        clamped=False,
        clip_path=str(clip),
    )


def _draft_json(result: capcut_draft.DraftResult) -> dict[str, Any]:
    return json.loads((Path(result.draft_path) / "draft_content.json").read_text(encoding="utf-8"))


def _segments_of(draft: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [segment for track in draft["tracks"] if track["type"] == kind for segment in track["segments"]]


def _materials(draft: dict[str, Any], bucket: str) -> list[dict[str, Any]]:
    return draft["materials"][bucket]


# ---------------------------------------------------------------------------
# Geometry: the draft has to match what the MP4 burn would look like
# ---------------------------------------------------------------------------
def test_caption_size_follows_the_frame_height() -> None:
    style = SubtitleStyle()
    vertical, vertical_px = capcut_draft.text_size(style, 1080, 1920)
    landscape, landscape_px = capcut_draft.text_size(style, 1920, 1080)

    # Same 4.2 % of the frame height the ASS export uses.
    assert vertical_px == 80
    assert landscape_px == 45
    assert vertical == pytest.approx(80 * 0.85 * 200 / 1080, abs=1e-3)
    assert vertical > landscape


def test_caption_block_moves_with_the_position_setting() -> None:
    bottom = SubtitleStyle(position="bottom", margin_v=7.0, horizontal="center")
    top = SubtitleStyle(position="top", margin_v=7.0, horizontal="center")
    middle = SubtitleStyle(position="middle", horizontal="center")

    def offset_y(style: SubtitleStyle) -> float:
        width_px = capcut_draft.text_width_px("xin chào", 80)
        return capcut_draft.text_offset(style, 1080, 1920, 80, 1, width_px)[1]

    assert offset_y(middle) == pytest.approx(0.0, abs=1e-6)
    assert offset_y(bottom) < -0.5
    assert offset_y(top) > 0.5


def test_left_caption_keeps_its_margin() -> None:
    left = SubtitleStyle(horizontal="left", margin_h=8.0)
    center = SubtitleStyle(horizontal="center")
    width_px = capcut_draft.text_width_px("xin chào", 80)

    x_left = capcut_draft.text_offset(left, 1080, 1920, 80, 1, width_px)[0]
    x_center = capcut_draft.text_offset(center, 1080, 1920, 80, 1, width_px)[0]

    assert x_center == pytest.approx(0.0, abs=1e-6)
    assert -0.9 < x_left < 0.0


def test_font_lookup_prefers_the_regular_face() -> None:
    plain = capcut_draft.font_path("Arial")
    if not plain:
        pytest.skip("Arial is not installed on this machine")

    bold = capcut_draft.font_path("Arial", bold=True)
    assert Path(plain).is_file()
    assert Path(bold).is_file()
    assert plain != bold
    # ARIAALNB.TTF is Arial *Narrow* Bold: a condensed face must never win.
    assert "narrow" not in Path(plain).name.casefold()
    assert "narrow" not in Path(bold).name.casefold()
    assert capcut_draft.font_path("No Such Family 123") == ""


# ---------------------------------------------------------------------------
# The draft folder itself
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_draft_folder_is_a_capcut_project(isolated: Settings, tmp_path: Path) -> None:
    clip = _clip(tmp_path / "clip.mp4")
    spec = capcut_draft.DraftSpec(
        video_path=str(clip),
        aspect="9:16",
        segments=[
            SubtitleSegment(index=1, start=0.0, end=1.0, text="xin chào", translation="hello"),
            SubtitleSegment(index=2, start=1.0, end=2.0, text="tạm biệt"),
        ],
        use_translation=True,
    )

    result = capcut_draft.export(spec, root=tmp_path / "drafts", name="Tập 001")
    folder = Path(result.draft_path)

    # CapCut looks for all of these; a missing one is a missing panel.
    for name in (
        "draft_content.json",
        "draft_content.json.bak",
        "draft_meta_info.json",
        "Timelines/project.json",
        "shortvideoai.json",
    ):
        assert (folder / name).is_file(), name
    assert (folder / "Timelines" / "project.json").is_file()

    draft = _draft_json(result)
    assert draft["canvas_config"] == {"ratio": "9:16", "width": 1080, "height": 1920, "background": None}
    assert draft["version"] == capcut_draft.DRAFT_VERSION
    assert draft["new_version"] == capcut_draft.NEW_VERSION
    assert set(draft["materials"]) == set(capcut_draft.MATERIAL_KEYS)

    assert [track["type"] for track in draft["tracks"]] == ["video", "text"]
    assert result.tracks == 2
    assert result.captions == 2
    assert result.dub_lines == 0
    assert result.media_files == 1

    # The clip is referenced where it lives: a hand-over must not duplicate it.
    video = _materials(draft, "videos")[0]
    assert video["path"] == clip.as_posix()


@needs_ffmpeg
def test_captions_are_separate_clips_in_reading_order(isolated: Settings, tmp_path: Path) -> None:
    clip = _clip(tmp_path / "clip.mp4")
    spec = capcut_draft.DraftSpec(
        video_path=str(clip),
        segments=[
            SubtitleSegment(index=1, start=0.0, end=1.0, text="xin chào", translation="hello"),
            SubtitleSegment(index=2, start=1.0, end=2.0, text="tạm biệt"),
        ],
        use_translation=True,
        style=SubtitleStyle(bold=False),
    )

    result = capcut_draft.export(spec, root=tmp_path / "drafts", name="phude")
    draft = _draft_json(result)
    captions = _segments_of(draft, "text")

    assert len(captions) == 2
    starts = [segment["target_timerange"]["start"] for segment in captions]
    assert starts == sorted(starts)
    # One cue must never sit on top of the next one, it would be unreadable.
    ends = [segment["target_timerange"]["start"] + segment["target_timerange"]["duration"] for segment in captions]
    assert all(ends[index] <= starts[index + 1] for index in range(len(starts) - 1))

    contents = [json.loads(material["content"]) for material in _materials(draft, "texts")]
    assert [content["text"] for content in contents] == ["hello", "tạm biệt"]
    # The style range is counted in UTF-16 units, like the app's own drafts.
    assert contents[0]["styles"][0]["range"] == [0, len("hello")]
    assert contents[0]["styles"][0]["font"]["path"] == capcut_draft.font_path("Arial")


@needs_ffmpeg
def test_materials_have_no_dangling_references(isolated: Settings, tmp_path: Path) -> None:
    clip = _clip(tmp_path / "clip.mp4")
    spec = capcut_draft.DraftSpec(
        video_path=str(clip),
        segments=[SubtitleSegment(index=1, start=0.0, end=1.0, text="xin chào")],
        dub_segments=[_report(1, 0.0, 0.6, _take(tmp_path / "take.wav"))],
    )

    result = capcut_draft.export(spec, root=tmp_path / "drafts", name="refs")
    draft = _draft_json(result)

    known = {material["id"] for bucket in draft["materials"].values() for material in bucket}
    referenced = set()
    for track in draft["tracks"]:
        assert track["segments"], track["name"]
        for segment in track["segments"]:
            referenced.add(segment["material_id"])
            assert set(segment["extra_material_refs"]) <= known
    assert referenced <= known


@needs_ffmpeg
def test_dub_lines_become_one_clip_each(isolated: Settings, tmp_path: Path) -> None:
    clip = _clip(tmp_path / "clip.mp4")
    first = _take(tmp_path / "one.wav", 0.6)
    second = _take(tmp_path / "two.wav", 0.8)
    spec = capcut_draft.DraftSpec(
        video_path=str(clip),
        dub_segments=[_report(1, 0.0, 0.6, first), _report(2, 1.2, 2.0, second)],
        keep_original_audio=True,
        background_gain=0.35,
    )

    result = capcut_draft.export(spec, root=tmp_path / "drafts", name="longtieng")
    draft = _draft_json(result)

    assert [track["type"] for track in draft["tracks"]] == ["video", "audio"]
    assert result.dub_lines == 2
    assert result.media_files == 3

    takes = _segments_of(draft, "audio")
    assert len(takes) == 2
    assert takes[0]["target_timerange"]["start"] == 0
    assert takes[1]["target_timerange"]["start"] == capcut_draft._us(1.2)
    # Audio segments carry no picture transform, CapCut writes `null` there.
    assert takes[0]["clip"] is None
    assert takes[0]["uniform_scale"] is None

    # The original soundtrack survives underneath, at the ducked level.
    assert _segments_of(draft, "video")[0]["volume"] == pytest.approx(0.35)


@needs_ffmpeg
def test_a_project_without_a_dub_keeps_its_full_sound(isolated: Settings, tmp_path: Path) -> None:
    clip = _clip(tmp_path / "clip.mp4")
    spec = capcut_draft.DraftSpec(video_path=str(clip), background_gain=0.2)

    result = capcut_draft.export(spec, root=tmp_path / "drafts", name="nguyenban")
    draft = _draft_json(result)

    assert _segments_of(draft, "video")[0]["volume"] == pytest.approx(1.0)
    assert "Chưa có phụ đề nào" in " ".join(result.warnings)


@needs_ffmpeg
def test_export_refuses_a_missing_source(isolated: Settings, tmp_path: Path) -> None:
    spec = capcut_draft.DraftSpec(video_path=str(tmp_path / "ghost.mp4"))

    with pytest.raises(capcut_draft.DraftError):
        capcut_draft.export(spec, root=tmp_path / "drafts")


# ---------------------------------------------------------------------------
# CapCut's own project list
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_export_appends_the_draft_to_the_capcut_index(isolated: Settings, tmp_path: Path) -> None:
    root = tmp_path / "drafts"
    root.mkdir()
    index = root / "root_meta_info.json"
    index.write_text(
        json.dumps(
            {
                "all_draft_store": [{"draft_id": "OLD-1", "draft_name": "Cũ"}],
                "draft_ids": 7,
                "root_path": str(root),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    clip = _clip(tmp_path / "clip.mp4")
    spec = capcut_draft.DraftSpec(video_path=str(clip), segments=[])
    result = capcut_draft.export(spec, root=root, name="Kiểm tra draft")

    assert result.registered is True
    assert result.index_backup is not None
    assert Path(result.index_backup).is_file()

    stored = json.loads(index.read_text(encoding="utf-8"))
    assert [entry["draft_id"] for entry in stored["all_draft_store"]][0] == "OLD-1"
    added = stored["all_draft_store"][-1]
    assert added["draft_name"] == "Kiểm tra draft"
    assert added["draft_fold_path"] == Path(result.draft_path).as_posix()
    assert added["draft_json_file"] == (Path(result.draft_path) / "draft_content.json").as_posix()
    assert stored["draft_ids"] == 8
    assert stored["root_path"] == str(root)


@needs_ffmpeg
def test_second_export_with_the_same_name_gets_its_own_folder(isolated: Settings, tmp_path: Path) -> None:
    root = tmp_path / "drafts"
    clip = _clip(tmp_path / "clip.mp4")
    spec = capcut_draft.DraftSpec(video_path=str(clip))

    first = capcut_draft.export(spec, root=root, name="Tập 001")
    second = capcut_draft.export(spec, root=root, name="Tập 001")

    assert first.draft_name == "Tập 001"
    assert second.draft_name == "Tập 001 2"
    assert Path(first.draft_path).is_dir() and Path(second.draft_path).is_dir()


@needs_ffmpeg
def test_a_folder_without_an_index_is_not_registered(isolated: Settings, tmp_path: Path) -> None:
    root = tmp_path / "elsewhere"
    clip = _clip(tmp_path / "clip.mp4")
    spec = capcut_draft.DraftSpec(video_path=str(clip))

    result = capcut_draft.export(spec, root=root, name="ngoai-capcut")

    assert result.registered is False
    assert result.index_backup is None
    # Never invent an index CapCut does not own.
    assert not (root / "root_meta_info.json").exists()


@needs_ffmpeg
def test_registration_is_idempotent(isolated: Settings, tmp_path: Path) -> None:
    root = tmp_path / "drafts"
    root.mkdir()
    (root / "root_meta_info.json").write_text(
        json.dumps({"all_draft_store": [], "draft_ids": 0}), encoding="utf-8"
    )
    clip = _clip(tmp_path / "clip.mp4")
    result = capcut_draft.export(capcut_draft.DraftSpec(video_path=str(clip)), root=root, name="một lần")

    assert capcut_draft.register_draft(root, Path(result.draft_path)) is None
    stored = json.loads((root / "root_meta_info.json").read_text(encoding="utf-8"))
    assert len(stored["all_draft_store"]) == 1


def test_draft_folder_name_drops_path_characters() -> None:
    assert capcut_draft._safe_folder('a/b:c*d?"e"') == "a b c d e"
    assert capcut_draft._safe_folder("   ") == "ShortVideoAI"


# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_export_route_hands_back_the_draft(isolated: Settings, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    clip = _clip(tmp_path / "clip.mp4")
    with TestClient(app) as client:
        response = client.post(
            "/api/export/capcut",
            json={
                "video_path": str(clip),
                "aspect": "9:16",
                "segments": [{"index": 1, "start": 0.0, "end": 1.5, "text": "xin chào"}],
                "draft_name": "Từ API",
                "output_dir": str(tmp_path / "drafts"),
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["draft_name"] == "Từ API"
    assert payload["captions"] == 1
    assert payload["registered"] is False
    assert Path(payload["draft_path"], "draft_content.json").is_file()


def test_export_route_rejects_a_missing_video(isolated: Settings, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        response = client.post(
            "/api/export/capcut",
            json={"video_path": str(tmp_path / "ghost.mp4"), "output_dir": str(tmp_path / "drafts")},
        )

    assert response.status_code == 404
    assert "Không tìm thấy video nguồn" in response.json()["detail"]


def test_targets_route_reports_the_capcut_folder(isolated: Settings) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        payload = client.get("/api/export/capcut/targets").json()

    assert set(payload) == {
        "drafts_root",
        "exists",
        "fallback_root",
        "capcut_version",
        "capcut_running",
        "drafts",
    }
    assert payload["fallback_root"].endswith(("capcut", "capcut\\"))
    assert payload["drafts"] >= 0
