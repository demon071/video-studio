"""Re-speaking one cue and re-assembling the track from the takes on disk."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes import dubbing as dubbing_route
from app.config import Settings, resolve_ffmpeg
from app.main import app
from app.schemas.subtitle import DubbingSegmentReport, SubtitleSegment
from app.services import media_processor, tts_service

FFMPEG = resolve_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg is not installed")


def _settings(tmp_path: Path) -> Settings:
    """A project that cannot reach a model: rewrites stay off, nothing downloads."""
    return Settings(
        data_dir=str(tmp_path / "data"),
        models_dir=str(tmp_path / "models"),
        translate_provider="local",
        tts_provider="capcut",
    )


def _tone(path: Path, seconds: float) -> Path:
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
            f"sine=frequency=440:duration={seconds}",
            "-ar",
            "24000",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
    )
    return path


def _fake_voice(monkeypatch: pytest.MonkeyPatch, *, seconds: float, spoken: list[str]) -> None:
    """Swap the provider for a tone, recording what each line asked to say."""

    def synthesize(text: str, output_path: str | Path, **_kwargs) -> Path:
        spoken.append(text)
        return _tone(Path(output_path), seconds)

    monkeypatch.setattr(tts_service, "synthesize", synthesize)


def _segments() -> list[SubtitleSegment]:
    return [
        SubtitleSegment(index=1, start=0.0, end=2.0, text="xin chào", translation="hello"),
        SubtitleSegment(index=2, start=2.0, end=4.0, text="tạm biệt", translation="goodbye"),
    ]


@needs_ffmpeg
def test_cue_speaks_the_edited_line_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spoken: list[str] = []
    _fake_voice(monkeypatch, seconds=1.0, spoken=spoken)

    outcome = tts_service.cue(
        _segments(),
        2,
        work_dir=tmp_path / "work",
        use_translation=True,
        settings=_settings(tmp_path),
    )

    assert spoken == ["goodbye"]
    assert outcome.report.index == 2
    assert outcome.report.clip_path is not None
    assert Path(outcome.report.clip_path).is_file()
    # The pre-rewrite text is kept so the editor can tell a stale take apart.
    assert outcome.report.source == "goodbye"
    # The new take is measured against its own cue, not against the whole track.
    assert outcome.report.target_duration == pytest.approx(2.0, abs=0.35)


@needs_ffmpeg
def test_cue_falls_back_to_the_original_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spoken: list[str] = []
    _fake_voice(monkeypatch, seconds=0.5, spoken=spoken)

    tts_service.cue(
        _segments(),
        1,
        work_dir=tmp_path / "work",
        use_translation=False,
        settings=_settings(tmp_path),
    )

    assert spoken == ["xin chào"]


@needs_ffmpeg
def test_audition_reads_the_line_without_rewording_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressing "listen" must not start a translation pass.

    The rewriter is reachable through the same code path, and on a machine with
    a local model it is a multi-second load - far too heavy for a play button.
    """
    spoken: list[str] = []
    _fake_voice(monkeypatch, seconds=1.0, spoken=spoken)
    long_line = "xin chào, đây là một câu rất dài để chắc chắn bước rút gọn sẽ bị gọi nếu nó được bật"

    def must_not_rewrite(*_args, **_kwargs):
        raise AssertionError("an audition must not reword the line")

    # Pretend a local model is loaded, so rewording *would* be allowed.
    monkeypatch.setattr(tts_service, "_prepare_rewrite", lambda enabled, _settings: enabled)
    monkeypatch.setattr(tts_service.translate_service, "shorten_for_timing", must_not_rewrite)
    segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text=long_line)]

    outcome = tts_service.cue(
        segments,
        1,
        work_dir=tmp_path / "work",
        use_translation=False,
        timing_rewrite=True,
        audition=True,
        settings=_settings(tmp_path),
    )

    assert spoken == [long_line]
    assert outcome.report.rewritten is False
    assert outcome.report.source == long_line


def test_cue_refuses_a_line_that_is_not_on_the_timeline(tmp_path: Path) -> None:
    with pytest.raises(tts_service.TtsError, match="Line 9"):
        tts_service.cue(_segments(), 9, work_dir=tmp_path / "work", settings=_settings(tmp_path))


def test_cue_refuses_an_empty_line(tmp_path: Path) -> None:
    segments = [SubtitleSegment(index=1, start=0.0, end=1.0, text="   ")]
    with pytest.raises(tts_service.TtsError, match="no text"):
        tts_service.cue(segments, 1, work_dir=tmp_path / "work", settings=_settings(tmp_path))


@needs_ffmpeg
def test_dub_remembers_every_take_so_it_can_be_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_voice(monkeypatch, seconds=0.6, spoken=[])

    outcome = tts_service.dub(_segments(), work_dir=tmp_path / "work", settings=_settings(tmp_path))

    assert [item.index for item in outcome.reports] == [1, 2]
    assert all(item.clip_path and Path(item.clip_path).is_file() for item in outcome.reports)


@needs_ffmpeg
def test_remix_lays_the_takes_back_on_the_timeline(tmp_path: Path) -> None:
    first = _tone(tmp_path / "a.wav", 0.5)
    second = _tone(tmp_path / "b.wav", 0.5)
    reports = [
        _report(1, start=0.0, clip=first),
        _report(2, start=1.5, clip=second),
    ]

    track = tts_service.remix(reports, output_path=tmp_path / "track.wav", duration=3.0)

    assert media_processor.probe_duration(track) == pytest.approx(3.0, abs=0.15)


def test_remix_names_the_lines_whose_take_is_gone(tmp_path: Path) -> None:
    reports = [
        _report(3, start=0.0, clip=tmp_path / "gone.wav"),
        _report(4, start=1.0, clip=None),
    ]

    with pytest.raises(tts_service.TtsError, match="lines 3, 4"):
        tts_service.remix(reports, output_path=tmp_path / "track.wav", duration=2.0)


def _report(index: int, *, start: float, clip: Path | None) -> DubbingSegmentReport:
    return DubbingSegmentReport(
        index=index,
        start=start,
        end=start + 1.0,
        target_duration=1.0,
        tts_duration=0.5,
        applied_ratio=1.0,
        clamped=False,
        spoken="a",
        clip_path=str(clip) if clip else None,
    )


# ---------------------------------------------------------------------------
# HTTP contract
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_cue_endpoint_refuses_a_line_that_is_not_on_the_timeline(client: TestClient) -> None:
    response = client.post(
        "/api/dubbing/cue",
        json={"segments": [item.model_dump() for item in _segments()], "index": 9},
    )

    assert response.status_code == 404
    assert "Line 9" in response.json()["detail"]


@needs_ffmpeg
def test_remix_endpoint_hands_back_the_rebuilt_track(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dubbing_route, "work_dir", lambda prefix: tmp_path / prefix)
    tone = _tone(tmp_path / "a.wav", 0.5)

    response = client.post(
        "/api/dubbing/remix",
        json={"segments": [_report(1, start=0.0, clip=tone).model_dump(mode="json")], "duration": 2.0},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lines"] == 1
    assert media_processor.probe_duration(body["track_path"]) == pytest.approx(2.0, abs=0.15)
