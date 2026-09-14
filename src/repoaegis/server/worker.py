"""Picks queued tasks, plans them, and opens the approval gate.

The claim is a compare-and-set through the state machine, so several workers
against one PostgreSQL can run this loop unchanged (a ``SKIP LOCKED`` lease is
the next step when polling becomes a cost).

Planning itself lives in ``PlanningService``; this loop only decides *which*
task gets worked on and when. One tick now takes as long as a clone plus a
model loop, which is the price of the work actually happening.

The same loop also sweeps expired envelopes, which is why no approval can sit
pending forever: the deadline is enforced by a writer, not by a reader noticing
the clock.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog

from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import TaskStatus
from repoaegis.server.planning import PlanningService
from repoaegis.server.solving import SolvingService
from repoaegis.server.state import ConcurrentTransition, TaskMachine
from repoaegis.server.storage import TaskRepo

log = structlog.get_logger(__name__)


class Worker:
    def __init__(
        self,
        machine: TaskMachine,
        repo: TaskRepo,
        gate: ApprovalGate,
        planning: PlanningService,
        solving: SolvingService,
        *,
        poll_seconds: float,
    ) -> None:
        self._machine = machine
        self._repo = repo
        self._gate = gate
        self._planning = planning
        self._solving = solving
        self._poll = poll_seconds

    async def tick(self) -> bool:
        """Expire what is overdue, then advance at most one task by one stage.

        Approved work is taken before new work: a task a human has already spent
        attention on should not queue behind a task nobody has looked at yet.
        """
        worked = bool(await self._gate.sweep_expired())

        approved = await self._repo.next_in(TaskStatus.SOLVING)
        if approved is not None:
            await self._solving.solve(approved)
            return True

        task = await self._repo.next_queued()
        if task is None:
            return worked
        try:
            await self._machine.advance(task.id, TaskStatus.PLANNING)
        except ConcurrentTransition:
            return True  # another worker got it; look again immediately
        # Everything past the claim -- including every way it can fail -- is the
        # planning service's job, which always leaves the task in a settled state.
        await self._planning.plan(task)
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
