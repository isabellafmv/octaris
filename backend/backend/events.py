from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._subscribers: dict[int, asyncio.Queue[dict[str, Any]]] = {}
        self._next_id = 0

    def subscribe(self) -> tuple[int, asyncio.Queue[dict[str, Any]]]:
        sub_id = self._next_id
        self._next_id += 1
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        self._subscribers.pop(sub_id, None)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers.values():
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Event queue full, dropping event: %s", event.get("type"))
