"""Turn raw recognition output into cues a viewer can actually read.

Whisper answers "what was said"; a subtitle also has to answer "what fits on
screen in the time this line is up". The gap between the two is what makes a
first draft *look* wrong even when every word is correct:

* one cue covering fourteen seconds of speech,
* two cues that overlap, so libass stacks them on top of each other,
* the same sentence repeated ten times over an instrumental,
* "thanks for watching" invented over silence.

Every rule here is deterministic and provider independent: it runs on the local
Whisper output and on the Groq/OpenAI transcripts alike. The budget numbers are
borrowed from the subtitle pass of `CapCap` (``SegmentRegroupService`` and the
optimizer in ``translation/orchestrator.py``), which is tuned for 9:16 short
form: two lines of about twenty characters, seven seconds at most.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher

MAX_CHARS = 42           # two lines of 21 characters
MAX_DURATION = 7.0       # longer than this and the eye loses the line
MIN_DURATION = 0.7       # shorter than this and nothing can be read
MIN_GAP = 0.04           # libass never draws two cues at the same instant
MERGE_GAP = 0.18         # a pause this short is a breath, not a sentence end
MAX_PIECES = 4           # a single whisper cue never needs more
# A cue that runs a tenth over the budget is invisible to the viewer, while
# cutting it lands the cut wherever the clock says - inside a word, in Chinese
# ("安" / "然无恙"). Widen the limit before splitting.
SPLIT_SLACK = 1.15

_TERMINAL = (".", "!", "?", "…", "。", "！", "？")
# Recognition loops over music and trailing credits. Dropped only when Whisper
# itself says the stretch was not speech (``no_speech_prob``), never on text
# alone: "cảm ơn các bạn đã xem" is a real sign-off at the end of a vlog.
_BOILERPLATE = (
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "subscribe to my channel",
    "like and subscribe",
    "cảm ơn các bạn đã xem",
    "cảm ơn bạn đã xem",
    "nhớ đăng ký kênh",
    "đăng ký kênh",
    "字幕由",
    "字幕提供",
    "ご視聴ありがとうございました",
    "amara.org",
    "sous-titres",
    "untertitel",
)

# Text nobody ever says aloud: the sign-off whisper invents over end-card music,
# usually in traditional characters. Dropped whatever ``no_speech_prob`` says,
# because the model is confident about this one - 0.12 on a line asking the
# viewer to like and subscribe.
_NEVER_SPOKEN = (
    "請不吝點贊",
    "點贊訂閱",
    "訂閱轉發",
    "打賞支持",
    "字幕志愿者",
    "明鏡與點點",
    "請訂閱",
    "訂閱頻道",
)

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
_SPACE_RE = re.compile(r"\s+")
# Whisper separates Chinese words with spaces ("你当妈妈 你当爸爸"); a subtitle
# in CJK has no such space, and keeping it also split the translator's input.
_CJK_SPACE_RE = re.compile(
    r"(?<=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af])"
    r"\s+"
    r"(?=[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af])"
)
# Brackets are used by every ASR for non-speech sounds: [music], (laughs), *sigh*.
_BRACKETED_RE = re.compile(r"[\[\(\*（【][^\]\)\*）】]*[\]\)\*）】]")


@dataclass
class Cue:
    """One recognised line plus the word timings that produced it."""

    start: float
    end: float
    text: str
    words: list[tuple[float, float, str]] = field(default_factory=list)
    no_speech_prob: float | None = None

    @property
    def duration(self) -> float:
        return max(self.end - self.start, 0.0)


def normalize_text(text: str) -> str:
    """Collapse whitespace and newlines the way a subtitle renderer will."""
    collapsed = _SPACE_RE.sub(" ", str(text or "").replace("\n", " ")).strip()
    return _CJK_SPACE_RE.sub("", collapsed)


def _is_unspaced_cjk(text: str) -> bool:
    """True for a line written in CJK with no spaces, i.e. already clause-sized."""
    return " " not in text.strip() and bool(_CJK_RE.search(text))


def _has_content(text: str) -> bool:
    """False for cues that only carry music notes, dashes or bracketed sounds."""
    stripped = _BRACKETED_RE.sub("", text)
    return any(character.isalnum() for character in stripped)


def _is_boilerplate(cue: Cue) -> bool:
    lowered = cue.text.lower()
    if any(marker in lowered for marker in _NEVER_SPOKEN):
        return True
    if cue.no_speech_prob is None or cue.no_speech_prob < 0.4:
        return False
    return any(marker in lowered for marker in _BOILERPLATE)


def _join_text(left: str, right: str) -> str:
    """Join two fragments, without a space where CJK does not want one."""
    if not left:
        return right
    if not right:
        return left
    if _CJK_RE.match(left[-1]) or _CJK_RE.match(right[0]):
        return f"{left}{right}"
    return f"{left} {right}"


def _join_tokens(tokens: list[str]) -> str:
    """Join word tokens back into a line, CJK aware.

    Whisper reports Chinese one character at a time, so ``" ".join`` turns a
    line into "签 字 然后 跟我 完 婚" - correct words, unreadable subtitle, and
    the translator gets a mangled source on top of it.
    """
    text = ""
    for token in tokens:
        text = _join_text(text, token.strip())
    return text.strip()


def _slice(items: list, cuts: list[int]) -> list[list]:
    groups: list[list] = []
    previous = 0
    for cut in cuts:
        groups.append(items[previous:cut])
        previous = cut
    groups.append(items[previous:])
    return [group for group in groups if group]


def _weighted_cuts(weights: list[float], pieces: int) -> list[int]:
    """Indexes where a token list must be cut so the pieces carry equal weight.

    Equal weight is what makes a split subtitle look deliberate. Greedily
    filling up to a character budget instead produces "22 characters" followed
    by one orphan word, which is the shape that reads as a mistake.
    """
    total = sum(weights)
    if pieces <= 1 or total <= 0 or len(weights) < pieces:
        return []
    cuts: list[int] = []
    running = 0.0
    target = 1
    for position, weight in enumerate(weights):
        running += weight
        while target < pieces and running >= total * target / pieces:
            cut = position + 1
            if cut < len(weights) and (not cuts or cuts[-1] != cut):
                cuts.append(cut)
            target += 1
    return cuts


def _split_by_words(cue: Cue, pieces: int) -> list[Cue]:
    """Split at word boundaries using the timings Whisper already produced."""
    words = [(start, end, token.strip()) for start, end, token in cue.words if token.strip()]
    if len(words) < 2:
        return []
    cuts = _weighted_cuts([len(token) + 1 for _, _, token in words], pieces)
    if not cuts:
        return []

    groups = _slice(words, cuts)
    split_cues: list[Cue] = []
    for position, group in enumerate(groups):
        start = cue.start if position == 0 else max(group[0][0], split_cues[-1].end)
        end = cue.end if position == len(groups) - 1 else max(group[-1][1], start + 0.2)
        text = _join_tokens([token for _, _, token in group])
        if not text:
            continue
        split_cues.append(
            Cue(
                start=round(start, 3),
                end=round(max(end, start + 0.2), 3),
                text=text,
                words=group,
                no_speech_prob=cue.no_speech_prob,
            )
        )
    return split_cues if len(split_cues) > 1 else []


_PUNCTUATION = "，。！？；：、,.!?;:…—"


def _split_blind(cue: Cue, pieces: int) -> list[Cue]:
    """Cut text that offers no boundary to cut on.

    Chinese, Japanese and Thai arrive as one long token with no spaces, so a
    proportional split can never fire on them and a fifteen second cue used to
    survive untouched. A hard cut near punctuation is far better than that; the
    same path rescues a single runaway token in any language.
    """
    text = cue.text.replace("\n", " ")
    if len(text) < 2:
        return []

    budget = max(4, math.ceil(len(text) / max(pieces, 1)))
    chunks: list[str] = []
    index = 0
    while index < len(text) and len(chunks) < MAX_PIECES:
        window = text[index : index + budget]
        consumed = max(len(window), 1)
        if len(window) == budget and index + consumed < len(text):
            cut = max((window.rfind(mark) for mark in _PUNCTUATION), default=-1)
            if cut >= max(2, budget // 2):
                window = window[: cut + 1]
        stripped = window.strip()
        if stripped:
            chunks.append(stripped)
        index += consumed
    if len(chunks) < 2:
        return []

    duration = cue.duration
    total_chars = len(text)
    cursor = cue.start
    split_cues: list[Cue] = []
    for position, chunk in enumerate(chunks):
        share = duration * len(chunk) / total_chars if total_chars else 0.0
        end = cue.end if position == len(chunks) - 1 else min(cue.end, cursor + max(share, 0.25))
        split_cues.append(
            Cue(
                start=round(cursor, 3),
                end=round(max(end, cursor + 0.2), 3),
                text=chunk,
                no_speech_prob=cue.no_speech_prob,
            )
        )
        cursor = split_cues[-1].end
    return split_cues


def _split_by_chars(cue: Cue, pieces: int) -> list[Cue]:
    """Split proportionally when the provider returned no word timings."""
    tokens = cue.text.split()
    if len(tokens) < 2:
        return _split_blind(cue, pieces)

    cuts = _weighted_cuts([len(token) + 1 for token in tokens], pieces)
    if not cuts:
        return []

    groups = _slice(tokens, cuts)
    weights = [sum(len(token) + 1 for token in group) for group in groups]
    total_weight = sum(weights) or 1
    duration = cue.duration
    cursor = cue.start
    split_cues: list[Cue] = []
    for position, group in enumerate(groups):
        share = duration * weights[position] / total_weight
        end = cue.end if position == len(groups) - 1 else min(cue.end, cursor + max(share, 0.3))
        split_cues.append(
            Cue(
                start=round(cursor, 3),
                end=round(max(end, cursor + 0.2), 3),
                text=_join_tokens(group),
                no_speech_prob=cue.no_speech_prob,
            )
        )
        cursor = split_cues[-1].end
    return split_cues if len(split_cues) > 1 else []


def _split(cue: Cue, *, max_chars: int, max_duration: float) -> list[Cue]:
    """Break a cue that is too long or too dense to be read in one go."""
    text = cue.text
    limit = max_duration * SPLIT_SLACK if max_duration else 0.0
    over_chars = len(text) > max_chars
    over_time = cue.duration > limit
    if not over_chars and not over_time:
        return [cue]

    wanted = max(
        math.ceil(len(text) / max_chars) if max_chars else 1,
        math.ceil(cue.duration / limit) if limit else 1,
        2,
    )
    pieces = min(MAX_PIECES, wanted)
    if pieces <= 1:
        return [cue]
    if cue.words:
        by_words = _split_by_words(cue, pieces)
        if by_words:
            return by_words
    return _split_by_chars(cue, pieces) or [cue]


def _clean(cues: list[Cue]) -> list[Cue]:
    cleaned: list[Cue] = []
    for cue in cues:
        text = normalize_text(cue.text)
        if not text or not _has_content(text):
            continue
        candidate = replace(cue, text=text, start=max(float(cue.start), 0.0), end=max(float(cue.end), 0.0))
        if candidate.end <= candidate.start:
            candidate.end = candidate.start + 0.2
        if _is_boilerplate(candidate):
            continue
        cleaned.append(candidate)
    cleaned.sort(key=lambda item: (item.start, item.end))
    return cleaned


def _drop_loops(cues: list[Cue], *, repeat_threshold: int = 3) -> list[Cue]:
    """Collapse the "same sentence forever" signature of a music-only stretch."""
    kept: list[Cue] = []
    run_text = ""
    run_count = 0
    for cue in cues:
        if cue.text == run_text:
            run_count += 1
        else:
            run_text, run_count = cue.text, 1
        if run_count < repeat_threshold:
            kept.append(cue)
    return kept


def _norm_for_dedup(text: str) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _is_informative_containment(left_text: str, right_text: str) -> bool:
    """Ported from CapCap AsrMergeService._is_informative_containment."""
    if not left_text or not right_text or left_text == right_text:
        return False
    short_text, long_text = sorted((left_text, right_text), key=len)
    short_words = short_text.split()
    long_words = long_text.split()
    informative_chars = sum(1 for char in short_text if char.isalnum())
    if informative_chars >= 10 and len(short_words) >= 3:
        width = len(short_words)
        if any(long_words[i : i + width] == short_words for i in range(len(long_words) - width + 1)):
            return True

    def _is_han(character: str) -> bool:
        cp = ord(character)
        return 0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF or 0x20000 <= cp <= 0x2FA1F

    han_count = sum(1 for char in short_text if _is_han(char))
    return han_count >= 8 and short_text in long_text


def dedup_cues(
    cues: list[Cue],
    *,
    dedup_enabled: bool = True,
    dedup_similarity: float = 0.82,
) -> list[Cue]:
    """Remove duplicate wording where overlapping/adjacent audio sections join (ported from CapCap).

    Keeps the fuller sentence when one cue is a clipped prefix/suffix of the
    adjacent cue and their timings overlap or touch closely.
    """
    if not dedup_enabled or len(cues) < 2:
        return list(cues)

    threshold = min(max(float(dedup_similarity), 0.5), 1.0)
    result: list[Cue] = []
    for cue in cues:
        if not result:
            result.append(cue)
            continue
        prev = result[-1]
        prev_norm = _norm_for_dedup(prev.text)
        curr_norm = _norm_for_dedup(cue.text)
        if not prev_norm or not curr_norm:
            result.append(cue)
            continue

        overlap = min(prev.end, cue.end) - max(prev.start, cue.start)
        gap = cue.start - prev.end
        shorter_dur = max(0.05, min(prev.duration, cue.duration))
        sim = SequenceMatcher(None, prev_norm, curr_norm).ratio()

        is_containment = prev_norm != curr_norm and (prev_norm in curr_norm or curr_norm in prev_norm)
        informative = (
            is_containment
            and _is_informative_containment(prev_norm, curr_norm)
            and gap <= 0.35
            and threshold < 1.0
        )
        substantial_overlap = overlap >= max(0.10, shorter_dur * 0.60)
        is_duplicate = informative if is_containment else (substantial_overlap and sim >= threshold)

        if is_duplicate:
            if len(curr_norm) > len(prev_norm) or (len(curr_norm) == len(prev_norm) and cue.duration > prev.duration):
                result[-1] = replace(cue, start=min(prev.start, cue.start), end=max(prev.end, cue.end))
            else:
                result[-1] = replace(prev, end=max(prev.end, cue.end))
            continue

        result.append(cue)
    return result


def _can_merge(left: Cue, right: Cue, max_chars: int) -> bool:
    if right.start - left.end > MERGE_GAP or right.start < left.start:
        return False
    if left.text.rstrip().endswith(_TERMINAL):
        return False  # a finished sentence is a deliberate boundary
    # Chinese and Japanese come out of whisper already clause by clause, with no
    # punctuation to replace the space a Latin script relies on. Gluing two of
    # them gives "我们就能结婚了结婚啦你当妈妈你当爸爸十年之后你必须把我娶回家":
    # one unreadable cue, and a run-on the translator cannot punctuate.
    if _is_unspaced_cjk(left.text) and _is_unspaced_cjk(right.text):
        return False
    joined = _join_text(left.text, right.text)
    if len(joined) > max_chars:
        return False
    return left.duration <= MAX_DURATION


def _merge(cues: list[Cue], *, max_chars: int) -> list[Cue]:
    """Glue fragments the recogniser cut on a breath ("Đây là" / "hoa")."""
    merged: list[Cue] = []
    for cue in cues:
        if merged and _can_merge(merged[-1], cue, max_chars):
            previous = merged[-1]
            previous.text = _join_text(previous.text, cue.text)
            previous.end = max(previous.end, cue.end)
            previous.words = [*previous.words, *cue.words]
            continue
        merged.append(replace(cue))
    return merged


def _fit(cues: list[Cue], *, min_duration: float, max_duration: float) -> list[Cue]:
    """Make the timeline monotonic and give every line a readable on-screen time."""
    fitted: list[Cue] = []
    for position, cue in enumerate(cues):
        start = max(cue.start, 0.0)
        if fitted and start < fitted[-1].end + MIN_GAP:
            start = fitted[-1].end + MIN_GAP
        end = max(cue.end, start + min_duration)
        end = min(end, start + max_duration * SPLIT_SLACK)
        next_start = cues[position + 1].start if position + 1 < len(cues) else None
        if next_start is not None and end > next_start - MIN_GAP:
            end = max(start + 0.2, next_start - MIN_GAP)
        fitted.append(replace(cue, start=round(start, 3), end=round(max(end, start + 0.2), 3)))
    return fitted


def polish(
    cues: list[Cue],
    *,
    max_chars: int = MAX_CHARS,
    min_duration: float = MIN_DURATION,
    max_duration: float = MAX_DURATION,
    dedup_enabled: bool = True,
    dedup_similarity: float = 0.82,
) -> list[Cue]:
    """Full pass: drop noise, dedup boundary overlaps, glue fragments, split long cues, fix the clock."""
    cleaned = _clean(cues)
    if not cleaned:
        return []

    deduped = dedup_cues(_drop_loops(cleaned), dedup_enabled=dedup_enabled, dedup_similarity=dedup_similarity)
    merged = _merge(deduped, max_chars=max_chars)
    split: list[Cue] = []
    for cue in merged:
        split.extend(_split(cue, max_chars=max_chars, max_duration=max_duration))
    return _fit(split, min_duration=min_duration, max_duration=max_duration)


__all__ = ["Cue", "MIN_GAP", "dedup_cues", "normalize_text", "polish"]
