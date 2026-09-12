"""In-process event bus: publish fans out to every live subscriber queue.

The database is the source of truth (events are appended there first); this
bus is only the low-latency projection that feeds SSE and logs. A subscriber
that falls behind loses events here but can always replay from storage.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog

from repoaegis.server.models import Event

log = structlog.get_logger(__name__)


class EventBus:
    def __init__(self, *, queue_size: int = 1000) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[Event]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, event: Event) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("event_dropped", event_id=event.id, reason="subscriber_backlog")

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)
