"""Picks up tasks, runs one stage on each, and keeps the lease alive meanwhile.

Taking a task is a compare-and-set through the state machine that also writes
a lease (who, until when, which claim), so several workers against one database
can run this loop unchanged. While a stage runs, a heartbeat renews the lease;
a worker that dies stops renewing, and whichever worker sweeps next takes the
task back. A worker that was only paused finds its renewal refused and drops
the stage before it can write over the new holder's work.

The stage services live elsewhere; this loop only decides *which* task gets
worked on and when, and it also sweeps expired approval envelopes so no gate
can sit pending forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import uuid
from collections.abc import Awaitable, Callable

import structlog

from repoaegis.server.delivery import DeliveryService
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import Claim, Task, TaskStatus
from repoaegis.server.planning import PlanningService
from repoaegis.server.solving import SolvingService
from repoaegis.server.state import StaleLease, TaskMachine, current_claim
from repoaegis.server.storage import TaskRepo

log = structlog.get_logger(__name__)

Stage = Callable[[Task], Awaitable[None]]


def default_owner() -> str:
    """Enough to tell workers apart in a log: host, process, and a little salt."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class Worker:
    def __init__(
        self,
        machine: TaskMachine,
        repo: TaskRepo,
        gate: ApprovalGate,
        planning: PlanningService,
        solving: SolvingService,
        delivery: DeliveryService,
        *,
        poll_seconds: float,
        lease_seconds: float = 300.0,
        heartbeat_seconds: float = 30.0,
        max_recoveries: int = 3,
        owner: str | None = None,
    ) -> None:
        self._machine = machine
        self._repo = repo
        self._gate = gate
        self._planning = planning
        self._solving = solving
        self._delivery = delivery
        self._poll = poll_seconds
        self._lease = lease_seconds
        self._heartbeat = heartbeat_seconds
        self._max_recoveries = max_recoveries
        self.owner = owner or default_owner()

    async def tick(self) -> bool:
        """Sweep what is overdue, then advance at most one task by one stage.

        Furthest along first: a task one step from a pull request should not
        wait behind a task nobody has looked at.
        """
        worked = bool(await self._gate.sweep_expired())
        worked = (
            bool(await self._machine.reclaim_expired(max_recoveries=self._max_recoveries)) or worked
        )

        ready = await self._repo.next_in(TaskStatus.DELIVERING)
        if ready is not None:
            await self._take(ready, TaskStatus.DELIVERING, self._delivery.deliver)
            return True

        approved = await self._repo.next_in(TaskStatus.SOLVING)
        if approved is not None:
            await self._take(approved, TaskStatus.SOLVING, self._solving.solve)
            return True

        task = await self._repo.next_queued()
        if task is None:
            return worked
        await self._take(task, TaskStatus.PLANNING, self._planning.plan)
        return True

    async def _take(self, task: Task, to: TaskStatus, stage: Stage) -> None:
        claim = await self._machine.claim(task, to, owner=self.owner, ttl_seconds=self._lease)
        if claim is None:
            return  # another worker got it; the caller looks again immediately
        await self._run(claim, task, stage)

    async def _run(self, claim: Claim, task: Task, stage: Stage) -> None:
        """One stage under one lease: heartbeat beside it, release after it.

        Everything the stage can fail at -- including every way it can fail --
        is the stage service's job, which always leaves the task in a settled
        state. The two things handled here are the lease being lost (the stage
        is cancelled and abandoned) and the lease being given back at the end.
        """
        me = asyncio.current_task()
        assert me is not None
        lost = asyncio.Event()

        async def beat() -> None:
            while True:
                await asyncio.sleep(self._heartbeat)
                if not await self._repo.renew(claim, ttl_seconds=self._lease):
                    lost.set()
                    log.warning("lease.lost", task_id=claim.task_id, token=claim.token)
                    me.cancel()
                    return

        token = current_claim.set(claim)
        heartbeat = asyncio.create_task(beat())
        try:
            await stage(task)
        except asyncio.CancelledError:
            if not lost.is_set():
                raise  # a real shutdown, not ours to swallow
            me.uncancel()
            log.warning("stage.abandoned", task_id=claim.task_id, token=claim.token)
        except StaleLease:
            # Refused at the write itself: the belt to the heartbeat's braces.
            log.warning("stage.stale_lease", task_id=claim.task_id, token=claim.token)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            current_claim.reset(token)
            await self._repo.release(claim)

    async def run(self, stop: asyncio.Event) -> None:
        log.info("worker_started", owner=self.owner, poll_seconds=self._poll)
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
