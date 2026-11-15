"""``/ws/progress`` - the single realtime channel of the app.

Every connected editor window receives:

* ``hello``     immediately after the handshake (hardware + model inventory)
* ``progress``  stage / percentage updates for whatever job is running
* ``subtitle``  newly recognised or translated lines, streamed one by one
* ``result`` / ``error`` terminal frames
* ``ping``      keep-alive every 25 s so proxies and the WebView stay awake
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import APP_VERSION
from app.api.events import bus
from app.core import hardware
from app.schemas.job import ProgressEvent
from app.services import model_manager, stt_service

log = logging.getLogger(__name__)

router = APIRouter()

HEARTBEAT_S = 25.0


@router.websocket("/ws/progress")
async def progress_socket(socket: WebSocket) -> None:
    await socket.accept()
    queue = bus.subscribe()
    log.info("progress socket connected (%s listeners)", bus.subscriber_count)

    try:
        profile = hardware.detect()
        await socket.send_json(
            ProgressEvent(
                type="hello",
                data={
                    "version": APP_VERSION,
                    "hardware": profile.to_dict(),
                    "device": "cuda" if profile.has_cuda else "cpu",
                    "models": [status.to_dict() for status in model_manager.list_models()],
                    "whisper_cache": stt_service.loaded(),
                },
            ).model_dump(mode="json")
        )

        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
            except TimeoutError:
                await socket.send_json({"type": "ping", "ts": time.time()})
                continue
            await socket.send_json(payload)
    except WebSocketDisconnect:
        log.debug("progress socket disconnected")
    except Exception as exc:  # noqa: BLE001 - closed socket during send
        log.debug("progress socket closed: %s", exc)
    finally:
        bus.unsubscribe(queue)
        log.info("progress socket released (%s listeners)", bus.subscriber_count)


__all__ = ["router"]
