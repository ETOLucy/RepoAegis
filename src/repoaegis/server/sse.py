"""Server-Sent Events over the audit log.

A client that reconnects sends ``Last-Event-ID``; we replay everything after it
from storage, then switch to the live bus. Subscribing *before* replaying and
de-duplicating on id closes the gap between the two.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from repoaegis.server.events import EventBus
from repoaegis.server.models import Event
from repoaegis.server.storage import TaskRepo


def format_sse(event: Event) -> str:
    return f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


HEARTBEAT = ": ping\n\n"


async def event_stream(
    bus: EventBus, repo: TaskRepo, *, after_id: int = 0, heartbeat_seconds: float = 15.0
) -> AsyncIterator[str]:
    async with bus.subscribe() as queue:
        last = after_id
        for event in await repo.list_events(after_id=after_id):
            last = event.id
            yield format_sse(event)
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), heartbeat_seconds)
            except TimeoutError:
                yield HEARTBEAT
                continue
            if event.id <= last:
                continue
            last = event.id
            yield format_sse(event)
