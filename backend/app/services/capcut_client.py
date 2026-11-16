"""CapCut / JianYing cloud speech-to-text and text-to-speech, without a login.

CapCap talks to the same two services the desktop editor uses, and so does this
module. There is no API key involved: each request carries a short-lived set of
headers derived from a device profile (``x-ss-stub`` = md5 of the body, ``sign``
= md5 over a fixed salt, and for uploads a temporary AWS4 credential pair that
the sign endpoint hands out anonymously).

Only the two edges needed by the pipeline live here:

* :func:`transcribe` - upload a compressed chunk, submit ``audio_subtitle``,
  poll until the utterances arrive (milliseconds, like every ByteDance API).
* :func:`synthesize` - one SSML payload through ``common_task``; the answer is
  a signed URL to an mp3.

Both are best-effort by nature (they are undocumented endpoints), so callers are
expected to surface :class:`CapCutError` to the user instead of retrying blindly.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import httpx

from app.config import BASE_DIR, Settings, get_settings, resolve_ffmpeg
from app.core.progress import NULL_REPORTER, Reporter
from app.services import capcut_pc_worker

log = logging.getLogger(__name__)

STT_BASE = "https://lv-pc-api-sinfonlinec.ulikecam.com"
TTS_BASE = "https://editor-api-sg.capcutapi.com"
VOD_HOST = "vod.bytedanceapi.com"
VOD_SPACE = "lv-mac-recognition"
VOD_REGION = "sdwdmwlll"
VOD_SERVICE = "vod"

JIANYING_UA = "Cronet/TTNetVersion:7a947855 2024-05-06 QuicVersion:4bf243e0 2023-04-17"
CAPCUT_UA = "Cronet/TTNetVersion:1d7cc3b1 2025-07-16 QuicVersion:52c2b40d 2025-04-03"

# 500 s per chunk keeps every upload small and every poll short; the boundary
# risk is a single word clipped in half, which the STT answer usually repairs
# because CapCut re-segments on its own.
CHUNK_SECONDS = 500.0
STT_TIMEOUT = 180.0
TTS_TIMEOUT = 90.0
POLL_INTERVAL = 1.0

_TTS_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmTd34Lw4b7IuldSXh/zY
CMla+ITdGG5TeWz6ad+OySd4r+IrY45AoqrYUxhQ2dl+7z+i7r/5vEa8rr39BYfB
8AGMQLmZA8HmgpWBsqrn/V6daUALkKnkLb70Fn32CJigIuGXAYqxUdGuI340aC+0
v5Es3puJsHyzf01/AelE4Cdc6bZhQrASJLBh8R3BQToYClmDVSDUQk28o8sl/guA
Z4n303Vj+6Siv1HayPCdV6kpVVnMBAG4+umUbwGmn132N3fgpzLarFF3XyWmS1zh
D/J07iM/rP8GDO9IskHNHd2phrO0G6KzrcFAnTBHjVv+hCBEfzN/no3FNA9AuC36
mwIDAQAB
-----END PUBLIC KEY-----"""

# Vietnamese voices of the CapCut catalogue (type -> (label, resource id)).
# The resource id is what the TTS payload actually needs; the type is the
# human-readable half that reaches the UI and the API request.
CAPCUT_VOICES: dict[str, tuple[str, str]] = {
    "BV421_vivn_streaming": ("Nhỏ Ngọt Ngào", "7252594014782755330"),
    "vi_female_huong": ("Giọng Nữ Phổ Thông", "7264854897953083905"),
    "BV074_streaming": ("Cô Gái Hoạt Ngôn", "7102355709945188865"),
    "BV074_streaming_dsp": ("Giọng Bé", "7550087831092251920"),
    "BV562_streaming": ("Mai", "7483736254694035984"),
    "multi_female_yangguangnv_uranus_bigtts": ("Ban Mai", "7637456432522218773"),
    "BV075_streaming": ("Thanh Niên Tự Tin", "7102355803792740865"),
    "BV560_streaming": ("Alex Đại Đế", "7483736167565758992"),
}
DEFAULT_TTS_VOICE = "BV421_vivn_streaming"
_VOICE_CATALOG_CACHE: list[dict[str, Any]] | None = None


def list_capcut_voices() -> list[dict[str, Any]]:
    """Load the full CapCut voice catalog from ``resources/capcut/Voice.json`` (ported from CapCap)."""
    global _VOICE_CATALOG_CACHE
    if _VOICE_CATALOG_CACHE is not None:
        return _VOICE_CATALOG_CACHE

    candidates = [
        BASE_DIR.parent / "resources" / "capcut" / "Voice.json",
        BASE_DIR / "resources" / "capcut" / "Voice.json",
    ]
    catalog_path = next((p for p in candidates if p.is_file()), None)
    items: list[dict[str, Any]] = []
    if catalog_path:
        try:
            raw_list = json.loads(catalog_path.read_text(encoding="utf-8"))
            for item in raw_list:
                v_type = str(item.get("voice_type") or "").strip()
                d_name = str(item.get("display_name") or "").strip()
                res_id = str(item.get("resource_id") or "").strip()
                if not v_type or not d_name or not res_id.isdigit():
                    continue
                if item.get("catalog_kind") == "digital_human_voice" or "neural" in v_type.lower():
                    continue
                lan = str(item.get("lan") or "vi").split("-")[0].lower()
                if v_type not in CAPCUT_VOICES:
                    CAPCUT_VOICES[v_type] = (d_name, res_id)
                items.append(
                    {
                        "id": v_type,
                        "name": d_name,
                        "resource_id": res_id,
                        "language": lan,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not read CapCut Voice.json: %s", exc)

    if not items:
        for v_type, (d_name, res_id) in CAPCUT_VOICES.items():
            items.append({"id": v_type, "name": d_name, "resource_id": res_id, "language": "vi"})

    _VOICE_CATALOG_CACHE = items
    return items

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class CapCutError(RuntimeError):
    """Raised when a CapCut endpoint refuses a request."""


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------
def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def body_stub(body: str) -> str:
    """``x-ss-stub`` is the md5 of the exact bytes that are sent."""
    return hashlib.md5(body.encode()).hexdigest()


def trace_id() -> str:
    seed = uuid.uuid4().hex
    return f"00-{seed}-{seed[:16]}-01"


def sign_header(url: str, appvr: str, device_time: str, tdid: str, pf: str) -> str:
    path = url.split("?", 1)[0]
    material = f"9e2c|{path[-7:]}|{pf}|{appvr}|{device_time}|{tdid}|11ac"
    return hashlib.md5(material.encode()).hexdigest()


def escape_xml(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _der_value(data: bytes, position: int, tag: int) -> tuple[bytes, int]:
    if data[position] != tag:
        raise CapCutError("invalid DER structure in the CapCut TTS key")
    position += 1
    first = data[position]
    position += 1
    if first < 0x80:
        length = first
    else:
        count = first & 0x7F
        length = int.from_bytes(data[position : position + count], "big")
        position += count
    return data[position : position + length], position + length


def _der_int(data: bytes, position: int) -> tuple[int, int]:
    raw, position = _der_value(data, position, 2)
    return int.from_bytes(raw.lstrip(b"\0"), "big"), position


def rsa_public_numbers(pem: str = _TTS_PUBLIC_KEY) -> tuple[int, int]:
    raw = base64.b64decode("".join(line for line in pem.splitlines() if not line.startswith("---")))
    outer, _ = _der_value(raw, 0, 0x30)
    _algorithm, position = _der_value(outer, 0, 0x30)
    bits, _ = _der_value(outer, position, 3)
    numbers, _ = _der_value(bits[1:], 0, 0x30)
    modulus, position = _der_int(numbers, 0)
    exponent, _ = _der_int(numbers, position)
    return modulus, exponent


def rsa_encrypt_pkcs1v15(message: str, pem: str = _TTS_PUBLIC_KEY) -> str:
    """RSA PKCS#1 v1.5 encryption with the standard library only.

    ``cryptography`` is not a dependency of the engine, and pulling it in for a
    single signature would be a poor trade, so the DER key is parsed by hand.
    """
    modulus, exponent = rsa_public_numbers(pem)
    size = (modulus.bit_length() + 7) // 8
    payload = message.encode()
    padding = bytearray()
    while len(padding) < size - len(payload) - 3:
        padding.extend(byte for byte in secrets.token_bytes(size) if byte)
    encoded = b"\0\2" + bytes(padding[: size - len(payload) - 3]) + b"\0" + payload
    return base64.b64encode(pow(int.from_bytes(encoded, "big"), exponent, modulus).to_bytes(size, "big")).decode()


def tts_payload_sign(ssml: str, extra_info: str, device_id: str, app_id: str) -> str:
    material = (
        f"appid:{app_id}&did:{device_id}&creditDisable:false"
        f"&ssml:{hashlib.md5(ssml.encode()).hexdigest()}&extraInfo:{extra_info}"
    )
    return rsa_encrypt_pkcs1v15(material)


def _hmac(key: str | bytes, message: str | bytes) -> bytes:
    """SHA-256 HMAC over str or bytes (the VOD signing key chains its own output)."""
    raw_key = key.encode() if isinstance(key, str) else key
    raw_message = message.encode() if isinstance(message, str) else message
    return hmac.new(raw_key, raw_message, hashlib.sha256).digest()


def _signing_key(secret: str, stamp: str) -> bytes:
    return _hmac(_hmac(_hmac(_hmac("AWS4" + secret, stamp), VOD_REGION), VOD_SERVICE), "aws4_request")


def _canonical_query(url: str) -> str:
    pairs = sorted(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    return "&".join(f"{quote(str(key), safe='-_.~')}={quote(str(value), safe='-_.~')}" for key, value in pairs)


def aws4_authorization(
    method: str,
    url: str,
    body: bytes,
    access_key: str,
    secret_key: str,
    session_token: str,
    amz_date: str,
) -> str:
    scope = f"{amz_date[:8]}/{VOD_REGION}/{VOD_SERVICE}/aws4_request"
    signed = "x-amz-date;x-amz-security-token"
    headers = f"x-amz-date:{amz_date}\nx-amz-security-token:{session_token}\n"
    canonical = "\n".join(
        [
            method,
            urlsplit(url).path,
            _canonical_query(url),
            headers,
            signed,
            hashlib.sha256(body).hexdigest(),
        ]
    )
    string = "\n".join(
        ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()]
    )
    signature = hmac.new(_signing_key(secret_key, amz_date[:8]), string.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"


def _utc_now() -> tuple[str, str]:
    now = dt.datetime.now(dt.UTC)
    return now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%a, %d %b %Y %H:%M:%S GMT")


# ---------------------------------------------------------------------------
# Device profiles
# ---------------------------------------------------------------------------
def _random_id(width: int = 20) -> str:
    return str(secrets.randbelow(10**width)).zfill(width)


def stt_device() -> dict[str, str]:
    """A JianYing desktop identity, which is what ``audio_subtitle`` expects."""
    return {
        "aid": "3704",
        "app_name": "JianyingPro",
        "appvr": "6.0.1",
        "version_name": "6.0.1",
        "version_code": "6.0.1",
        "channel": "jianyingpro_0",
        "device_platform": "windows",
        "device_type": "Windows",
        "device_brand": "American Megatrends Inc.",
        "os_version": "10.0.26200",
        "device_id": _random_id(16),
        "iid": _random_id(16),
        "tdid": _random_id(16),
        "region": "VN",
        "loc": "VN",
        "lan": "vi-VN",
        "pf": "4",
    }


def tts_device() -> dict[str, str]:
    """A CapCut desktop identity; ``common_task`` is the editor's own endpoint."""
    return {
        "aid": "359289",
        "app_name": "CapCut",
        "appvr": "8.7.0",
        "version_name": "8.7.0",
        "version_code": "8.7.0",
        "channel": "capcutpc_google",
        "device_platform": "mac",
        "device_type": "MacBookPro17,4",
        "device_brand": "MacBookPro17,4",
        "os_version": "15.7.4",
        "device_id": _random_id(),
        "iid": _random_id(),
        "tdid": _random_id(),
        "region": "VN",
        "loc": "VN",
        "lan": "vi-VN",
        "pf": "3",
    }


def _user_agent(device: dict[str, str]) -> str:
    return JIANYING_UA if device["app_name"] == "JianyingPro" else CAPCUT_UA


def _base_headers(device: dict[str, str], body: str) -> dict[str, str]:
    device_time = str(int(time.time()))
    return {
        "content-type": "application/json",
        "appvr": device["appvr"],
        "device-time": device_time,
        "lan": device["lan"],
        "loc": device["loc"],
        "pf": device["pf"],
        "sign-ver": "1",
        "tdid": device["tdid"],
        "x-ss-stub": body_stub(body),
        "x-ss-dp": device["aid"],
        "x-khronos": device_time,
        "x-tt-trace-id": trace_id(),
        "user-agent": _user_agent(device),
        "accept-encoding": "gzip, deflate",
    }


def _signed_headers(device: dict[str, str], url: str, body: str, *, appid: bool = False) -> dict[str, str]:
    headers = _base_headers(device, body)
    if appid:
        headers["app-sdk-version"] = device["appvr"]
        headers["appid"] = device["aid"]
    headers["sign"] = sign_header(url, device["appvr"], headers["device-time"], device["tdid"], device["pf"])
    return headers


# ---------------------------------------------------------------------------
# Text to speech
# ---------------------------------------------------------------------------
def resolve_resource_id(voice: str) -> str:
    list_capcut_voices()
    key = (voice or "").replace("capcut:", "").strip()
    if key in CAPCUT_VOICES:
        return CAPCUT_VOICES[key][1]
    # Unknown type: fall back to a Vietnamese female voice rather than fail,
    # which is what CapCap does for every type missing from its catalogue.
    return "7252594014782755330" if "vivn" in key.lower() else "7102355709945188865"


def _post_control_json(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    body: str,
    device: dict[str, str],
    timeout: float = 60.0,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Send a signed CapCut control POST via either Web (httpx) or PC (sscronet.dll)."""
    backend = (
        (settings.capcut_backend if settings else "")
        or os.environ.get("CAPCUT_BACKEND", "web")
        or "web"
    ).strip().lower()
    host = (urlsplit(url).hostname or "").lower()
    if backend == "pc" and host in capcut_pc_worker.API_HOSTS:
        pc_path = (settings.capcut_pc_path if settings else "") or os.environ.get("CAPCUT_PC_PATH", "")
        dll_path = capcut_pc_worker.find_capcut_pc_dll(pc_path)
        status, _resp_headers, raw_body = capcut_pc_worker.native_request(
            "POST",
            url,
            headers=headers,
            body=body.encode("utf-8"),
            timeout=timeout,
            device=device,
            dll_path=dll_path,
        )
        if status >= 400:
            raise CapCutError(f"CapCut PC HTTP {status}: {raw_body.decode('utf-8', 'replace')[:300]}")
        return json.loads(raw_body.decode("utf-8", "replace") or "{}")

    response = client.post(url, headers=headers, content=body.encode("utf-8"), timeout=timeout)
    response.raise_for_status()
    return response.json()


def _voice_type(voice: str) -> str:
    return (voice or DEFAULT_TTS_VOICE).replace("capcut:", "").strip() or DEFAULT_TTS_VOICE


def _ssml(text: str, *, voice: str, rate: float) -> str:
    key = _voice_type(voice)
    block = (
        f'<voice name="{escape_xml(key)}" mock_tone_info="" platform="sami" '
        f'resource_id="{resolve_resource_id(key)}" emotion="" emotion_scale="0" style="" '
        f'role="" moyin_emotion="" is_clone_tone="false" need_subtitle_timestamp="false">\n'
        f'<prosody rate="{rate:.2f}">{escape_xml(text)}</prosody>\n</voice>'
    )
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">\n'
        f"{block}\n</speak>"
    )


def synthesize(
    text: str,
    *,
    voice: str | None = None,
    rate: float = 1.0,
    settings: Settings | None = None,
) -> bytes:
    """Return the mp3 bytes for one line spoken by a CapCut cloud voice."""
    settings = settings or get_settings()
    spoken = (text or "").strip()
    if not spoken:
        raise CapCutError("No text to speak")

    device = tts_device()
    spoken_ssml = _ssml(spoken, voice=voice or settings.capcut_voice, rate=rate)
    extra_info = compact_json({"benefit_info": {}})
    payload = {
        "audio_format": "mp3",
        "babi_param": compact_json(
            {
                "feature_entrance": "editor",
                "feature_entrance_detail": "editor-feature-text_to_speech",
                "feature_key": "text_to_speech",
                "scenario": "video_editor",
            }
        ),
        "credit_disable": False,
        "extra_info": extra_info,
        "need_merge_voice": False,
        "need_subtitle_timestamp": False,
        "scene": "text_to_speech",
        "ssml": spoken_ssml,
    }
    payload["sign"] = tts_payload_sign(spoken_ssml, extra_info, device["device_id"], device["aid"])
    body = {
        "bind_id": str(uuid.uuid4()),
        "can_queue": True,
        "enter_from": "text_to_speech",
        "tasks": [
            {
                "context": str(uuid.uuid4()),
                "payload": compact_json(payload),
                "req_key": "sami_text_to_speech",
                "task_version": "v3",
            }
        ],
    }
    body_text = compact_json(body)
    query = {
        key: device[key]
        for key in (
            "app_name",
            "device_type",
            "os_version",
            "channel",
            "version_name",
            "device_brand",
            "device_id",
            "iid",
            "version_code",
            "device_platform",
            "aid",
            "region",
        )
    }
    query["babi_param"] = payload["babi_param"]
    create_url = f"{TTS_BASE}/lv/v1/common_task/new?{urlencode(query)}"

    try:
        with httpx.Client(timeout=60.0) as client:
            created = client.post(
                create_url,
                headers=_signed_headers(device, create_url, body_text, appid=True),
                content=body_text.encode(),
            )
            created.raise_for_status()
            data = created.json()
            if str(data.get("ret")) not in {"0", "None"}:
                raise CapCutError(f"CapCut TTS refused the task: {data.get('errmsg') or data.get('ret')}")

            tasks = (data.get("data") or {}).get("tasks") or []
            if not tasks:
                raise CapCutError("CapCut TTS returned no task")
            task_id = tasks[0].get("id")
            token = tasks[0].get("token")
            if not task_id or not token:
                raise CapCutError("CapCut TTS task is missing its id or token")

            return _download_tts(client, device, str(task_id), str(token))
    except httpx.HTTPError as exc:
        raise CapCutError(f"CapCut TTS request failed: {exc}") from exc


def _download_tts(client: httpx.Client, device: dict[str, str], task_id: str, token: str) -> bytes:
    query = {
        key: device[key]
        for key in (
            "app_name",
            "device_type",
            "os_version",
            "channel",
            "version_name",
            "device_brand",
            "device_id",
            "iid",
            "version_code",
            "device_platform",
            "aid",
        )
    }
    query_url = f"{TTS_BASE}/lv/v1/common_task/query?{urlencode(query)}"
    body = compact_json(
        {
            "tasks": [
                {
                    "bind_id": "",
                    "id": task_id,
                    "req_key": "sami_text_to_speech",
                    "task_version": "v3",
                    "token": token,
                }
            ]
        }
    )

    deadline = time.monotonic() + TTS_TIMEOUT
    while time.monotonic() < deadline:
        response = client.post(
            query_url,
            headers=_signed_headers(device, query_url, body, appid=True),
            content=body.encode(),
        )
        response.raise_for_status()
        tasks = (response.json().get("data") or {}).get("tasks") or []
        current = tasks[0] if tasks else {}
        status = str(current.get("status", "")).lower()
        if status in {"succeed", "success"}:
            raw = current.get("payload") or "{}"
            payload = json.loads(raw) if isinstance(raw, str) else raw
            results = payload.get("audio_subtitles") or []
            url = (results[0] or {}).get("speech_url") if results else None
            if not url:
                raise CapCutError("CapCut TTS succeeded without an audio URL")
            audio = client.get(url, timeout=120.0)
            audio.raise_for_status()
            return audio.content
        if status in {"failed", "fail", "error"}:
            detail = current.get("err_msg") or current.get("detail_info") or status
            raise CapCutError(f"CapCut TTS task failed: {detail}")
        time.sleep(POLL_INTERVAL)

    raise CapCutError(f"CapCut TTS did not finish within {TTS_TIMEOUT:.0f}s")


# ---------------------------------------------------------------------------
# Speech to text
# ---------------------------------------------------------------------------
def _ffmpeg(args: list[str]) -> None:
    binary = resolve_ffmpeg()
    if binary is None:
        raise CapCutError("ffmpeg is required to prepare audio for CapCut")
    result = subprocess.run(
        [str(binary), "-hide_banner", "-loglevel", "error", *args],
        capture_output=True,
        creationflags=_NO_WINDOW,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace")[:300]
        raise CapCutError(f"ffmpeg failed while preparing the upload: {detail}")


def _chunk_to_mp3(source: Path, target: Path, start: float, duration: float) -> Path:
    _ffmpeg(
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
            "48k",
            "-y",
            str(target),
        ]
    )
    return target


def _upload_credentials(client: httpx.Client, device: dict[str, str]) -> dict[str, str]:
    body = compact_json({"biz": "pc-recognition"})
    query = {
        key: device[key]
        for key in (
            "aid",
            "app_name",
            "channel",
            "device_brand",
            "device_id",
            "device_platform",
            "device_type",
            "iid",
            "os_version",
            "version_code",
            "version_name",
        )
    }
    url = f"{STT_BASE}/lv/v1/upload_sign?{urlencode(query)}"
    response = client.post(url, headers=_signed_headers(device, url, body), content=body.encode(), timeout=60.0)
    response.raise_for_status()
    data = response.json()
    if str(data.get("ret")) not in {"0", "None"}:
        raise CapCutError(f"CapCut upload_sign failed: {data.get('errmsg') or data.get('ret')}")

    credentials = dict(data.get("data") or {})
    if not all(credentials.get(key) for key in ("access_key_id", "secret_access_key", "session_token")):
        raise CapCutError("CapCut did not return upload credentials")
    return credentials


def _upload(client: httpx.Client, device: dict[str, str], audio: Path, credentials: dict[str, str]) -> str:
    """Push one mp3 through ByteDance VOD and return its store URI."""
    raw = audio.read_bytes()
    crc32 = f"{binascii.crc32(raw) & 0xFFFFFFFF:08x}"

    def vod_headers(method: str, url: str, body: bytes) -> dict[str, str]:
        amz_date, http_date = _utc_now()
        return {
            "Authorization": aws4_authorization(
                method,
                url,
                body,
                credentials["access_key_id"],
                credentials["secret_access_key"],
                credentials["session_token"],
                amz_date,
            ),
            "Date": http_date,
            "User-Agent": f"BDFileUpload({int(time.time() * 1000)})",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": "31536000",
            "X-Amz-Security-Token": credentials["session_token"],
            "accept-encoding": "identity",
        }

    apply_url = (
        f"https://{VOD_HOST}/top/v1?"
        + urlencode(
            {
                "Action": "ApplyUploadInner",
                "SpaceName": VOD_SPACE,
                "UseQuic": "false",
                "Version": "2020-11-19",
                "device_platform": "win",
            }
        )
    )
    applied = client.get(apply_url, headers=vod_headers("GET", apply_url, b""), timeout=60.0)
    applied.raise_for_status()
    node = applied.json()["Result"]["InnerUploadAddress"]["UploadNodes"][0]
    store = node["StoreInfos"][0]
    store_uri = store["StoreUri"]
    host = node["UploadHost"]
    upload_id = store["UploadID"]

    transfer_url = f"https://{host}/upload/v1/{store_uri}?" + urlencode(
        {"uploadid": upload_id, "part_number": "0", "phase": "transfer"}
    )
    transfer_headers = {
        "Authorization": store["Auth"],
        "User-Agent": f"BDFileUpload({int(time.time() * 1000)})",
        "X-Upload-Content-CRC32": crc32,
        "accept-encoding": "identity",
    }
    transferred = client.post(transfer_url, headers=transfer_headers, content=raw, timeout=300.0)
    transferred.raise_for_status()

    finish_url = f"https://{host}/upload/v1/{store_uri}?" + urlencode(
        {"uploadmode": "part", "phase": "finish", "uploadid": upload_id}
    )
    finish_headers = dict(transfer_headers)
    finish_headers.pop("X-Upload-Content-CRC32", None)
    finished = client.post(finish_url, headers=finish_headers, content=f"0:{crc32}".encode(), timeout=60.0)
    finished.raise_for_status()
    return str(store_uri)


def _submit(client: httpx.Client, device: dict[str, str], store_uri: str, duration_ms: int) -> str:
    query = {
        key: device[key]
        for key in (
            "aid",
            "app_name",
            "channel",
            "device_brand",
            "device_id",
            "device_platform",
            "device_type",
            "iid",
            "os_version",
            "version_code",
            "version_name",
        )
    }
    body = compact_json(
        {
            "adjust_endtime": 200,
            "audio": store_uri,
            "caption_type": 2,
            "client_request_id": str(uuid.uuid4()),
            "max_lines": 1,
            "songs_info": [{"end_time": duration_ms, "id": "", "start_time": 0}],
            "words_per_line": 16,
        }
    )
    url = f"{STT_BASE}/lv/v1/audio_subtitle/submit?{urlencode(query)}"
    response = client.post(url, headers=_signed_headers(device, url, body), content=body.encode(), timeout=60.0)
    response.raise_for_status()
    data = response.json()
    task_id = (data.get("data") or {}).get("id")
    if not task_id:
        raise CapCutError(f"CapCut STT submit failed: ret={data.get('ret')} msg={data.get('errmsg')}")
    return str(task_id)


def _await_utterances(client: httpx.Client, device: dict[str, str], task_id: str) -> list[dict[str, Any]]:
    query = {
        key: device[key]
        for key in (
            "aid",
            "app_name",
            "channel",
            "device_brand",
            "device_id",
            "device_platform",
            "iid",
            "os_version",
            "version_code",
            "version_name",
        )
    }
    body = compact_json({"id": task_id, "pack_options": {"need_attribute": True}})
    url = f"{STT_BASE}/lv/v1/audio_subtitle/query?{urlencode(query)}"

    deadline = time.monotonic() + STT_TIMEOUT
    while time.monotonic() < deadline:
        response = client.post(url, headers=_signed_headers(device, url, body), content=body.encode(), timeout=60.0)
        response.raise_for_status()
        data = response.json()
        utterances = (data.get("data") or {}).get("utterances") or []
        if utterances:
            return list(utterances)
        if str(data.get("ret")) not in {"0", "None"}:
            raise CapCutError(f"CapCut STT query failed: ret={data.get('ret')} msg={data.get('errmsg')}")
        time.sleep(POLL_INTERVAL)
    raise CapCutError(f"CapCut did not finish the transcription within {STT_TIMEOUT:.0f}s")


def transcribe(
    audio: str | Path,
    *,
    work_dir: str | Path,
    report: Reporter = NULL_REPORTER,
    settings: Settings | None = None,
) -> list[tuple[float, float, str]]:
    """Transcribe a WAV clip through CapCut, returning ``(start, end, text)``."""
    source = Path(audio)
    if not source.is_file():
        raise CapCutError(f"Audio not found: {source}")

    from app.services.media_processor import MediaError, probe_duration  # noqa: PLC0415 - import cycle

    try:
        duration = probe_duration(source)
    except MediaError as exc:
        raise CapCutError(str(exc)) from exc
    if duration <= 0:
        raise CapCutError("Cannot determine the audio duration")

    folder = Path(work_dir)
    folder.mkdir(parents=True, exist_ok=True)

    starts: list[tuple[float, float]] = []
    position = 0.0
    while position < duration:
        length = min(CHUNK_SECONDS, duration - position)
        starts.append((position, length))
        position += length

    lines: list[tuple[float, float, str]] = []
    report(stage="upload", fraction=0.02, message=f"CapCut STT: {len(starts)} chunk(s)")

    with httpx.Client(timeout=120.0) as client:
        for index, (start, length) in enumerate(starts):
            device = stt_device()
            report(
                stage="upload",
                fraction=0.02 + 0.9 * index / len(starts),
                message=f"CapCut chunk {index + 1}/{len(starts)}",
            )
            chunk = _chunk_to_mp3(source, folder / f"capcut_{index:03d}.mp3", start, length)
            try:
                credentials = _upload_credentials(client, device)
                store_uri = _upload(client, device, chunk, credentials)
                task_id = _submit(client, device, store_uri, int(length * 1000))
                utterances = _await_utterances(client, device, task_id)
            finally:
                chunk.unlink(missing_ok=True)

            for item in utterances:
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                left = start + float(item.get("start_time") or 0) / 1000.0
                right = start + float(item.get("end_time") or 0) / 1000.0
                lines.append((round(left, 3), round(max(right, left + 0.2), 3), text))

    report(stage="transcribe", fraction=0.95, message=f"CapCut returned {len(lines)} line(s)")
    return lines


__all__ = [
    "CAPCUT_VOICES",
    "DEFAULT_TTS_VOICE",
    "CapCutError",
    "aws4_authorization",
    "body_stub",
    "compact_json",
    "resolve_resource_id",
    "rsa_encrypt_pkcs1v15",
    "sign_header",
    "synthesize",
    "trace_id",
    "transcribe",
]
