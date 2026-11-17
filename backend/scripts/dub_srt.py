"""Dub a subtitle file on its own timeline - one audio track, no video needed.

    python backend/scripts/dub_srt.py "001 ... .vi.srt" --provider omnivoice --steps 16

The SRT becomes ``SubtitleSegment`` objects, so the takes land exactly where the
subtitles sit and the app's slot maths applies: a line that runs long is sped up
(capped by ``SVA_MAX_TEMPO_RATIO``) instead of being cut. Rewording to fit is
off - that is an LLM pass, and a listen-back run must not wait on a second model.

Outputs, next to the subtitle file unless -o says otherwise:
    <name>.dub.wav        the whole track, same length as the last cue
    <name>.dub-parts/     per-line takes, so a single line can be re-listened

OmniVoice's ``auto`` and ``design`` voices are invented per line. ``--lock-voice``
speaks the opener once, keeps that take as the reference and clones it for every
other line, which is the only way 47 cues come out as one speaker.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings  # noqa: E402
from app.schemas.subtitle import SubtitleSegment  # noqa: E402
from app.services import provider_config, tts_service  # noqa: E402

STAMP = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
TAGS = re.compile(r"<[^>]+>|\{[^}]*\}")
# A clone reference wants 3-10 s, and every second of it is re-attended on every
# diffusion step of every line: a 7 s reference measured 40 s per line against
# 12 s for the promptless auto voice. Two short lines (~4 s) is the sweet spot.
LOCK_LINES = 2
LOCK_CHARS = 55


def _seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000


def parse_srt(path: Path) -> list[SubtitleSegment]:
    """The cues of an SRT file, with formatting tags stripped."""
    text = path.read_text(encoding="utf-8-sig")
    cues: list[SubtitleSegment] = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing = next((line for line in lines if STAMP.search(line)), None)
        if timing is None:
            continue
        found = STAMP.search(timing)
        assert found is not None  # guarded by the generator above
        start = _seconds(*found.group(1, 2, 3, 4))
        end = _seconds(*found.group(5, 6, 7, 8))
        spoken = " ".join(TAGS.sub("", line) for line in lines[lines.index(timing) + 1 :]).strip()
        if not spoken or end <= start:
            continue
        cues.append(SubtitleSegment(index=len(cues) + 1, start=start, end=end, text=spoken))
    return cues


def lock_reference(
    cues: list[SubtitleSegment],
    folder: Path,
    *,
    voice: str | None,
    provider: str,
    settings: Settings,
) -> tuple[str, str]:
    """Speak the opening cues once and return ``(clone:<wav>, transcript)``.

    ``auto`` and ``design`` describe a voice instead of naming one, so the model
    invents a fresh speaker on every call. Joining the first takes into a 3-10 s
    sample turns that one invented voice into the reference the rest of the file
    clones, which is what makes 47 lines sound like 47 lines of one person.
    """
    import numpy as np  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415

    folder.mkdir(parents=True, exist_ok=True)
    picks: list[SubtitleSegment] = []
    characters = 0
    for cue in cues:
        picks.append(cue)
        characters += len(cue.text)
        if characters >= LOCK_CHARS or len(picks) >= LOCK_LINES:
            break

    takes = []
    for position, cue in enumerate(picks):
        target = folder / f"ref-{position + 1:02d}.wav"
        print(f"  reference take {position + 1}/{len(picks)}: {cue.text}", flush=True)
        tts_service.synthesize(cue.text, target, provider=provider, voice=voice, settings=settings)
        samples, rate = sf.read(str(target), dtype="float32")
        takes.append((np.asarray(samples, dtype=np.float32).reshape(-1), rate))

    rate = takes[0][1]
    gap = np.zeros(int(rate * 0.12), dtype=np.float32)
    joined = np.concatenate([part for samples, _ in takes for part in (samples, gap)])
    reference = folder / "voice-lock.wav"
    sf.write(str(reference), joined, rate, subtype="PCM_16")
    transcript = " ".join(cue.text for cue in picks)
    print(f"  locked voice: {reference.name} ({joined.size / rate:.1f}s, {len(picks)} lines)", flush=True)
    return f"clone:{reference}", transcript


def main() -> int:
    parser = argparse.ArgumentParser(description="Dub an SRT with the engine's own pipeline.")
    parser.add_argument("srt", type=Path, help="subtitle file to speak")
    parser.add_argument("-o", "--out", type=Path, default=None, help="track path (default: <srt>.dub.wav)")
    parser.add_argument("-p", "--provider", default="omnivoice", help="capcut | omnivoice | elevenlabs")
    parser.add_argument("-v", "--voice", default=None, help="auto, design:<words> or clone:<wav>")
    parser.add_argument("-s", "--steps", type=int, default=16, help="OmniVoice diffusion steps (4-64)")
    parser.add_argument("-l", "--language", default="vi", help="language the voice reads")
    parser.add_argument("--work", type=Path, default=None, help="folder for the per-line takes")
    parser.add_argument(
        "--lock-voice",
        action="store_true",
        help="auto/design invent a speaker per line: speak the opener once and clone it for the rest",
    )
    args = parser.parse_args()

    if not args.srt.is_file():
        print(f"no subtitle file at {args.srt}", file=sys.stderr)
        return 2

    cues = parse_srt(args.srt)
    if not cues:
        print(f"{args.srt.name} has no usable cue", file=sys.stderr)
        return 2

    track = args.out or args.srt.with_name(f"{args.srt.stem}.dub.wav")
    work = args.work or args.srt.with_name(f"{args.srt.stem}.dub-parts")
    settings = get_settings().model_copy(
        update={
            "tts_provider": args.provider,
            "target_language": args.language,
            "omnivoice_steps": args.steps,
        }
    )

    print(f"{len(cues)} cues, {cues[-1].end:.1f}s of timeline -> {track.name}")
    speaker = tts_service.resolve_voice(args.provider, args.voice, provider_config.overlay(settings))
    if args.lock_voice and args.provider == "omnivoice" and not speaker.lower().startswith("clone:"):
        print("locking one voice for the whole file", flush=True)
        spoken, transcript = lock_reference(
            cues, Path(work) / "lock", voice=args.voice, provider=args.provider, settings=settings
        )
        speaker = spoken
        settings = settings.model_copy(update={"omnivoice_reference_text": transcript})

    started = time.perf_counter()

    def report(*, message: str | None = None, **_ignored: object) -> None:
        if message:
            print(f"  {message}", flush=True)

    outcome = tts_service.dub(
        cues,
        work_dir=work,
        provider=args.provider,
        voice=speaker,
        timing_rewrite=False,
        language=args.language,
        report=report,
        settings=settings,
    )

    worst = max(outcome.reports, key=lambda item: item.applied_ratio, default=None)
    elapsed = time.perf_counter() - started

    # dub() assembles the track inside the work folder; the documented output is
    # the one next to the subtitle file, so move it there.
    produced = Path(outcome.track_path)
    if produced.resolve() != track.resolve():
        track.parent.mkdir(parents=True, exist_ok=True)
        produced.replace(track)

    print(f"track: {track}")
    print(f"duration: {outcome.duration:.1f}s  lines: {len(outcome.reports)}  skipped: {outcome.skipped}")
    if worst is not None:
        print(
            f"fastest speed-up: {worst.applied_ratio:.2f}x on line {worst.index} (clamped: {worst.clamped})"
        )
    print(f"generated in {elapsed / 60:.1f} min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
