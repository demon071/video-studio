"""In-process pub/sub bus bridging worker threads and WebSocket clients.

FFmpeg and the AI services run in worker threads, while the sockets live on
the asyncio loop. :meth:`EventBus.publish` is therefore thread safe: it hands
the payload to the loop with ``call_soon_threadsafe`` and never blocks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.schemas.job import ProgressEvent

log = logging.getLogger(__name__)

QUEUE_SIZE = 512


def _offer(queue: asyncio.Queue, payload: dict[str, Any]) -> None:
    """Drop the frame when a slow client lets the queue fill up."""
    try:
        queue.put_nowait(payload)
    except asyncio.QueueFull:
        log.debug("progress queue full, dropping frame")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- lifecycle ---------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the server loop so threads can publish safely."""
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # -- publishing --------------------------------------------------------
    def publish(self, event: ProgressEvent) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        payload = event.model_dump(mode="json")
        for queue in list(self._subscribers):
            try:
                loop.call_soon_threadsafe(_offer, queue, payload)
            except RuntimeError:  # loop shut down between check and call
                return

    def publish_nowait(self, event: ProgressEvent) -> None:
        """For code already running on the loop thread."""
        payload = event.model_dump(mode="json")
        for queue in list(self._subscribers):
            _offer(queue, payload)


bus = EventBus()


__all__ = ["EventBus", "bus"]
