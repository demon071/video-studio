"""SenseVoice through sherpa-onnx: the offline Chinese path.

Ported from CapCap's ``app/sensevoice_processor.py``, where this model is the CPU
default for exactly this material (vertical short drama, music under dialogue).
Two things make it worth carrying over next to faster-whisper:

* it reads zh / en / ja / ko / yue and *nothing else* - it cannot answer "ru" for
  a Chinese clip, which is what whisper did on "002 - Tập 002" (detected `ru` at
  0.45 from the music intro, then 53 lines written in Russian);
* it emits punctuation and inverse text normalisation, so a line arrives
  clause-sized instead of being stitched up later.

Only the recognition is shared; the windows still come from
``stt_service._speech_windows``, so both engines hear the same, unspliced audio.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.progress import NULL_REPORTER, Reporter
from app.services import model_manager, subtitle_timeline

log = logging.getLogger(__name__)

SPEC_KEY = "sensevoice-zh-en-ja-ko-yue"
SAMPLE_RATE = 16_000

# What the checkpoint was trained on; anything else has to go to whisper.
LANGUAGES = frozenset({"auto", "zh", "en", "ja", "ko", "yue"})

_TERMINAL = frozenset("。！？?!;；\n")
_SOFT = frozenset("，,、…")
_TAG_RE = re.compile(r"<\|[^|>]*\|>")
_SPACE_RE = re.compile(r"\s+")

# The recogniser is ~240 MB and is loaded once per (folder, language); the model
# is used from several jobs, so loading is serialised.
_LOCK = threading.Lock()
_LOADED: tuple[str, str, Any] | None = None


def language_code(value: str | None) -> str:
    """``zh-CN``/``jp``/``kr`` -> the code the checkpoint was trained with."""
    code = str(value or "").strip().lower().replace("_", "-").split("-")[0]
    return {"jp": "ja", "kr": "ko", "cn": "zh", "chi": "zh"}.get(code, code)


def available() -> bool:
    """True when the sherpa-onnx runtime can be imported."""
    try:
        import sherpa_onnx  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def folder(settings: Settings | None = None) -> Path:
    """Where the checkpoint lives (``models/sensevoice/<key>``)."""
    return model_manager.model_dir(model_manager.require(SPEC_KEY), settings)


def installed(settings: Settings | None = None) -> bool:
    return model_manager.status(model_manager.require(SPEC_KEY), settings).downloaded


def load(directory: Path, language: str = "auto") -> Any:
    """The recogniser for one folder and language, loaded on first use.

    sherpa-onnx renamed the language argument between releases
    (``sense_voice_language`` -> ``language``), so the call is tried both ways.
    """
    global _LOADED  # noqa: PLW0603 - one checkpoint per process
    language = language_code(language) or "auto"
    if language not in LANGUAGES:
        raise ValueError(f"SenseVoice does not read '{language}'")
    key = (str(directory), language)
    with _LOCK:
        if _LOADED is not None and _LOADED[:2] == key:
            return _LOADED[2]
        import sherpa_onnx  # noqa: PLC0415

        model_path = directory / "model.int8.onnx"
        if not model_path.is_file():
            model_path = directory / "model.onnx"
        tokens = directory / "tokens.txt"
        if not model_path.is_file() or not tokens.is_file():
            raise FileNotFoundError(f"SenseVoice files are missing in {directory}")
        options = {
            "model": str(model_path),
            "tokens": str(tokens),
            "num_threads": max(1, min(4, os.cpu_count() or 1)),
            "use_itn": True,
        }
        try:
            recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                language=language, **options
            )
        except TypeError:  # 1.x: the argument was named sense_voice_language
            try:
                recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    sense_voice_language=language, **options
                )
            except TypeError:  # very old wheel: let the model identify the language
                recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(**options)
        _LOADED = (key[0], key[1], recognizer)
        log.info("SenseVoice loaded from %s (language %s)", directory, language)
        return recognizer


def _is_punct(token: str) -> bool:
    return any(char in _TERMINAL or char in _SOFT or char.isspace() for char in token)


def _clean(text: str) -> str:
    """Drop the model's ``<|zh|><|NEUTRAL|>`` markers and settle the spacing."""
    return _SPACE_RE.sub(" ", _TAG_RE.sub("", text)).strip()


def split_tokens(
    tokens: Sequence[str],
    times: Sequence[float],
    start: float,
    end: float,
) -> list[tuple[float, float, str]]:
    """Cut one window's token stream into subtitle lines.

    The rule is CapCap's, kept because it was tuned on the same kind of audio: a
    full stop always ends a line; a comma ends one once the clause is three words
    long and either pauses or already runs 1.5 s; a 0.65 s silence ends one
    without any punctuation. A line that starts with a lone "啊，" belongs to the
    line it introduces, not to its own cue.

    Two things had to be added, because this engine times tokens differently from
    whisper: the *last* token of an utterance carries the end of the stream, so a
    closing "。" arrives seconds after the word it closes ("交换戒指" 5.2 s, "。" at
    14.5 s), and that same mark can open the next line because the pause before it
    already cut the line. A line therefore ends on its last *word*, and a mark is
    always attached to the line it closes.
    """
    if not tokens or len(tokens) != len(times):
        return []
    items = [(str(token), float(when)) for token, when in zip(tokens, times, strict=False)]

    def word_time(index: int) -> float:
        """When the word at ``index`` was spoken; punctuation borrows the one before."""
        for token, when in reversed(items[: index + 1]):
            if not _is_punct(token):
                return when
        return items[index][1]

    lines: list[list[Any]] = []
    current: list[str] = []
    current_times: list[float] = []

    def flush(upto: float) -> None:
        if not current:
            return
        head = 0
        while head < len(current) and _is_punct(current[head]):
            head += 1
        lead = _clean("".join(current[:head]))
        body = _clean("".join(current[head:]))
        started = current_times[head] if head < len(current_times) else upto
        current.clear()
        current_times.clear()
        if lead and lines:
            lines[-1][2] += lead  # "。结果" -> the stop closes the line before
        if not any(char.isalnum() for char in body):
            return
        lines.append([round(started, 3), round(upto + 0.12, 3), body])

    for index, (token, when) in enumerate(items):
        current.append(token)
        current_times.append(when)
        if index == len(items) - 1:
            flush(word_time(index))
            break

        next_word = next(
            (later for later_token, later in items[index + 1 :] if not _is_punct(later_token)),
            None,
        )
        pause = (next_word - word_time(index)) if next_word is not None else 0.0
        span = when - current_times[0]
        words = sum(1 for item in current if not _is_punct(item))

        # Three reasons to end a line: a full stop, a comma after a clause, or a
        # silence long enough to be a sentence break on its own.
        terminal = any(char in _TERMINAL for char in token) and words >= 1
        comma = any(char in _SOFT for char in token) and words >= 3 and (pause >= 0.10 or span >= 1.5)
        silence = pause >= 0.65 and words >= 1
        if terminal or comma or silence:
            flush(word_time(index))

    # A one-character interjection glued to the next line reads better than a cue
    # of its own, which the renderer would flash for a single frame.
    merged: list[list[Any]] = []
    for line in lines:
        if merged:
            previous = merged[-1]
            letters = [char for char in previous[2] if not _is_punct(char)]
            if len(letters) == 1 and line[0] - previous[1] <= 0.25:
                previous[1] = line[1]
                previous[2] += line[2]
                continue
        merged.append(line)

    # The engine can place the first word of the next line before the mark that
    # closed this one, and nothing may overlap another cue.
    ceiling = round(end + 0.2, 3)
    result: list[tuple[float, float, str]] = []
    for position, (left, right, text) in enumerate(merged):
        left = max(left, start)
        right = min(right, ceiling)
        if position + 1 < len(merged):
            right = min(right, round(merged[position + 1][0] - 0.04, 3))
        if right <= left:
            right = round(left + 0.1, 3)  # a cue still needs a length of its own
        result.append((left, right, text))
    return result


def read(
    recognizer: Any,
    audio: Any,
    windows: Sequence[tuple[float, float]],
    *,
    report: Reporter = NULL_REPORTER,
) -> tuple[list[subtitle_timeline.Cue], str]:
    """Decode every window; return the lines and the language heard in them.

    With ``language="auto"`` the checkpoint tags each utterance (``res.lang`` is
    ``<|zh|>``, ``<|en|>``, ...), so the transcript can be labelled with what was
    actually spoken instead of the "auto" that was asked for.
    """
    cues: list[subtitle_timeline.Cue] = []
    votes: Counter[str] = Counter()
    total = max(len(windows), 1)
    for position, (start, end) in enumerate(windows):
        chunk = audio[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)]
        if len(chunk) == 0:
            continue
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, chunk)
        recognizer.decode_stream(stream)
        result = stream.result
        tag = str(getattr(result, "lang", "") or "").strip("<>| ")
        if tag:
            votes[tag] += 1
        # The engine times a token from the start of the chunk it decoded, so the
        # window offset is added here and every line below is absolute.
        stamps = [start + float(when) for when in (getattr(result, "timestamps", ()) or ())]
        for left, right, text in split_tokens(
            getattr(result, "tokens", ()) or (), stamps, start, end
        ):
            if right <= left:
                continue
            cues.append(subtitle_timeline.Cue(start=round(left, 3), end=round(right, 3), text=text))
        report(fraction=min((position + 1) / total, 0.99), message=f"line {len(cues)}")
    heard = votes.most_common(1)[0][0] if votes else ""
    return cues, heard


__all__ = [
    "LANGUAGES",
    "SAMPLE_RATE",
    "SPEC_KEY",
    "available",
    "folder",
    "installed",
    "language_code",
    "load",
    "read",
    "split_tokens",
]
