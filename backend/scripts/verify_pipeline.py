"""End-to-end check of the Step 2 engine against a live server.

Prerequisites: the engine is already running on 127.0.0.1:8756 (npm run
dev:backend) and the machine has internet access for CapCut TTS.

It builds a synthetic clip, then exercises probe -> dub (with atempo sync) ->
subtitle export -> final render, while listening on /ws/progress to prove the
progress stream works. Exit code 0 means every assertion passed.

    cd backend
    $env:PYTHONPATH="."; ..\\.venv\\Scripts\\python.exe scripts\\verify_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import websockets

from app.config import resolve_ffmpeg
from app.services import media_processor

BASE = "http://127.0.0.1:8756"
WS_URL = "ws://127.0.0.1:8756/ws/progress"

SEGMENTS = [
    {"index": 1, "start": 0.0, "end": 2.0, "text": "Xin chào các bạn, mình là một video ngắn."},
    {
        "index": 2,
        "start": 2.3,
        "end": 3.7,
        "text": "Hôm nay chúng ta sẽ cùng tìm hiểu một chủ đề cực kỳ thú vị và bổ ích.",
    },
    {"index": 3, "start": 4.0, "end": 6.0, "text": "Nhớ nhấn like và đăng ký kênh nhé."},
]

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("PASS  " if condition else "FAIL  ") + message)
    if not condition:
        failures.append(message)


def make_video(target: Path, seconds: float = 6.5) -> Path:
    ffmpeg = resolve_ffmpeg()
    if ffmpeg is None:
        raise SystemExit("ffmpeg not found")
    subprocess.run(  # noqa: S603 - fixed arguments
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1280x720:rate=30:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:duration={seconds}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(target),
        ],
        check=True,
    )
    return target


async def collect(socket, frames: list[dict]) -> None:
    try:
        while True:
            frames.append(json.loads(await socket.recv()))
    except Exception:  # noqa: BLE001 - socket closed at the end of the run
        return


async def run(work: Path) -> None:
    video = make_video(work / "source.mp4")
    print(f"source: {video}")

    async with websockets.connect(WS_URL, max_size=None) as socket:
        hello = json.loads(await socket.recv())
        check(hello["type"] == "hello", "websocket handshake sends hardware hello")
        print(f"      device: {hello['data']['hardware']['device']} | models: {len(hello['data']['models'])}")

        frames: list[dict] = []
        listener = asyncio.create_task(collect(socket, frames))

        async with httpx.AsyncClient(
            base_url=BASE,
            timeout=httpx.Timeout(connect=10.0, read=900.0, write=120.0, pool=30.0),
        ) as client:
            health = (await client.get("/api/health")).json()
            check(health["status"] == "ok" and health["ffmpeg"], "health reports ffmpeg available")

            probe = (await client.post("/api/media/probe", json={"video_path": str(video)})).json()
            check(probe["duration"] > 6.0 and probe["width"] == 1280, f"probe reads the clip ({probe['duration']}s)")

            print("\n-- dubbing --")
            dub_response = await client.post(
                "/api/dubbing/synthesize",
                json={
                    "segments": SEGMENTS,
                    "use_translation": False,
                    "provider": "capcut",
                    "voice": "BV421_vivn_streaming",
                },
            )
            check(dub_response.status_code == 200, f"dubbing endpoint returned 200 ({dub_response.status_code})")
            dub = dub_response.json()
            track = Path(dub["track_path"])
            check(track.is_file(), f"dub track written: {track.name}")

            for report, segment in zip(dub["segments"], SEGMENTS, strict=False):
                slot = segment["end"] - segment["start"]
                print(
                    f"      line {report['index']}: tts={report['tts_duration']:.2f}s "
                    f"slot={slot:.2f}s ratio={report['applied_ratio']:.2f} "
                    f"clamped={report['clamped']} trimmed={report.get('overflow_s', 0):.2f}s"
                )
                check(
                    report["applied_ratio"] <= 1.350001,
                    f"line {report['index']} ratio {report['applied_ratio']:.2f} stays under 1.35",
                )
            clipped = [r for r in dub["segments"] if r["clamped"]]
            check(bool(clipped), f"long line was clamped ({len(clipped)} line(s))")

            track_duration = media_processor.probe_duration(track)
            check(
                abs(track_duration - dub["duration"]) < 0.2,
                f"dub track covers the timeline ({track_duration:.2f}s vs {dub['duration']:.2f}s)",
            )

            print("\n-- subtitle export --")
            ass = (await client.post(
                "/api/subtitles/export",
                json={
                    "segments": SEGMENTS,
                    "output_path": str(work / "subs.ass"),
                    "fmt": "ass",
                    "aspect": "9:16",
                },
            )).json()
            check(Path(ass["output_path"]).is_file(), "ass file written")
            check("PlayResY: 1920" in Path(ass["output_path"]).read_text(encoding="utf-8-sig"), "ass targets 1080x1920")

            print("\n-- render --")
            final = work / "final.mp4"
            render = (await client.post(
                "/api/render",
                json={
                    "video_path": str(video),
                    "output_path": str(final),
                    "aspect": "9:16",
                    "subtitle_path": ass["output_path"],
                    "dub_track_path": str(track),
                    "keep_original_audio": True,
                    "background_gain": 0.35,
                },
            )).json()
            check(Path(render["output_path"]).is_file(), "rendered file exists")
            check((render["width"], render["height"]) == (1080, 1920), f"frame is {render['width']}x{render['height']}")
            check(render["size_bytes"] > 100_000, f"output size {render['size_bytes'] / 1024:.0f} KB")
            check(render["duration"] > 6.0, f"duration {render['duration']}s")

            jobs = (await client.get("/api/jobs")).json()["jobs"]
            check(len(jobs) >= 2, f"job history recorded ({len(jobs)} jobs)")
            check(all(job["state"] == "done" for job in jobs), "every job finished as done")

        await asyncio.sleep(0.5)
        listener.cancel()

        kinds: dict[str, int] = {}
        stages: set[str] = set()
        for frame in frames:
            kinds[frame["type"]] = kinds.get(frame["type"], 0) + 1
            if frame.get("stage"):
                stages.add(frame["stage"])
        print(f"\nwebsocket frames: {kinds}")
        print(f"stages observed: {sorted(stages)}")
        check(kinds.get("progress", 0) > 5, "progress frames received")
        check("dubbing" in {f.get("kind") for f in frames if f.get("kind")}, "dubbing job events received")
        check("render" in stages, "render stage reported")

    print(f"\nartifacts in {work}")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="sva-verify-"))
    try:
        asyncio.run(run(work))
    except Exception as exc:  # noqa: BLE001 - report and fail
        print(f"\nERROR: {type(exc).__name__}: {exc}")
        return 1

    print()
    if failures:
        print(f"{len(failures)} CHECK(S) FAILED")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
