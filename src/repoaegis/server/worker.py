"""Picks queued tasks and walks them to the approval gate.

For now the "plan" is a placeholder; the agent line will replace
``stub_plan``. The claim is a compare-and-set through the state machine, so
several workers against one PostgreSQL can run this loop unchanged (a
``SKIP LOCKED`` lease is the next step when polling becomes a cost).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog

from repoaegis.server.models import Task, TaskStatus
from repoaegis.server.state import ConcurrentTransition, TaskMachine
from repoaegis.server.storage import TaskRepo

log = structlog.get_logger(__name__)


def stub_plan(task: Task) -> dict[str, Any]:
    return {
        "summary": f"Placeholder plan for {task.title}",
        "steps": ["locate the relevant code", "apply a patch", "run the tests"],
        "risk": "unknown",
    }


class Worker:
    def __init__(self, machine: TaskMachine, repo: TaskRepo, *, poll_seconds: float) -> None:
        self._machine = machine
        self._repo = repo
        self._poll = poll_seconds

    async def tick(self) -> bool:
        """Process at most one task. Returns whether there was anything to do."""
        task = await self._repo.next_queued()
        if task is None:
            return False
        try:
            await self._machine.advance(task.id, TaskStatus.PLANNING)
        except ConcurrentTransition:
            return True  # another worker got it; look again immediately
        plan = stub_plan(task)
        await self._machine.advance(task.id, TaskStatus.AWAITING_APPROVAL, payload={"plan": plan})
        return True

    async def run(self, stop: asyncio.Event) -> None:
        log.info("worker_started", poll_seconds=self._poll)
        while not stop.is_set():
            try:
                worked = await self.tick()
            except Exception:
                log.exception("worker_tick_failed")
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), self._poll)
        log.info("worker_stopped")
