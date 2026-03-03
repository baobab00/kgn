"""KGN Web — In-memory event bus for SSE streaming.

Provides a simple publish/subscribe mechanism using asyncio queues.
Phase 9 builds infrastructure; Phase 10 wires actual task events.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from typing import Any


class EventBus:
    """In-memory event bus for Server-Sent Events (SSE).

    Subscribers receive events via async iteration.
    A bounded history is kept for late-joining clients.
    """

    def __init__(self, maxlen: int = 100) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._history: deque[dict[str, Any]] = deque(maxlen=maxlen)

    @property
    def subscriber_count(self) -> int:
        """Number of active subscribers."""
        return len(self._subscribers)

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return a copy of the event history (oldest first)."""
        return list(self._history)

    async def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to all active subscribers.

        Args:
            event_type: Event type string (e.g. 'task_checkout').
            data: Arbitrary JSON-serializable payload.
        """
        event: dict[str, Any] = {
            "type": event_type,
            "data": data,
            "timestamp": time.time(),
        }
        self._history.append(event)
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    async def subscribe(self):  # noqa: ANN201
        """Async generator that yields events as they are published.

        Usage::

            async for event in bus.subscribe():
                print(event)
        """
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.append(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            self._subscribers.remove(q)


# Module-level singleton used by the application.
event_bus = EventBus()
