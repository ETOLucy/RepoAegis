"""Picks queued tasks, plans, and opens the approval gate.

For now the "plan" is a placeholder; the agent line will replace ``stub_plan``.
The claim is a compare-and-set through the state machine, so several workers
against one PostgreSQL can run this loop unchanged (a ``SKIP LOCKED`` lease is
the next step when polling becomes a cost).

The same loop also sweeps expired envelopes, which is why no approval can sit
pending forever: the deadline is enforced by a writer, not by a reader noticing
the clock.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog

from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, Task, TaskStatus
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
    def __init__(
        self, machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate, *, poll_seconds: float
    ) -> None:
        self._machine = machine
        self._repo = repo
        self._gate = gate
        self._poll = poll_seconds

    async def tick(self) -> bool:
        """One unit of work: expire what is overdue, then plan at most one task."""
        worked = bool(await self._gate.sweep_expired())
        task = await self._repo.next_queued()
        if task is None:
            return worked
        try:
            await self._machine.advance(task.id, TaskStatus.PLANNING)
        except ConcurrentTransition:
            return True  # another worker got it; look again immediately
        plan = stub_plan(task)
        await self._machine.advance(task.id, TaskStatus.AWAITING_APPROVAL, payload={"plan": plan})
        # The gate may settle immediately (policy said allow or deny) or leave a
        # pending envelope; either way the task's next move belongs to the gate.
        await self._gate.request(
            task.id, ApprovalKind.PLAN, {"plan": plan}, subject=str(plan["summary"])
        )
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
