"""Bilibili BCut (必剪) cloud speech recognition, without an account.

BCut is the transcription engine behind Bilibili's own upload page and the
`bcut-asr` project. The flow is a plain three-step upload plus a poll:

1. ``resource/create`` reserves an upload id and hands back the part URLs,
2. each part is PUT to object storage and answers with an ``ETag``,
3. ``resource/create/complete`` returns a signed ``download_url`` that
   ``task`` turns into a job, and ``task/result`` eventually carries the
   utterances.

Everything is form encoded and anonymous - no cookie, no key, no signature.
The only obstacle is the WAF, which answers ``412 Precondition Failed`` when a
request does not look like the member portal, hence :data:`_HEADERS`.

Timings come back in milliseconds and are converted here, so the caller sees
the same ``(start, end, text)`` tuples as :mod:`app.services.capcut_client`.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings, get_settings
from app.core.progress import NULL_REPORTER, Reporter
from app.services import media_processor

log = logging.getLogger(__name__)

API_BASE = "https://member.bilibili.com/x/bcut/rubick-interface"
API_UPLOAD = f"{API_BASE}/resource/create"
API_COMMIT = f"{API_BASE}/resource/create/complete"
API_TASK = f"{API_BASE}/task"
API_RESULT = f"{API_BASE}/task/result"

# 7 = the general Mandarin/Vietnamese-friendly model the webpage itself uses.
MODEL_ID = 7
# Long clips are cut so a retry never means re-uploading a whole episode.
CHUNK_SECONDS = 900.0
POLL_INTERVAL = 1.5
RESULT_TIMEOUT = 300.0
UPLOAD_BITRATE = "48k"

STATE_COMPLETE = 4
STATE_ERROR = 3

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Referer": "https://member.bilibili.com/platform/upload-v2/video",
    "Origin": "https://member.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


class BcutError(RuntimeError):
    """Raised when BCut refuses a request or fails a job."""


def _data(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise BcutError(f"{label} returned no JSON (HTTP {response.status_code})") from exc
    code = payload.get("code")
    if code not in (0, "0", None):
        raise BcutError(f"{label} refused the request: {payload.get('message') or code}")
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _call(client: httpx.Client, label: str, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    try:
        response = client.request(method, url, **kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == httpx.codes.PRECONDITION_FAILED:
            raise BcutError(
                "Bilibili refused the request (HTTP 412) - the upload endpoint "
                "blocks this network. Try again or use the CapCut provider."
            ) from exc
        raise BcutError(f"{label} failed: {exc}") from exc
    except httpx.HTTPError as exc:
        raise BcutError(f"{label} failed: {exc}") from exc
    return _data(response, label)


def _chunk_to_mp3(source: Path, target: Path, start: float, duration: float) -> Path:
    """Cut one chunk and re-encode it small enough to upload quickly."""
    media_processor.run_ffmpeg(
        [
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            UPLOAD_BITRATE,
            str(target),
        ],
        timeout=300.0,
    )
    return target


def _upload(client: httpx.Client, audio: Path) -> str:
    """Push one mp3 and return the signed ``download_url`` for the task."""
    raw = audio.read_bytes()
    if not raw:
        raise BcutError(f"{audio.name} is empty")

    created = _call(
        client,
        "BCut upload request",
        "POST",
        API_UPLOAD,
        data={
            "type": 2,
            "name": audio.name,
            "size": len(raw),
            "resource_file_type": "mp3",
            "model_id": MODEL_ID,
        },
    )
    resource_id = str(created.get("resource_id") or "")
    in_boss_key = str(created.get("in_boss_key") or "")
    upload_id = str(created.get("upload_id") or "")
    urls = [str(url) for url in created.get("upload_urls") or []]
    per_size = int(created.get("per_size") or len(raw))
    if not (resource_id and in_boss_key and upload_id and urls):
        raise BcutError("BCut did not return an upload slot")

    etags: list[str] = []
    for index, url in enumerate(urls):
        part = raw[index * per_size : (index + 1) * per_size]
        try:
            response = client.put(url, content=part, timeout=300.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BcutError(f"BCut upload part {index + 1} failed: {exc}") from exc
        etag = response.headers.get("etag") or response.headers.get("ETag")
        if not etag:
            raise BcutError(f"BCut upload part {index + 1} returned no ETag")
        etags.append(etag)

    completed = _call(
        client,
        "BCut upload commit",
        "POST",
        API_COMMIT,
        data={
            "in_boss_key": in_boss_key,
            "resource_id": resource_id,
            "etags": ",".join(etags),
            "upload_id": upload_id,
            "model_id": MODEL_ID,
        },
    )
    download_url = str(completed.get("download_url") or "")
    if not download_url:
        raise BcutError("BCut did not return a download URL")
    return download_url


def _create_task(client: httpx.Client, download_url: str) -> str:
    data = _call(
        client,
        "BCut task",
        "POST",
        API_TASK,
        json={"resource": download_url, "model_id": str(MODEL_ID)},
    )
    task_id = str(data.get("task_id") or "")
    if not task_id:
        raise BcutError("BCut did not return a task id")
    return task_id


def _await_result(client: httpx.Client, task_id: str) -> list[dict[str, Any]]:
    deadline = time.monotonic() + RESULT_TIMEOUT
    while time.monotonic() < deadline:
        data = _call(
            client,
            "BCut result",
            "GET",
            API_RESULT,
            params={"model_id": MODEL_ID, "task_id": task_id},
        )
        state = data.get("state")
        if state == STATE_COMPLETE:
            import json  # noqa: PLC0415 - the result is a JSON string

            raw = data.get("result") or "{}"
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError as exc:
                raise BcutError("BCut returned an unreadable result") from exc
            utterances = parsed.get("utterances") if isinstance(parsed, dict) else None
            return list(utterances or [])
        if state == STATE_ERROR:
            raise BcutError(f"BCut job failed: {data.get('remark') or 'unknown error'}")
        time.sleep(POLL_INTERVAL)
    raise BcutError(f"BCut did not finish the transcription within {RESULT_TIMEOUT:.0f}s")


def transcribe(
    audio: str | Path,
    *,
    work_dir: str | Path,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> list[tuple[float, float, str]]:
    """Transcribe an audio file through BCut, returning ``(start, end, text)``."""
    settings = settings or get_settings()
    source = Path(audio)
    if not source.is_file():
        raise BcutError(f"Audio not found: {source}")

    try:
        duration = media_processor.probe_duration(source)
    except media_processor.MediaError as exc:
        raise BcutError(str(exc)) from exc
    if duration <= 0:
        raise BcutError("Cannot determine the audio duration")

    folder = Path(work_dir)
    folder.mkdir(parents=True, exist_ok=True)

    spans: list[tuple[float, float]] = []
    position = 0.0
    while position < duration:
        length = min(CHUNK_SECONDS, duration - position)
        spans.append((position, length))
        position += length

    report(stage="upload", fraction=0.02, message=f"Bilibili BCut: {len(spans)} chunk(s)")

    lines: list[tuple[float, float, str]] = []
    timeout = httpx.Timeout(connect=30.0, read=180.0, write=300.0, pool=30.0)
    with httpx.Client(headers=_HEADERS, follow_redirects=True, timeout=timeout) as client:
        for index, (start, length) in enumerate(spans):
            report(
                stage="upload",
                fraction=0.02 + 0.85 * index / len(spans),
                message=f"BCut chunk {index + 1}/{len(spans)}",
            )
            chunk = _chunk_to_mp3(source, folder / f"bcut_{index:03d}.mp3", start, length)
            try:
                download_url = _upload(client, chunk)
                task_id = _create_task(client, download_url)
                utterances = _await_result(client, task_id)
            finally:
                chunk.unlink(missing_ok=True)

            for item in utterances:
                text = str(item.get("transcript") or item.get("text") or "").strip()
                if not text:
                    continue
                left = start + float(item.get("start_time") or 0) / 1000.0
                right = start + float(item.get("end_time") or 0) / 1000.0
                lines.append((round(left, 3), round(max(right, left + 0.2), 3), text))

    report(stage="transcribe", fraction=0.95, message=f"BCut returned {len(lines)} line(s)")
    return lines


__all__ = ["BcutError", "transcribe"]
