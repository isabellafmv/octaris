from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Event = dict[str, Any]


class EventBus:
    """The app's one event channel.

    Components publish events (dicts with a "type"). Every event goes to
    each WebSocket subscriber's queue, then to the in-process listeners,
    which are called synchronously, so e.g. a lost connection has stopped
    the print before publish() returns.
    """

    def __init__(self):
        self._subscribers: dict[int, asyncio.Queue[Event]] = {}
        self._listeners: list[Callable[[Event], None]] = []
        self._next_id = 0

    def subscribe(self) -> tuple[int, asyncio.Queue[Event]]:
        sub_id = self._next_id
        self._next_id += 1
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        self._subscribers.pop(sub_id, None)

    def listen(self, handler: Callable[[Event], None]) -> None:
        """Call handler(event) for every event published from now on."""
        self._listeners.append(handler)

    def publish(self, event: Event) -> None:
        for queue in self._subscribers.values():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full, dropping event: %s", event.get("type"))
        for handler in list(self._listeners):
            try:
                handler(event)
            except Exception:
                logger.exception("Event listener failed on %s", event.get("type"))
