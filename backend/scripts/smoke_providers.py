"""Smoke-test the new provider clients against the live services."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from app.services import capcut_client, web_translate


def web() -> int:
    lines = ["你到底是谁？", "我只是一个普通人。", "别走，听我说完。"]
    for provider in ("google", "bing"):
        started = time.monotonic()
        try:
            # No credentials: this is the plain engine, not the app.
            answers = web_translate.translate_texts(lines, provider=provider, source="zh", target="vi")
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"{provider:6s} FAILED  {type(exc).__name__}: {exc}")
            continue
        elapsed = time.monotonic() - started
        print(f"{provider:6s} {elapsed:5.2f}s  {answers}")
    return 0


def tts(text: str) -> int:
    started = time.monotonic()
    try:
        audio = capcut_client.synthesize(text, voice="BV421_vivn_streaming")
    except Exception as exc:  # noqa: BLE001
        print(f"capcut tts FAILED  {type(exc).__name__}: {exc}")
        return 1
    target = Path("pipeline-check") / "capcut-tts.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(audio)
    print(f"capcut tts ok  {len(audio)} bytes in {time.monotonic() - started:.2f}s -> {target}")
    return 0


def stt(video: str, seconds: float) -> int:
    from app.services import bcut_client, media_processor

    source = Path(video)
    work = Path("pipeline-check") / "smoke"
    wav = work / "slice.wav"
    work.mkdir(parents=True, exist_ok=True)
    media_processor.run_ffmpeg(
        [
            "-ss",
            "0",
            "-i",
            str(source),
            "-t",
            f"{seconds:.1f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ]
    )
    for name, engine in (("capcut", capcut_client.transcribe), ("bilibili", bcut_client.transcribe)):
        started = time.monotonic()
        try:
            lines = engine(wav, work_dir=work / name)
        except Exception as exc:  # noqa: BLE001
            import traceback

            print(f"{name:9s} FAILED  {type(exc).__name__}: {exc}")
            traceback.print_exc()
            continue
        print(f"{name:9s} {time.monotonic() - started:5.1f}s  {len(lines)} line(s)")
        for start, end, text in lines[:8]:
            print(f"   {start:7.2f} -> {end:7.2f}  {text}")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "web"
    if mode == "web":
        raise SystemExit(web())
    if mode == "tts":
        raise SystemExit(tts(sys.argv[2] if len(sys.argv) > 2 else "Xin chào, đây là giọng đọc CapCut."))
    if mode == "stt":
        raise SystemExit(stt(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 30.0))
    raise SystemExit(f"unknown mode {mode}")
