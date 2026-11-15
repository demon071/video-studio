"""Subtitle translation with timestamps untouched.

Only the *text* of each segment is sent to the model; ``start``/``end`` never
leave this module. That is the whole trick behind "translate without breaking
sync": the model returns ``{id -> text}`` pairs and the original timeline is
re-assembled around them.

Providers:
  * ``local``  llama-cpp-python + Qwen2.5-3B or Gemma 3 4B GGUF, JSON grammar enforced
  * ``gemini`` google-genai, request shape taken from ``GEMINI_PROFILES``
  * ``openai`` OpenAI chat completions
  * ``google`` free Google Translate web endpoint (see ``web_translate``)
  * ``bing``   free Bing Translator web endpoint
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings, api_key, get_settings
from app.core import hardware
from app.core.progress import NULL_REPORTER, Cancelled, Reporter
from app.schemas.subtitle import SubtitleSegment
from app.services import model_manager, provider_config, stt_service, web_translate

log = logging.getLogger(__name__)

# A 3B model loses the thread somewhere around six lines: it merges two of them
# and then answers every following id with the *next* line's translation, which
# is silent corruption - every line is valid Vietnamese, just the wrong one.
# Batches of three were measured too: they shift just as often and cost twice the
# calls, so the size is a compromise between context and latency, not a cure.
MAX_LINES_PER_CHUNK = 5
MAX_CHARS_PER_CHUNK = 1400


@dataclass(frozen=True)
class GeminiProfile:
    """Request shape for one Gemini model id.

    A free-tier key is limited by *requests per minute*, not by tokens, so the
    batch is the biggest lever available: forty short lines in one call cost one
    request where the local model's five-line batch costs eight. The thinking
    level and the quiet gap between two calls come from the same recommendation
    - they are what keeps a 28-line drama batch out of the 429 bucket.
    """

    lines: int = MAX_LINES_PER_CHUNK
    chars: int = MAX_CHARS_PER_CHUNK
    thinking: str | None = None  # ``ThinkingLevel`` member name; None = model default
    pause: float = 0.0           # quiet seconds kept between two requests


# Anything not listed (2.5 Flash/Pro, a proxy's own model id) keeps a modest
# batch and a short gap. No thinking knob: 2.5 models take a budget, not a level.
GEMINI_DEFAULT = GeminiProfile(lines=20, chars=2800, thinking=None, pause=2.0)

# The two recommended Free Tier shapes. Line counts are the middle of the
# recommended range, so a slightly longer line cannot push a batch over it.
GEMINI_PROFILES: dict[str, GeminiProfile] = {
    # 35-45 lines per request, minimal reasoning, 4 s of quiet between calls:
    # the cheapest way to run a long series through a free key.
    "gemini-3.1-flash-lite": GeminiProfile(lines=40, chars=4500, thinking="MINIMAL", pause=4.0),
    # 25-30 lines per request with a little reasoning: dialogue, address forms
    # and the emotional through-line survive better, and the JSON still parses.
    "gemini-4-hydrogen": GeminiProfile(lines=28, chars=3200, thinking="LOW", pause=3.0),
}


def gemini_profile(model: str) -> GeminiProfile:
    """Tuning for ``model``, matched loosely so a dated or prefixed id lines up."""
    name = (model or "").strip().lower()
    for key in sorted(GEMINI_PROFILES, key=len, reverse=True):
        if key in name:
            return GEMINI_PROFILES[key]
    return GEMINI_DEFAULT


SYSTEM_PROMPT = (
    "You are a professional audiovisual translator working on short-form vertical "
    "video subtitles (TikTok / Reels / Shorts).\n"
    "Rules:\n"
    "1. Translate into {target}. Every \"t\" value must be written in {target} - never "
    "left in the source language, not even a short one-line greeting, and never a copy "
    "of the source characters. A line that is only a person's name or a two-word "
    "exclamation is still a line: answer it, do not skip it.\n"
    "2. Translate faithfully, keeping the spoken tone, humour and emotion of the original.\n"
    "3. Keep every line short enough to be read in the time it is on screen (about "
    "15 characters per second); prefer natural spoken language over literal wording.\n"
    "4. Never merge, split or drop lines. Exactly one output entry per input id, the ids "
    "must be copied unchanged from the input, and two different lines must never share "
    "the same translation.\n"
    "5. Keep numbers, proper nouns, brand names and emojis unchanged.\n"
    '6. Answer with STRICT JSON only: {"lines":[{"i":<id>,"t":"<translated text>"}]}\n'
    "7. Do not add explanations, markdown fences or the source text."
)

# A 3B model does not reliably turn "vi" into "Vietnamese", and guessing wrong
# (Indonesian, English) is silent corruption, so the prompt carries both the
# name and the code.
LANGUAGES = {
    "vi": "Vietnamese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "th": "Thai",
    "id": "Indonesian",
    "ms": "Malay",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ru": "Russian",
    "hi": "Hindi",
    "ar": "Arabic",
    "auto": "the source language",
}


def _language_name(code: str) -> str:
    normalized = (code or "").strip().lower().replace("_", "-").split("-")[0]
    return LANGUAGES.get(normalized, normalized or "the target language")


# Vietnamese address forms. A 3B model translates word by word unless it is told
# how "你/我" land in spoken Vietnamese, and short drama lives or dies on
# "mày - tao" versus "anh - em". Ported from CapCap's translation presets
# (`prompts/presets/zh_short_drama.md`, `subtitle_translation.system.md`).
_VIETNAMESE_RULES = (
    "Address forms (xưng hô): decide who is speaking to whom, then keep ONE pair "
    "per relationship for the whole batch (anh - em, chị - em, mày - tao, tôi - "
    "cậu, sếp - em). Never default close dialogue to 'tôi - bạn', and never use an "
    "affectionate 'em' for someone threatening or confronting the speaker.\n"
    "Names: one spelling per character across every line. Write Chinese, Japanese "
    "or Korean names in their standard Vietnamese (Hán-Việt) form (顾总 -> Cố "
    "tổng, 林浅 -> Lâm Thiển); never leave pinyin and never invent a name."
)

_STYLE_MARKERS: dict[str, tuple[str, ...]] = {
    "drama": ("short drama", "drama", "phim ngan", "phim ngắn", "c-drama", "douyin", "短剧"),
    "romance": ("romance", "tinh cam", "tình cảm", "ngon tinh", "ngôn tình"),
    "anime": ("anime", "animation", "manga", "hoat hinh", "hoạt hình"),
    "documentary": ("documentary", "phong su", "phóng sự", "tai lieu", "tài liệu"),
    "vlog": ("vlog", "social", "tiktok", "reels", "youtube"),
    "action": ("action", "hanh dong", "hành động"),
}

_STYLE_PRESETS: dict[str, str] = {
    "drama": (
        "Genre - short drama: keep every line punchy and high impact; a cue is "
        "one line, not a paragraph. Render threats, reversals and face-slapping "
        "with the force of the original. Usual terms: hào môn, chiến thần, ở rể, "
        "phu nhân, 学长/学姐 -> học trưởng/học tỷ (or anh/chị), 哥们 addressed to "
        "a woman -> cô nương / em gái."
    ),
    "romance": "Genre - romance: keep the intimacy and teasing; never flatten it into a neutral report.",
    "anime": "Genre - animation: keep catchphrases and honorifics natural in Vietnamese; do not explain jokes.",
    "documentary": "Genre - documentary: neutral factual register, exact terminology, no embellishment.",
    "vlog": "Genre - vlog: conversational first person, contractions welcome, keep the creator's tone.",
    "action": "Genre - action: short tense lines; commands and threats must land hard.",
}


def _style_rules(target: str, style: str) -> str:
    """Extra system rules for the target language and the requested genre."""
    rules: list[str] = []
    if _language_name(target) == "Vietnamese":
        rules.append(_VIETNAMESE_RULES)
    lowered = (style or "").lower()
    for key, markers in _STYLE_MARKERS.items():
        if any(marker in lowered for marker in markers):
            rules.append(_STYLE_PRESETS[key])
            break
    return "\n".join(rules)


def _system_prompt(target: str, source: str = "auto", style: str = "") -> str:
    # ``str.replace`` and not ``str.format``: rule 6 is full of JSON braces.
    base = SYSTEM_PROMPT.replace("{target}", _language_name(target))
    # Naming the source stops the 3B model from occasionally *answering* a
    # question it was given instead of translating it.
    source_name = _language_name(source)
    if source_name not in {"the source language", ""}:
        base = f"{base}\nThe dialogue is in {source_name}."
    # A 3B model leaves single characters of the source script inside an
    # otherwise perfect line ("Các khoản này是真的 hay giả"). Saying it once,
    # plainly, removes most of them before the answer even has to be checked.
    target_name = _language_name(target)
    if target_name not in _CJK_LANGUAGES:
        base = (
            f"{base}\nWrite {target_name} letters only: no Chinese, Japanese or Korean "
            "characters anywhere, including inside a name."
        )
    rules = _style_rules(target, style)
    return f"{base}\n{rules}" if rules else base



class TranslateError(RuntimeError):
    """Raised when every chunk failed or the provider is unusable."""


def _not_installed(settings: Settings) -> TranslateError:
    """Actionable error for a missing GGUF (the UI shows this text verbatim)."""
    return TranslateError(
        f"{model_manager.llm_label(settings.llm_model_file)} ({settings.llm_model_file}) is not "
        "installed. Open the Models panel and download it, or switch the provider to Gemini."
    )


@dataclass(frozen=True)
class TranslationOutcome:
    provider: str
    model: str
    segments: list[SubtitleSegment]
    failed_chunks: int = 0


# ---------------------------------------------------------------------------
# LLM lifecycle (a GGUF model is expensive to load, keep one warm)
# ---------------------------------------------------------------------------
_LLM_LOCK = threading.Lock()
_LLM: Any = None
_LLM_KEY: tuple[str, int] | None = None

# llama.cpp must render the turn markers the weights were trained on: Qwen2.5
# answers chatml, Gemma answers <start_of_turn>. A Gemma loaded with chatml gets
# tokens it never saw and degrades quietly instead of failing.
_CHAT_FORMATS = {"gemma": "gemma"}


def _chat_format(path: Path) -> str:
    """Chat template name for the GGUF, matched loosely on the file name."""
    name = path.name.lower()
    for marker, chat_format in _CHAT_FORMATS.items():
        if marker in name:
            return chat_format
    return "chatml"


def _messages(path: Path | None, prompt: str, system: str) -> list[dict[str, str]]:
    """Chat turns for the loaded GGUF.

    Gemma's formatter has no system role - llama.cpp drops the message with a
    debug line - so its rules would vanish and every line would be translated
    without the address-form guidance. They ride inside the user turn instead,
    which is exactly what Gemma's own template does with a system message.
    """
    if path is not None and _chat_format(path) == "gemma":
        return [{"role": "user", "content": f"{system}\n\n{prompt}"}]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]


def unload() -> None:
    global _LLM, _LLM_KEY
    with _LLM_LOCK:
        _LLM = None
        _LLM_KEY = None
    hardware.release_vram()
    log.debug("llama-cpp model unloaded")


def _load_llm(settings: Settings):
    global _LLM, _LLM_KEY
    path = model_manager.llm_path(settings)
    if path is None:
        raise _not_installed(settings)

    profile = hardware.detect()
    layers = settings.llm_gpu_layers
    if layers < 0:
        layers = profile.llm_gpu_layers  # -1 = all blocks, or a VRAM-clamped count
    key = (str(path), int(layers))

    with _LLM_LOCK:
        if _LLM is not None and key == _LLM_KEY:
            return _LLM
        if _LLM is not None:
            # Switching GGUF: let go of the old weights before the new ones are
            # mapped, otherwise both models sit in VRAM on a small card.
            _LLM = None
            _LLM_KEY = None
            hardware.release_vram()
        try:
            from llama_cpp import Llama  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise TranslateError(
                "Offline translation needs llama-cpp-python "
                "(pip install llama-cpp-python). Switch to the Gemini provider instead."
            ) from exc

        log.info("loading GGUF %s (gpu_layers=%s)", path.name, layers)
        _LLM = Llama(
            model_path=str(path),
            n_ctx=settings.llm_context,
            n_gpu_layers=layers,
            n_threads=hardware.cpu_threads(settings),
            n_batch=256,
            verbose=False,
            chat_format=_chat_format(path),
        )
        _LLM_KEY = key
        return _LLM


# ---------------------------------------------------------------------------
# Prompt plumbing
# ---------------------------------------------------------------------------
def _chunk(
    segments: list[SubtitleSegment],
    *,
    max_lines: int = MAX_LINES_PER_CHUNK,
    max_chars: int = MAX_CHARS_PER_CHUNK,
) -> list[list[SubtitleSegment]]:
    """Group the lines into requests, sized by the provider (see GeminiProfile)."""
    chunks: list[list[SubtitleSegment]] = []
    current: list[SubtitleSegment] = []
    length = 0
    for segment in segments:
        if current and (len(current) >= max_lines or length + len(segment.text) > max_chars):
            chunks.append(current)
            current, length = [], 0
        current.append(segment)
        length += len(segment.text)
    if current:
        chunks.append(current)
    return chunks


_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\u1100-\u11ff\uac00-\ud7af]")
_CJK_RE = re.compile(r"[\u1100-\u11ff\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_CJK_LANGUAGES = {"Chinese", "Japanese", "Korean"}

def _source_script(text: str) -> str | None:
    if _HANGUL_RE.search(text):
        return "Korean"
    if _KANA_RE.search(text):
        return "Japanese"
    if _HAN_RE.search(text):
        return "Chinese"
    return None


# There used to be a worked "你好，欢迎回来 -> Xin chào, chào mừng trở lại"
# example in the prompt, to keep a CJK source from being copied out. It did more
# harm than good: the model answered a real name line ("郭先生") with the
# example's own sentence. The letters-only rule in the system prompt does the
# same job without handing the model text it can parrot.


def _user_prompt(
    chunk: list[SubtitleSegment],
    source: str,
    target: str,
    style: str,
) -> str:
    payload = [
        {"i": segment.index, "t": segment.text, "d": round(segment.duration, 2)}
        for segment in chunk
    ]
    target_name = _language_name(target)
    budget = 'Each line carries "d", its on-screen length in seconds: keep the translation short enough to read in it.'
    return (
        f"Source language: {_language_name(source)}\n"
        f"Target language: {target_name} ({target})\n"
        f"Style: {style}\n"
        f"Translate all {len(payload)} lines into {target_name}. {budget}\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
# Qwen2.5-3B drops out of the JSON contract on long batches and numbers the
# lines instead ("1. xin chào"). That answer is still usable, so it is read
# rather than thrown away and re-bought with a recursive halving.
_NUMBERED_RE = re.compile(r"^\s*(?P<id>\d+)\s*[.):\-\u2013\u2014]\s*(?P<text>.+?)\s*$")


def _parse_numbered(text: str) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for line in text.splitlines():
        match = _NUMBERED_RE.match(line)
        if not match:
            continue
        value = match.group("text").strip().strip('"').strip("'").strip()
        if value:
            mapping[int(match.group("id"))] = value
    if not mapping:
        raise TranslateError("model answer contained no lines")
    return mapping


def _parse_lines(raw: str) -> dict[int, str]:
    """Pull ``{id: text}`` out of a model answer, tolerating stray prose."""
    if not raw or not raw.strip():
        raise TranslateError("empty model answer")

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    if "{" not in text:
        return _parse_numbered(text)

    candidate = text
    match = _JSON_OBJECT_RE.search(text)
    if match:
        candidate = match.group(0)

    data = json.loads(candidate)
    entries: Any
    if isinstance(data, dict):
        if any(key in data for key in ("lines", "subtitles", "data")):
            entries = data.get("lines") or data.get("subtitles") or data.get("data") or []
        elif any(key in data for key in ("i", "id", "index", "t", "text", "translation")):
            # A single line without the wrapper: {"i":1,"t":"..."}
            entries = [data]
        else:
            # {"1": "text", "2": "text"}
            entries = [{"i": key, "t": value} for key, value in data.items()]
    else:  # a bare array is common enough to accept
        entries = data
    if isinstance(entries, dict):
        entries = [{"i": key, "t": value} for key, value in entries.items()]

    mapping: dict[int, str] = {}
    for position, entry in enumerate(entries):
        if isinstance(entry, str):
            mapping[position] = entry
            continue
        if not isinstance(entry, dict):
            continue
        raw_id = entry.get("i", entry.get("id", entry.get("index", position)))
        raw_text = entry.get("t", entry.get("text", entry.get("translation", "")))
        try:
            key = int(raw_id)
        except (TypeError, ValueError):
            key = position
        value = str(raw_text).strip()
        if value:
            mapping[key] = value
    if not mapping:
        raise TranslateError("model answer contained no lines")
    return mapping


def _residue_lines(mapping: dict[int, str], target: str) -> list[int]:
    """Ids whose translation still carries source-script characters.

    A ratio test cannot see a mixed line, and mixed lines are the usual
    failure: "Các khoản này是真的 hay giả", "Anh要是走了霍家可就完了？". That is
    90% Vietnamese and 100% wrong, so any CJK character in a non-CJK target
    counts as residue and the batch is asked again.
    """
    if _language_name(target) in _CJK_LANGUAGES:
        return []
    return [key for key, value in mapping.items() if _CJK_RE.search(value)]


def _script_reminder(target: str) -> str:
    """The last-resort prompt for one line that keeps its Chinese characters."""
    name = _language_name(target)
    return (
        f"\n\nRewrite this single line using {name} letters only. Every Chinese, "
        f"Japanese or Korean character must be replaced by its {name} equivalent, "
        f"names included. The answer must contain no Chinese characters at all."
    )


def _comparable(text: str) -> str:
    return re.sub(r"[\W_]+", "", text or "").casefold()


def _repeated_lines(mapping: dict[int, str], chunk: list[SubtitleSegment]) -> list[int]:
    """Ids answered with another line's text instead of their own.

    A 3B model that loses the thread answers the rest of a batch with one line
    copied over and over, or - the quieter version - merges two lines and then
    slides every following id one place, so two neighbouring ids end up with the
    same sentence. Every id is present and every line is Vietnamese, so only the
    repetition gives it away; two identical subtitles are worse than one line
    re-asked, and the system prompt already forbids it.
    """
    sources = {segment.index: _comparable(segment.text) for segment in chunk}
    groups: dict[str, list[int]] = {}
    for key, value in mapping.items():
        text = _comparable(value)
        if len(text) >= 12:
            groups.setdefault(text, []).append(key)
    repeated: list[int] = []
    for keys in groups.values():
        # One id cannot repeat another, and a source that really is repeated
        # ("小玥" twice) may honestly produce the same sentence twice.
        if len(keys) >= 2 and len({sources.get(key) for key in keys}) == len(keys):
            repeated.extend(keys)
    return sorted(repeated)


def _strip_residue(text: str) -> str:
    """Drop source-script characters from a translation, keeping what it says.

    "Đây là số chi phí是真的 hay giả?" is one Chinese fragment away from a usable
    line; the fragment is exactly what the reader cannot read, so removing it
    costs a word and saves the cue.
    """
    stripped = _CJK_RE.sub("", text or "")
    stripped = re.sub(r"\s+", " ", stripped)
    stripped = re.sub(r"\s+([,.;:!?])", r"\1", stripped)
    return stripped.strip(" ,;:-")


def _salvage(mapping: dict[int, str], chunk: list[SubtitleSegment], target: str) -> dict[int, str]:
    """Last resort for one line: keep the Latin part of a mixed answer."""
    if _language_name(target) in _CJK_LANGUAGES:
        return {}
    kept: dict[int, str] = {}
    for segment in chunk:
        value = mapping.get(segment.index)
        if not value or not _CJK_RE.search(value):
            continue
        cleaned = _strip_residue(value)
        words = re.findall(r"[^\W\d_]{2,}", cleaned)
        # "Là" left over from "Là叔林" is a consonant and a vowel, not a subtitle.
        if len(words) >= 2 or (words and len(words[0]) >= 4):
            kept[segment.index] = cleaned
    return kept


def _align_mapping(mapping: dict[int, str], chunk: list[SubtitleSegment]) -> dict[int, str]:
    """Re-key the answer onto the real segment indexes.

    Two things go wrong with small models: they renumber from 1 (because the
    "i" values are only a hint) and they echo the context lines. Re-keying by
    position when nothing matched handles the first; a partial answer is handed
    back as-is so the caller retries with a smaller batch, because half-source,
    half-translated output is exactly what "not accurate" looks like.
    """
    wanted = [segment.index for segment in chunk]
    aligned = {index: mapping[index] for index in wanted if index in mapping}
    if len(aligned) == len(wanted):
        return aligned
    ordered = [mapping[key] for key in sorted(mapping)]
    if not aligned and len(ordered) == len(wanted):
        return dict(zip(wanted, ordered, strict=True))
    return aligned


def _copied_lines(
    mapping: dict[int, str], chunk: list[SubtitleSegment], source_language: str, target_language: str
) -> list[int]:
    """Ids that came back byte-identical to the source.

    With a Latin-script source (English, Indonesian) the script guard cannot see
    a copy, yet an identical line in a Vietnamese subtitle is always a failure -
    unless it is a short name or a number, which is why short lines are left
    alone. Line by line rather than "most of the batch": one refused line in a
    batch of five used to send all five back through the model.
    """
    if _language_name(source_language) == _language_name(target_language):
        return []
    copied: list[int] = []
    for segment in chunk:
        value = mapping.get(segment.index)
        if value is None:
            continue
        source = _comparable(segment.text)
        if len(source) >= 12 and source == _comparable(value):
            copied.append(segment.index)
    return copied


# The grammar is built from this schema, so a 3B model physically cannot answer
# with the wrong shape (llama-cpp falls back to generic JSON only if the schema
# cannot be compiled).
_LINES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "t": {"type": "string"}},
                "required": ["i", "t"],
            },
        }
    },
    "required": ["lines"],
}


def _ask_local(prompt: str, settings: Settings, system: str) -> str:
    return _complete_local(prompt, settings, system, schema=_LINES_SCHEMA, max_tokens=_budget(prompt))


def _budget(prompt: str) -> int:
    """How many tokens this answer may use.

    A 3B model that starts looping ("十年前十年前十年前...") only stops at the
    cap, and a cap of 800 turns one bad line into a minute of GPU time. Sizing
    the budget from the number of lines asked for cuts that to seconds.
    """
    for line in reversed(prompt.splitlines()):
        text = line.lstrip()
        if not text.startswith("["):
            continue
        try:
            lines = len(json.loads(text))
        except ValueError:
            break
        return max(120, min(800, 90 + 70 * lines))
    return 800


def _complete_local(
    prompt: str,
    settings: Settings,
    system: str,
    *,
    schema: dict[str, Any] | None = None,
    max_tokens: int = 800,
) -> str:
    llm = _load_llm(settings)
    path = model_manager.llm_path(settings)
    response_format: dict[str, Any] = {"type": "json_object"}
    if schema is not None:
        response_format["schema"] = schema
    response = llm.create_chat_completion(
        messages=_messages(path, prompt, system),
        temperature=0.2,
        top_p=0.9,
        # Without a penalty the model repeats one fragment until max_tokens;
        # with it a stuck line comes back as a short answer that fails
        # validation instead of a long one that fails it slowly.
        repeat_penalty=1.15,
        frequency_penalty=0.2,
        # 5 short lines need ~250 tokens; a runaway model otherwise rambles for
        # a full minute repeating the same JSON blocks before hitting the cap.
        max_tokens=max_tokens,
        response_format=response_format,
    )
    choices = response.get("choices") or []
    if not choices:
        raise TranslateError("llama-cpp returned no choices")
    return (choices[0].get("message") or {}).get("content") or ""


def _gemini_thinking(types: Any, profile: GeminiProfile) -> Any:
    """``ThinkingConfig`` for the profile, or None when the model takes none.

    ``thinking_level`` only exists on the newest models: 2.5 Flash/Pro and older
    SDKs would reject it, so the knob is applied only where the profile asks for
    it and quietly skipped when the installed SDK does not have the member.
    """
    if not profile.thinking:
        return None
    levels = getattr(types, "ThinkingLevel", None)
    config_cls = getattr(types, "ThinkingConfig", None)
    if levels is None or config_cls is None:  # pragma: no cover - older SDK
        return None
    level = getattr(levels, profile.thinking.upper(), None)
    if level is None:  # pragma: no cover - a renamed enum member
        return None
    return config_cls(thinking_level=level)


def _ask_gemini(prompt: str, settings: Settings, system: str) -> str:
    key = api_key("gemini", settings)
    if not key:
        raise TranslateError("GEMINI_API_KEY is not configured")
    try:
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise TranslateError("google-genai is not installed (pip install google-genai)") from exc

    profile = gemini_profile(settings.gemini_model)
    options: dict[str, Any] = {
        "system_instruction": system,
        "temperature": 0.2,
        "response_mime_type": "application/json",
    }
    thinking = _gemini_thinking(types, profile)
    if thinking is not None:
        options["thinking_config"] = thinking

    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(base_url=settings.gemini_base_url or None),
    )
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(**options),
    )
    return getattr(response, "text", "") or ""


def _ask_openai(prompt: str, settings: Settings, system: str) -> str:
    key = api_key("openai", settings)
    if not key:
        raise TranslateError("OPENAI_API_KEY is not configured")
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise TranslateError("openai is not installed (pip install openai)") from exc

    client = OpenAI(api_key=key, base_url=settings.openai_base_url or None)
    response = client.chat.completions.create(
        model=settings.openai_chat_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    if not response.choices:
        raise TranslateError("OpenAI returned no choices")
    return response.choices[0].message.content or ""


_ASKERS = {
    "local": _ask_local,
    "gemini": _ask_gemini,
    "openai": _ask_openai,
}

# Sleep in slices so a cancel during a 4 s wait is felt in half a second, not
# after the next (rate-limited) request has already been sent.
_PAUSE_SLICE = 0.5


class _Pacer:
    """Keeps consecutive provider calls at least ``pause`` seconds apart.

    A free-tier key is limited per minute, and the repair path re-asks a batch
    milliseconds after the batch it repairs. The clock starts when the previous
    answer *arrived*, so a slow request already spent the gap and the pause is
    only the quiet time the API actually asks for. ``report(emit=True)`` doubles
    as the cancellation checkpoint, since a job cancelled during the wait has no
    other chance to stop before the next request.
    """

    def __init__(self, pause: float, report: Reporter) -> None:
        self.pause = pause
        self.report = report
        self.last: float | None = None  # when the previous answer arrived

    def wait(self) -> None:
        self.report(emit=True)  # raises Cancelled when the user stopped the job
        if self.last is None:
            return
        end = self.last + self.pause
        while (remaining := end - time.monotonic()) > 0:
            time.sleep(min(remaining, _PAUSE_SLICE))
            self.report(emit=True)  # a 4 s wait must still stop on cancel

    def mark(self) -> None:
        self.last = time.monotonic()


def _paced(
    ask: Callable[[str, Settings, str], str], pacer: _Pacer | None
) -> Callable[[str, Settings, str], str]:
    """Wrap an asker so every request keeps the pause - retries included."""
    if pacer is None:
        return ask

    def paced(prompt: str, settings: Settings, system: str) -> str:
        pacer.wait()
        try:
            return ask(prompt, settings, system)
        finally:
            # Marked even on failure: a retry is a request like any other.
            pacer.mark()

    return paced


# Sentence engines, not chat models: same service the browser page calls, no key.
_WEB_PROVIDERS = {"google": "translate.google.com", "bing": "bing.com/translator"}
# Big enough to amortise the per-request cost, small enough that one failure
# does not throw the whole clip away.
_WEB_BATCH = 40


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def _problems(
    mapping: dict[int, str],
    chunk: list[SubtitleSegment],
    *,
    source_language: str,
    target_language: str,
) -> dict[int, str]:
    """Ids that cannot be shown as they are, with the reason.

    Knowing *which* lines are wrong is what makes a partial answer usable: a
    batch of ten usually loses one or two, and halving the batch throws the
    other eight away with them.
    """
    problems = {
        segment.index: "answer missed the line"
        for segment in chunk
        if segment.index not in mapping
    }
    problems |= {
        index: "kept source-script characters"
        for index in _residue_lines(mapping, target_language)
    }
    problems |= {
        index: "repeated another line's text" for index in _repeated_lines(mapping, chunk)
    }
    problems |= {
        index: "copied the source text"
        for index in _copied_lines(mapping, chunk, source_language, target_language)
    }
    return problems


def _translate_chunk(
    chunk: list[SubtitleSegment],
    *,
    ask: Callable[[str, Settings, str], str],
    settings: Settings,
    system: str,
    source_language: str,
    target_language: str,
    style: str,
) -> dict[int, str]:
    """Translate one batch, re-asking only the lines that came back wrong.

    A 3B model that copies the source for five lines often behaves on two, so a
    batch that fails is repaired line by line and only halved when nothing in it
    survived - splitting everything up front is what turns a batch into ten
    one-line prompts the model answers from thin air.

    No previous batch is shown to the model. The rolling "here are the lines you
    already translated" block was the single most damaging idea here: with it in
    the prompt, half of a batch came back as the *previous* batch's answers
    ("Anh phải tìm anh ngay lập tức" for a line that says "please stay").
    """
    options = {
        "ask": ask,
        "settings": settings,
        "system": system,
        "source_language": source_language,
        "target_language": target_language,
        "style": style,
    }
    prompt = _user_prompt(chunk, source_language, target_language, style)
    # One shot per batch: a 3B model that copies the source for five lines will
    # copy it again, so the escalation is a smaller question, not a louder one.
    # A lone line is the opposite case - it has no smaller form, so it is asked
    # again, and a third time with the script spelled out.
    reminder = (
        f"\n\nAnswer again, in {_language_name(target_language)} only: translate "
        "every line, never copy the source text."
    )
    attempts = [prompt]
    if len(chunk) == 1:
        attempts.append(prompt + reminder)
        if _source_script(chunk[0].text) is not None:
            attempts.append(prompt + _script_reminder(target_language))

    last: dict[int, str] = {}
    for attempt_prompt in attempts:
        raw = ""
        try:
            raw = ask(attempt_prompt, settings, system)
            mapping = _align_mapping(_parse_lines(raw), chunk)
            last = mapping
            problems = _problems(
                mapping,
                chunk,
                source_language=source_language,
                target_language=target_language,
            )
            if not problems:
                return mapping
            if len(problems) < len(chunk):
                good = {key: value for key, value in mapping.items() if key not in problems}
                left = [segment for segment in chunk if segment.index in problems]
                log.info(
                    "batch of %s: re-asking %s of them, keeping %s",
                    len(chunk),
                    len(left),
                    len(good),
                )
                return {**good, **_translate_chunk(left, **options)}
            reasons = ", ".join(sorted(set(problems.values())))
            raise TranslateError(f"{len(problems)} of {len(chunk)} lines wrong ({reasons})")
        except Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - provider or parse failure
            # Keep the answer in the log: the reason is only visible here.
            log.warning(
                "batch of %s lines failed: %s | answer=%r",
                len(chunk),
                exc,
                raw[:400],
            )

    # Every attempt kept a Chinese word inside the line: drop the word rather
    # than show the reader a character they cannot read.
    kept = _salvage(last, chunk, target_language)
    if kept:
        log.warning("salvaged %s line(s) by dropping source-script fragments: %s", len(kept), sorted(kept))
        return kept

    if len(chunk) > 1:
        middle = len(chunk) // 2
        log.info(
            "batch of %s lines failed, splitting %s + %s",
            len(chunk),
            middle,
            len(chunk) - middle,
        )
        left = _translate_chunk(chunk[:middle], **options)
        right = _translate_chunk(chunk[middle:], **options)
        return {**left, **right}
    return {}


def _translate_web(
    segments: list[SubtitleSegment],
    *,
    provider: str,
    source_language: str,
    target_language: str,
    report: Reporter,
) -> TranslationOutcome:
    """Translate through a free web engine, bypassing the prompt machinery.

    Google and Bing are sentence translators, not chat models: there is no
    system prompt to obey, no JSON contract to keep and no id to slide. The
    guards that protect the 3B model (id alignment, repeated lines, salvage)
    would only add noise here. The script check is still worth doing - an
    engine that answers a Chinese line with Chinese is broken on every
    provider - and a mixed answer loses only the characters nobody can read.
    """
    model_name = _WEB_PROVIDERS[provider]
    batches = [segments[position : position + _WEB_BATCH] for position in range(0, len(segments), _WEB_BATCH)]
    translated: list[SubtitleSegment] = []
    failed = 0

    report(stage="translate", fraction=0.0, message=f"{len(segments)} lines via {model_name}")

    for position, batch in enumerate(batches, start=1):
        if position > 1:
            report(emit=True)  # cheap cancellation checkpoint
        try:
            answers = web_translate.translate_texts(
                [segment.text for segment in batch],
                provider=provider,
                source=source_language,
                target=target_language,
            )
        except web_translate.WebTranslateError as exc:
            log.warning("%s batch %s failed: %s", provider, position, exc)
            answers = []

        if len(answers) != len(batch):
            failed += 1
            log.warning("%s batch %s returned %s of %s lines", provider, position, len(answers), len(batch))
            answers = []

        kept = 0
        for index, segment in enumerate(batch):
            copy = segment.model_copy()
            value = (answers[index] if index < len(answers) else "").strip()
            if value and _language_name(target_language) not in _CJK_LANGUAGES and _CJK_RE.search(value):
                value = _strip_residue(value)
            if not value:
                kept += 1
                value = segment.text
            copy.translation = value
            translated.append(copy)

        if kept:
            report(
                stage="translate",
                fraction=position / len(batches),
                message=f"batch {position}: {kept} line(s) kept the source text",
            )
        report(
            fraction=position / len(batches),
            message=f"batch {position}/{len(batches)} translated",
            segment=translated[-1] if translated else None,
        )

    if failed == len(batches):
        raise TranslateError(f"{model_name} did not answer - check the internet connection")

    report(stage="translate", fraction=1.0, message=f"{len(translated)} lines in {target_language}")
    log.info(
        "translated %s lines to %s via %s (%s failed batches)",
        len(translated),
        target_language,
        provider,
        failed,
    )
    return TranslationOutcome(provider=provider, model=model_name, segments=translated, failed_chunks=failed)


def translate(
    segments: list[SubtitleSegment],
    *,
    target_language: str = "vi",
    source_language: str = "auto",
    provider: str | None = None,
    style: str = "short-form video subtitle",
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> TranslationOutcome:
    settings = provider_config.overlay(settings or get_settings())
    if not segments:
        raise TranslateError("No subtitle lines to translate")

    chosen = (provider or settings.translate_provider or "local").lower()
    if chosen in _WEB_PROVIDERS:
        return _translate_web(
            segments,
            provider=chosen,
            source_language=source_language,
            target_language=target_language,
            report=report,
        )
    if chosen not in _ASKERS:
        raise TranslateError(f"Unsupported translation provider: {chosen}")

    ask = _ASKERS[chosen]
    model_name = {
        "local": settings.llm_model_file,
        "gemini": settings.gemini_model,
        "openai": settings.openai_chat_model,
    }[chosen]

    # Fail once, up-front, when the weights are missing: retrying 2x per batch
    # would just log the same configuration error a dozen times.
    if chosen == "local" and model_manager.llm_path(settings) is None:
        raise _not_installed(settings)

    # The LLM needs the VRAM the whisper model was holding.
    if chosen == "local":
        stt_service.unload()

    # Gemini is billed per *request*, and a free-tier key is limited per minute:
    # the model decides how many lines fit in one call and how long to breathe.
    # The local model keeps its five-line compromise (see MAX_LINES_PER_CHUNK).
    profile = gemini_profile(model_name) if chosen == "gemini" else None
    if profile is not None:
        ask = _paced(ask, _Pacer(profile.pause, report))

    chunks = _chunk(
        segments,
        max_lines=profile.lines if profile else MAX_LINES_PER_CHUNK,
        max_chars=profile.chars if profile else MAX_CHARS_PER_CHUNK,
    )
    system = _system_prompt(target_language, source_language, style)
    translated: list[SubtitleSegment] = []
    failed_chunks = 0

    tuning = ""
    if profile is not None:
        tuning = f" · {profile.lines} lines/request · thinking {profile.thinking or 'default'}"
    report(stage="translate", fraction=0.0, message=f"{len(chunks)} batches via {chosen}/{model_name}{tuning}")

    for position, chunk in enumerate(chunks, start=1):
        if position > 1:
            report(emit=True)  # cheap cancellation checkpoint
        mapping = _translate_chunk(
            chunk,
            ask=ask,
            settings=settings,
            system=system,
            source_language=source_language,
            target_language=target_language,
            style=style,
        )

        if not mapping:
            failed_chunks += 1
            report(
                stage="translate",
                fraction=position / len(chunks),
                message=f"batch {position} kept the source text",
            )
            translated.extend(segment.model_copy() for segment in chunk)
            continue

        kept = [segment.index for segment in chunk if segment.index not in mapping]
        for segment in chunk:
            copy = segment.model_copy()
            copy.translation = mapping.get(segment.index) or segment.text
            translated.append(copy)
        if kept:
            report(
                stage="translate",
                fraction=position / len(chunks),
                message=f"batch {position}: {len(kept)} line(s) kept the source text",
            )
        report(
            fraction=position / len(chunks),
            message=f"batch {position}/{len(chunks)} translated",
            segment=translated[-1],
        )

    if failed_chunks == len(chunks):
        raise TranslateError("Translation failed for every batch - check the provider configuration")

    report(stage="translate", fraction=1.0, message=f"{len(translated)} lines in {target_language}")
    log.info("translated %s lines to %s via %s (%s failed batches)", len(translated), target_language, chosen, failed_chunks)
    return TranslationOutcome(
        provider=chosen,
        model=model_name,
        segments=translated,
        failed_chunks=failed_chunks,
    )


# ---------------------------------------------------------------------------
# Dubbing timing rescue
# ---------------------------------------------------------------------------
_SHORTEN_SYSTEM = (
    "You edit dubbing lines for a vertical video. Rewrite the line so it can be "
    "spoken inside its time slot: keep the meaning, the intent and the tone, "
    "delete filler, repetition and redundant words, never add information and "
    "never explain. Answer with STRICT JSON only: {\"t\":\"<shortened line>\"}"
)

_SHORTEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"t": {"type": "string"}},
    "required": ["t"],
}


def _parse_shortened(raw: str) -> str:
    match = _JSON_OBJECT_RE.search(raw or "")
    if not match:
        return ""
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    value = data.get("t") or data.get("text") or data.get("translation") or ""
    return str(value).strip()


def shorten_for_timing(
    text: str,
    *,
    max_chars: int,
    provider: str | None = None,
    language: str = "vi",
    settings: Settings | None = None,
) -> str:
    """Rewrite one dubbing line so it fits ``max_chars`` characters.

    The renderer can stretch audio with atempo, but a line that has to be sped
    up by 1.35x sounds like a chipmunk. Asking the model for a shorter rewrite
    first (CapCap's "timing rescue") keeps the voice natural, so the return value
    is always shorter than the input - or the input itself when nothing better
    came back, because a failed rewrite must never block the render.
    """
    text = (text or "").strip()
    if len(text) <= max_chars or max_chars <= 0:
        return text

    settings = settings or get_settings()
    chosen = (provider or settings.translate_provider or "local").lower()
    prompt = (
        f"Language: {_language_name(language)}\n"
        f"Character limit: {max_chars}\n"
        f"Line: {text}"
    )
    try:
        ask = _ASKERS.get(chosen)
        # "local" and the web translators (Google/Bing only translate, they
        # cannot rephrase) both go through the local model.
        if chosen == "local" or ask is None:
            if model_manager.llm_path(settings) is None:
                return text
            raw = _complete_local(
                prompt, settings, _SHORTEN_SYSTEM, schema=_SHORTEN_SCHEMA, max_tokens=200
            )
        else:
            raw = ask(prompt, settings, _SHORTEN_SYSTEM)
    except Cancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - a rewrite is best effort
        log.warning("timing rewrite failed for %r: %s", text[:60], exc)
        return text

    candidate = _parse_shortened(raw)
    # Longer or identical means the model paraphrased instead of shortening;
    # that would only make the clamping worse.
    return candidate if 0 < len(candidate) < len(text) else text


__all__ = ["TranslateError", "TranslationOutcome", "shorten_for_timing", "translate", "unload"]
