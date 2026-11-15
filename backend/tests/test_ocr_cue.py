"""A second look at one cue: the window, the consensus and the HTTP contract.

Extraction reads a whole clip and trusts what it saw twice. When one line still
comes out wrong the fix is a targeted re-read of that cue - same crop, same
engine, one to three frames inside the line's own window. These tests pin the
arithmetic of that window and the "majority wins" rule without any weights.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import subtitles as subtitles_route
from app.config import Settings
from app.main import app
from app.services import ocr_service, provider_config

Region = tuple[float, float, float, float]


class _Reader:
    """Stand-in for a loaded engine: one reading per frame file name."""

    def __init__(self, texts: dict[str, list[str]]) -> None:
        self.texts = texts
        self.seen: list[str] = []

    def __call__(self, image: Path) -> list[str]:
        self.seen.append(image.name)
        return self.texts.get(image.name, [])


def _fake_grab(record: list[tuple[float, Region | None]]) -> Callable[..., Path]:
    def grab(video, target, when, *, position, band, region):  # noqa: ANN001, ARG001 - mirrors the real one
        record.append((when, region))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
        return target

    return grab


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------
def test_cue_offsets_land_inside_the_window_never_on_its_edges() -> None:
    # A cue's first and last frames are mid-fade; reading them costs a diacritic.
    assert ocr_service.cue_offsets(2.0, 3.0, 3) == [2.167, 2.5, 2.833]
    assert ocr_service.cue_offsets(2.0, 3.0, 1) == [2.5]


def test_cue_offsets_give_a_degenerate_cue_a_readable_window() -> None:
    # A line whose out-point was edited onto its in-point still needs a frame.
    assert ocr_service.cue_offsets(5.0, 5.0, 1) == [5.1]
    assert ocr_service.cue_offsets(-1.0, 0.2, 2) == [0.05, 0.15]
    # The API and this helper both cap the sample count.
    assert len(ocr_service.cue_offsets(0.0, 10.0, 99)) == 9


# ---------------------------------------------------------------------------
# Consensus between the frames of one cue
# ---------------------------------------------------------------------------
def test_best_reading_counts_the_frames_and_keeps_the_accented_form() -> None:
    readings = [(2.2, "xin chào"), (2.5, "xin chao"), (2.8, "xin chào")]

    text, candidates = ocr_service.best_reading(readings)

    assert text == "xin chào"
    # One line seen twice: the missing diacritic is a spelling of the same cue,
    # not a second candidate to choose between.
    assert candidates == ["xin chào"]


def test_best_reading_keeps_the_majority_line_of_two_rivals() -> None:
    readings = [(1.0, "Em làm mẹ"), (1.5, "Anh làm bố"), (2.0, "Em làm mẹ")]

    text, candidates = ocr_service.best_reading(readings)

    assert text == "Em làm mẹ"
    # The rival stays available: the dialog offers it as a choice.
    assert candidates == ["Em làm mẹ", "Anh làm bố"]


def test_best_reading_prefers_the_longest_line_when_the_frames_disagree() -> None:
    readings = [(1.0, "Đi thôi"), (1.5, "Em chào anh ạ")]

    text, candidates = ocr_service.best_reading(readings)

    assert text == "Em chào anh ạ"
    assert candidates == ["Em chào anh ạ", "Đi thôi"]


def test_best_reading_reports_nothing_for_blank_frames() -> None:
    assert ocr_service.best_reading([(1.0, ""), (1.5, "   ")]) == ("", [])


# ---------------------------------------------------------------------------
# The re-read itself
# ---------------------------------------------------------------------------
def test_read_cue_reuses_the_scan_rectangle_and_keeps_the_middle_still(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    settings = Settings(ocr_region="10,60,90,95", ocr_device="gpu")
    grabbed: list[tuple[float, Region | None]] = []
    reader = _Reader({"cue_00.png": ["xin chao"], "cue_01.png": ["xin chào"], "cue_02.png": ["xin chào"]})

    monkeypatch.setattr(ocr_service, "grab_frame", _fake_grab(grabbed))
    monkeypatch.setattr(
        ocr_service,
        "load_engine",
        lambda *args, **kwargs: ocr_service.Engine(name="rapidocr", device="gpu", read=reader),  # noqa: ARG005
    )
    monkeypatch.setattr(provider_config, "overlay", lambda settings=None: settings)

    reading = ocr_service.read_cue(
        video, start=2.0, end=3.0, samples=3, work_dir=tmp_path / "work", settings=settings
    )

    assert [when for when, _ in grabbed] == [2.167, 2.5, 2.833]
    # The rectangle the user picked is the rectangle a second look crops.
    assert all(region == (10.0, 60.0, 90.0, 95.0) for _, region in grabbed)
    assert reader.seen == ["cue_00.png", "cue_01.png", "cue_02.png"]
    assert reading.text == "xin chào"
    assert reading.frames == [(2.167, "xin chao"), (2.5, "xin chào"), (2.833, "xin chào")]
    # What the dialog shows is the crop the reader actually saw.
    assert reading.frame_path is not None and reading.frame_path.name == "cue_01.png"
    assert (reading.engine, reading.device) == ("rapidocr", "gpu")
    assert (reading.start, reading.end) == (2.0, 3.0)


def test_read_cue_refuses_a_missing_clip(tmp_path: Path) -> None:
    with pytest.raises(ocr_service.OcrError, match="Video not found"):
        ocr_service.read_cue(tmp_path / "nope.mp4", start=0.0, end=1.0, work_dir=tmp_path)


# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_ocr_frame_rejects_a_missing_clip(client: TestClient) -> None:
    response = client.post(
        "/api/subtitles/ocr-frame",
        json={"video_path": "Z:/does-not-exist.mp4", "start": 0.0, "end": 2.0},
    )

    assert response.status_code == 404


def test_ocr_frame_returns_the_reading_without_touching_the_timeline(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    monkeypatch.setattr(subtitles_route, "work_dir", lambda prefix: tmp_path / prefix)
    monkeypatch.setattr(provider_config, "overlay", lambda settings=None: settings)

    def fake_read_cue(video_path, *, start, end, work_dir, samples=3, report=None, settings=None):  # noqa: ARG001
        assert samples == 2
        return ocr_service.CueReading(
            text="Em chào anh",
            candidates=["Em chào anh", "Em chào anh ạ"],
            frames=[(start, "Em chào anh"), (end, "Em chào anh ạ")],
            frame_path=None,
            engine="rapidocr",
            device="cpu",
            start=start,
            end=end,
            region=(0.0, 55.0, 100.0, 100.0),
        )

    monkeypatch.setattr(ocr_service, "read_cue", fake_read_cue)

    response = client.post(
        "/api/subtitles/ocr-frame",
        json={"video_path": str(video), "start": 1.0, "end": 2.0, "samples": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "Em chào anh"
    assert payload["candidates"] == ["Em chào anh", "Em chào anh ạ"]
    assert [frame["time"] for frame in payload["frames"]] == [1.0, 2.0]
    assert (payload["engine"], payload["device"]) == ("rapidocr", "cpu")
    assert payload["region"] == [0.0, 55.0, 100.0, 100.0]
    assert payload["frame_path"] is None
