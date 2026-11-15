"""Free web translators: Google Translate and Bing Translator.

The local 3B model is strong on idiom and weak on consistency; the free web
engines are the opposite - literal, but they never invent a sentence. Both are
therefore offered as translation providers.

Neither service has a public key, so the module speaks to the same endpoints the
browser page uses:

* Google - the undocumented ``translate_a/single`` endpoint behind the web
  widget, with the mobile page as a fallback (the JSON endpoint answers 429 from
  residential IPs more often than one would like).
* Bing - ``www.bing.com/translator`` is fetched once to read the per-session
  ``IG``/``IID``/token triplet, then ``ttranslatev3`` is called with it.

One request per cue would be slow, so up to 15 cues are joined with a rare
delimiter and split again afterwards. When the service eats the delimiter the
chunk is retried one cue at a time, which is also what CapCap's providers do.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from collections.abc import Sequence

import httpx

log = logging.getLogger(__name__)

DELIMITER = "\n===@@@===\n"
# Markers that survive even when the engine rewrites the ``===`` padding.
DELIMITER_MARK = "@@@"

GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
GOOGLE_MOBILE = "https://translate.google.com/m"
GOOGLE_CLIENTS = ("dict-chrome-ex", "at", "it", "gtx")
GOOGLE_MAX_CUES = 15
GOOGLE_MAX_CHARS = 1800
GOOGLE_MAX_CUE_CHARS = 2048

BING_HOME = "https://www.bing.com/translator"
BING_MAX_CUES = 12
BING_MAX_CHARS = 1000
BING_SESSION_TTL = 300.0
# Bing wants the script, not the ISO code, for Chinese.
BING_LANG = {
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-hans": "zh-Hans",
    "zh-tw": "zh-Hant",
    "zh-hant": "zh-Hant",
    "auto": "auto-detect",
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
_RESULT_DIV_RE = re.compile(r'<div[^>]*class="[^"]*result-container[^"]*"[^>]*>(.*?)</div>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_PADDING_RE = re.compile(r"^[\s=]+|[\s=]+$")


class WebTranslateError(RuntimeError):
    """Raised when a free web engine cannot be used."""


def _language(code: str, *, bing: bool) -> str:
    value = (code or "").strip()
    if not value:
        return "auto-detect" if bing else "auto"
    if bing:
        return BING_LANG.get(value.lower(), value)
    return value


def plan_chunks(texts: Sequence[str], *, provider: str) -> list[list[str]]:
    """Group cue texts so one request stays small enough to be answered."""
    max_cues, max_chars = (
        (BING_MAX_CUES, BING_MAX_CHARS) if provider == "bing" else (GOOGLE_MAX_CUES, GOOGLE_MAX_CHARS)
    )
    chunks: list[list[str]] = []
    current: list[str] = []
    length = 0
    for text in texts:
        size = len(text or "")
        if current and (len(current) >= max_cues or length + size > max_chars):
            chunks.append(current)
            current, length = [], 0
        current.append(text or "")
        length += size
    if current:
        chunks.append(current)
    return chunks


def split_answer(text: str, expected: int) -> list[str] | None:
    """Split a batched answer back into cues, or ``None`` if it cannot be done."""
    if DELIMITER_MARK not in (text or ""):
        return None
    parts = [_PADDING_RE.sub("", piece) for piece in text.split(DELIMITER_MARK)]
    if len(parts) != expected:
        return None
    return parts


def google_text(payload: object) -> str:
    """Read the translation out of Google's ``translate_a/single`` answer.

    The shape is a list of "sentence" entries, each ``[translated, source, ...]``;
    only the first element of every entry is text we can use.
    """
    if not isinstance(payload, list) or not payload:
        return ""
    sentences = payload[0]
    if not isinstance(sentences, list):
        return ""
    parts = [
        item[0]
        for item in sentences
        if isinstance(item, list) and item and isinstance(item[0], str)
    ]
    return "".join(parts).strip()


def google_mobile_text(page: str) -> str:
    """Read the translation out of Google's mobile result page."""
    match = _RESULT_DIV_RE.search(page or "")
    if not match:
        return ""
    return html.unescape(_TAG_RE.sub("", match.group(1))).strip()


# ---------------------------------------------------------------------------
# Bing session tokens
# ---------------------------------------------------------------------------
_BING_LOCK = threading.Lock()
_BING_SESSION: tuple[str, str, str, str, str] | None = None
_BING_STAMP = 0.0


def reset_bing_session() -> None:
    """Drop the cached Bing tokens (tests and expired sessions)."""
    global _BING_SESSION, _BING_STAMP
    with _BING_LOCK:
        _BING_SESSION = None
        _BING_STAMP = 0.0


def _bing_session(client: httpx.Client, timeout: float) -> tuple[str, str, str, str, str]:
    """``(base_url, IG, IID, key, token)``, cached for a few minutes."""
    global _BING_SESSION, _BING_STAMP
    now = time.monotonic()
    with _BING_LOCK:
        if _BING_SESSION is not None and now - _BING_STAMP < BING_SESSION_TTL:
            return _BING_SESSION

    try:
        response = client.get(BING_HOME, headers=_HEADERS, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WebTranslateError(f"Bing translator is unreachable: {exc}") from exc

    page = response.text or ""
    url = str(response.url).split("?")[0]
    if not url.endswith("/"):
        url = url.rsplit("/", 1)[0] + "/"

    ig_match = re.search(r'"ig":"(.*?)"', page) or re.search(r'IG:"([A-Za-z0-9]+)"', page)
    iid_matches = re.findall(r'data-iid="(.*?)"', page)
    abuse = re.search(r'params_AbusePreventionHelper\s*=\s*\[(.*?),"(.*?)",', page)
    if not ig_match or not abuse:
        raise WebTranslateError("Bing translator page did not contain its session tokens")

    result = (
        url,
        ig_match.group(1),
        iid_matches[-1] if iid_matches else "translator.5028",
        abuse.group(1),
        abuse.group(2),
    )
    with _BING_LOCK:
        _BING_SESSION = result
        _BING_STAMP = now
    return result


# ---------------------------------------------------------------------------
# Single-string engines
# ---------------------------------------------------------------------------
def _google_one(client: httpx.Client, text: str, source: str, target: str, timeout: float) -> str:
    if not (text or "").strip():
        return ""
    params = {"sl": _language(source, bing=False), "tl": _language(target, bing=False), "dt": "t", "q": text}

    for client_name in GOOGLE_CLIENTS:
        try:
            response = client.get(GOOGLE_URL, params={**params, "client": client_name}, timeout=timeout)
        except httpx.HTTPError as exc:
            log.debug("google client %s failed: %s", client_name, exc)
            continue
        if response.status_code == 200:
            try:
                translated = google_text(response.json())
            except ValueError:
                translated = ""
            if translated:
                return translated
        elif response.status_code in {403, 429}:
            # Rate limited for this IP; another client name will not help.
            break

    return _google_mobile(client, text, source, target, timeout)


def _google_mobile(client: httpx.Client, text: str, source: str, target: str, timeout: float) -> str:
    if len(text) > GOOGLE_MAX_CUE_CHARS:
        raise WebTranslateError(
            f"Google's mobile fallback accepts at most {GOOGLE_MAX_CUE_CHARS} characters per cue"
        )
    headers = {**_HEADERS, "Accept": "text/html,application/xhtml+xml"}
    try:
        response = client.get(
            GOOGLE_MOBILE,
            params={
                "sl": _language(source, bing=False),
                "tl": _language(target, bing=False),
                "q": text,
            },
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise WebTranslateError(f"Google translate failed: {exc}") from exc

    translated = google_mobile_text(response.text)
    if not translated:
        raise WebTranslateError("Google translate returned an empty answer")
    return translated


def _bing_one(client: httpx.Client, text: str, source: str, target: str, timeout: float) -> str:
    if not (text or "").strip():
        return ""

    last_error = "Bing translate failed"
    for attempt in range(2):
        base, ig, iid, key, token = _bing_session(client, timeout)
        endpoint = f"{base}ttranslatev3?isVertical=1&IG={ig}&IID={iid}"
        headers = {
            **_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.bing.com",
            "Referer": BING_HOME,
        }
        try:
            response = client.post(
                endpoint,
                data={
                    "fromLang": _language(source, bing=True),
                    "to": _language(target, bing=True),
                    "text": text,
                    "token": token,
                    "key": key,
                },
                headers=headers,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            last_error = f"Bing translate failed: {exc}"
            if attempt == 0:
                time.sleep(1.5)
                continue
            break

        if response.status_code in {401, 403}:
            # The token triplet expired; fetch a new page and try once more.
            reset_bing_session()
            last_error = f"Bing translate rejected the session ({response.status_code})"
            if attempt == 0:
                continue
            break
        if response.status_code >= 400:
            raise WebTranslateError(f"Bing translate error {response.status_code}: {response.text[:200]}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise WebTranslateError("Bing translate returned no JSON") from exc
        translated = _bing_text(payload)
        if translated:
            return translated
        last_error = "Bing translate returned an empty answer"
        break

    raise WebTranslateError(last_error)


def _bing_text(payload: object) -> str:
    if not isinstance(payload, list) or not payload:
        return ""
    first = payload[0]
    if not isinstance(first, dict):
        return ""
    translations = first.get("translations")
    if not isinstance(translations, list) or not translations:
        return ""
    item = translations[0]
    return str(item.get("text") or "").strip() if isinstance(item, dict) else ""


def _one(client: httpx.Client, provider: str, text: str, source: str, target: str, timeout: float) -> str:
    if provider == "bing":
        return _bing_one(client, text, source, target, timeout)
    return _google_one(client, text, source, target, timeout)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def translate_texts(
    texts: Sequence[str],
    *,
    provider: str,
    source: str = "auto",
    target: str = "vi",
    timeout: float = 20.0,
) -> list[str]:
    """Translate every cue, preserving the order and the count of ``texts``."""
    chosen = (provider or "google").strip().lower()
    if chosen not in {"google", "bing"}:
        raise WebTranslateError(f"Unsupported web translator: {chosen}")

    items = [text or "" for text in texts]
    if not items:
        return []

    translated: list[str] = []
    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=timeout) as client:
        for index, chunk in enumerate(plan_chunks(items, provider=chosen)):
            if index:
                time.sleep(0.25)
            if len(chunk) == 1:
                translated.append(_one(client, chosen, chunk[0], source, target, timeout))
                continue

            parts: list[str] | None = None
            try:
                answer = _one(client, chosen, DELIMITER.join(chunk), source, target, timeout)
                parts = split_answer(answer, len(chunk))
            except (httpx.HTTPError, WebTranslateError) as exc:
                log.debug("%s batch of %s failed (%s), retrying line by line", chosen, len(chunk), exc)

            if parts is not None:
                translated.extend(parts)
                continue
            for item in chunk:
                time.sleep(0.1)
                translated.append(_one(client, chosen, item, source, target, timeout))

    return translated


__all__ = [
    "WebTranslateError",
    "google_mobile_text",
    "google_text",
    "plan_chunks",
    "reset_bing_session",
    "split_answer",
    "translate_texts",
]
