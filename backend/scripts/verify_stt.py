"""Round-trip check of the speech pipeline: CapCut TTS -> faster-whisper.

Downloads the active Whisper checkpoint through the managed model API (with
live job progress), then transcribes a Vietnamese sentence that was just
synthesised, proving dubbing + STT work end to end on this machine.

    cd backend
    $env:PYTHONPATH="."; ..\\.venv\\Scripts\\python.exe scripts\\verify_stt.py [--size medium]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8756"
SENTENCE = "Xin chào, đây là bài kiểm tra nhận dạng giọng nói tiếng Việt."
HINTS = ("chào", "kiểm tra", "giọng nói", "tiếng việt", "nhận dạng")


async def wait_for_job(client: httpx.AsyncClient, job_id: str) -> dict:
    deadline = time.monotonic() + 3600
    last = ""
    while time.monotonic() < deadline:
        job = (await client.get(f"/api/jobs/{job_id}")).json()
        line = f"  {job['state']:<7} {job['stage']:<10} {job['progress'] * 100:5.1f}%  {job.get('message') or ''}"
        if line != last:
            print(line)
            last = line
        if job["state"] in {"done", "error", "cancelled"}:
            return job
        await asyncio.sleep(1.5)
    raise TimeoutError(f"job {job_id} did not finish")


async def run(size: str) -> int:
    work = Path(tempfile.mkdtemp(prefix="sva-stt-"))
    async with httpx.AsyncClient(
        base_url=BASE, timeout=httpx.Timeout(connect=10.0, read=3600.0, write=300.0, pool=30.0)
    ) as client:
        models = (await client.get("/api/models")).json()["models"]
        spec = next((m for m in models if m["key"] == f"whisper-{size}"), None)
        if spec is None:
            print(f"whisper-{size} is not in the catalogue")
            return 1
        print(f"whisper-{size}: downloaded={spec['downloaded']} installed={spec['installed_mb']} MB")

        if not spec["downloaded"]:
            response = await client.post(f"/api/models/whisper-{size}/download")
            job = response.json()["job"]
            print(f"downloading ({spec['size_mb']} MB) as job {job['id']}")
            finished = await wait_for_job(client, job["id"])
            if finished["state"] != "done":
                print(f"download failed: {finished.get('error')}")
                return 1

        speech = work / "speech.mp3"
        print(f"\nsynthesising: {SENTENCE}")
        preview = await client.post(
            "/api/dubbing/preview",
            params={"text": SENTENCE, "provider": "capcut"},
        )
        if preview.status_code != 200:
            print(f"tts preview failed: {preview.status_code} {preview.text[:300]}")
            return 1
        speech.write_bytes(Path(preview.json()["path"]).read_bytes())
        print(f"audio: {preview.json()['duration']:.2f}s at {speech}")

        print("\ntranscribing...")
        started = time.monotonic()
        response = await client.post(
            "/api/subtitles/transcribe",
            json={"video_path": str(speech), "language": "vi", "provider": "local", "model": size},
        )
        if response.status_code != 200:
            print(f"transcribe failed: {response.status_code} {response.text[:500]}")
            return 1

        result = response.json()
        elapsed = time.monotonic() - started
        print(f"\n{result['provider']}/{result['model']} on {result['device']} in {elapsed:.1f}s")
        for segment in result["segments"]:
            print(f"  [{segment['start']:6.2f} -> {segment['end']:6.2f}] {segment['text']}")

        joined = " ".join(segment["text"] for segment in result["segments"]).lower()
        hits = [hint for hint in HINTS if hint in joined]
        print(f"\nkeyword hits: {hits}")
        if not hits:
            print("FAIL: recognised text does not contain any expected Vietnamese keyword")
            return 1
        if len(result["segments"]) < 1:
            print("FAIL: no segments")
            return 1
        print("STT ROUND-TRIP PASSED")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", default="medium", choices=["small", "medium", "large-v3-turbo"])
    args = parser.parse_args()
    return asyncio.run(run(args.size))


if __name__ == "__main__":
    sys.exit(main())
