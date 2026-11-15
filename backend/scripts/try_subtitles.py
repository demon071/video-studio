"""Run the real subtitle pipeline on one video file, in process (no server).

It prints the raw Whisper lines next to the cleaned timeline, then the
translation of every line, and writes .srt/.ass files you can eyeball:

    cd backend
    $env:PYTHONPATH="."; ..\\.venv\\Scripts\\python.exe scripts\\try_subtitles.py "D:\\clip.mp4"

Useful flags:
    --target vi          target language (default: the SVA_TARGET_LANGUAGE setting)
    --style "short drama"genre hint fed to the translation preset
    --no-raw             skip the extra unpolished pass (saves one decode)
    --dub                also voice the first --dub-lines lines and print the fit
    --dub-lines 10       how many lines to voice
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas.subtitle import TranscribeRequest
from app.services import stt_service, translate_service, tts_service
from app.utils.subtitles import to_ass, to_srt


def say(**event: Any) -> None:
    """Reporter sink: keep the running commentary to one short line."""
    message = event.get("message")
    if message:
        print(f"    .. {message}", flush=True)


def _table(title: str, segments: list[Any]) -> None:
    print(f"\n{title}")
    for segment in segments:
        text = getattr(segment, "text", "")
        print(f"  [{segment.index:3d}] {segment.start:7.2f} -> {segment.end:7.2f} "
              f"({segment.end - segment.start:4.2f}s) {text}")


def _cps(segment: Any) -> float:
    return len(segment.text) / max(segment.end - segment.start, 0.05)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check subtitle extraction and translation on a real clip.")
    parser.add_argument("video")
    parser.add_argument("--target", default=None)
    parser.add_argument("--style", default="short drama, vertical video subtitle")
    parser.add_argument("--no-raw", action="store_true", help="skip the unpolished comparison pass")
    parser.add_argument("--dub", action="store_true", help="voice the first lines and report the fit")
    parser.add_argument("--dub-lines", type=int, default=10)
    args = parser.parse_args()

    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # noqa: PLW1514 - console only

    video = Path(args.video)
    if not video.is_file():
        print(f"video not found: {video}")
        return 2

    settings = get_settings()
    target = args.target or settings.target_language
    print(f"video : {video.name}")
    print(f"target: {target} | style: {args.style}")

    started = time.perf_counter()
    if not args.no_raw:
        print("\n== 1. raw recognition (what Whisper returns) ==")
        raw = stt_service.transcribe(
            TranscribeRequest(video_path=str(video), polish=False), report=say, settings=settings
        )
        _table(f"{len(raw.segments)} raw lines - language {raw.language}", raw.segments)
        raw_count = len(raw.segments)

    print("\n== 2. cleaned timeline (what the user gets) ==")
    cleaned = stt_service.transcribe(
        TranscribeRequest(video_path=str(video), polish=True), report=say, settings=settings
    )
    _table(f"{len(cleaned.segments)} clean lines - language {cleaned.language}", cleaned.segments)
    if not args.no_raw:
        print(f"\n  lines: {raw_count} raw -> {len(cleaned.segments)} clean")
    fastest = max(cleaned.segments, key=_cps) if cleaned.segments else None
    if fastest is not None:
        print(f"  densest line: {_cps(fastest):.1f} chars/s (a viewer reads ~15)")
    print(f"  elapsed: {time.perf_counter() - started:.1f}s")

    print(f"\n== 3. translation to {target} ==")
    started = time.perf_counter()
    outcome = translate_service.translate(
        cleaned.segments,
        target_language=target,
        source_language=cleaned.language,
        style=args.style,
        report=say,
        settings=settings,
    )
    print(f"  {outcome.provider}/{outcome.model} in {time.perf_counter() - started:.1f}s, "
          f"{outcome.failed_chunks} failed batch(es)\n")
    for segment in outcome.segments:
        print(f"  [{segment.index:3d}] {segment.text}")
        print(f"        -> {segment.translation or '(untranslated)'}")

    folder = video.parent / "pipeline-check"
    folder.mkdir(parents=True, exist_ok=True)
    srt_path = folder / f"subs-{target}.srt"
    ass_path = folder / f"subs-{target}.ass"
    srt_path.write_text(to_srt(outcome.segments, use_translation=True), encoding="utf-8-sig")
    ass_path.write_text(to_ass(outcome.segments), encoding="utf-8-sig")
    print(f"\nwrote {srt_path}\n      {ass_path}")

    if args.dub:
        print(f"\n== 4. dubbing the first {args.dub_lines} lines ==")
        work = Path(tempfile.mkdtemp(prefix="sva-dub-check-"))
        subset = outcome.segments[: args.dub_lines]
        try:
            dubbed = tts_service.dub(
                subset,
                work_dir=work,
                video_path=str(video),
                language=target,
                report=say,
                settings=settings,
            )
        except tts_service.TtsError as exc:
            print(f"  dubbing failed: {exc}")
            return 1
        print(f"  track: {dubbed.track_path} ({dubbed.duration:.2f}s)")
        for item in dubbed.reports:
            flag = "CLAMPED" if item.clamped else "ok"
            print(f"  [{item.index:3d}] tts={item.tts_duration:5.2f}s slot={item.target_duration:5.2f}s "
                  f"ratio={item.applied_ratio:.2f} {flag} rewritten={item.rewritten}")
            if item.rewritten:
                print(f"        spoken: {item.spoken}")

    print("\ndone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
