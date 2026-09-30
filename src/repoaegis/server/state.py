"""The outer state machine, written by hand.

``TRANSITIONS`` is the whole control-flow spec: a task may only move along an
edge listed here. ``TaskMachine.advance`` is the place a status changes while a
stage is running, and every change leaves an event behind. That is deliberately
the entire framework; with this few states a graph library would hide more than
it shows.

Two other writers move a status, both in ``TaskRepo`` and both compare-and-set:
``claim`` (a worker taking a task, which has to write the lease in the same
statement) and ``reclaim_expired`` (taking a task back from a dead worker).
The machine wraps both so the events still come from here.

Leases: while a worker holds a task, every write for that task must carry the
worker's ``Claim``. The worker puts its claim in ``current_claim`` (defined next
to the writes that check it, in ``storage``) for the duration of a stage, so
the services underneath need not thread it through every call; a write can
still pass ``claim=`` explicitly. A write with no claim at all is the human
path (answering a gate) and is accepted only while no worker holds the task.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Final

import structlog

from repoaegis.server.events import EventBus
from repoaegis.server.models import Claim, Event, Task, TaskCreate, TaskStatus, default_title
from repoaegis.server.storage import Reclaimed, StaleLease, TaskRepo, current_claim

__all__ = [
    "TERMINAL",
    "TRANSITIONS",
    "ConcurrentTransition",
    "IllegalTransition",
    "StaleLease",
    "TaskMachine",
    "TaskNotFound",
    "assert_transition",
    "current_claim",
]

log = structlog.get_logger(__name__)

S = TaskStatus

TRANSITIONS: Final = MappingProxyType(
    {
        # PLANNING -> QUEUED is the reclaim edge: a planner that died starts over.
        S.QUEUED: frozenset({S.PLANNING, S.FAILED}),
        S.PLANNING: frozenset({S.AWAITING_APPROVAL, S.QUEUED, S.FAILED}),
        S.AWAITING_APPROVAL: frozenset({S.SOLVING, S.REJECTED}),
        # VERIFYING is reserved for the round that runs the repository's tests;
        # with only a syntax check, solving hands straight to the patch gate.
        S.SOLVING: frozenset({S.AWAITING_PATCH_APPROVAL, S.VERIFYING, S.FAILED}),
        S.AWAITING_PATCH_APPROVAL: frozenset({S.DELIVERING, S.REJECTED}),
        S.VERIFYING: frozenset({S.AWAITING_PATCH_APPROVAL, S.SOLVING, S.FAILED}),
        # A human refusing the push is a rejection, not a failure.
        S.DELIVERING: frozenset({S.DONE, S.FAILED, S.REJECTED}),
        S.DONE: frozenset[TaskStatus](),
        S.FAILED: frozenset[TaskStatus](),
        S.REJECTED: frozenset[TaskStatus](),
    }
)

TERMINAL: Final = frozenset({S.DONE, S.FAILED, S.REJECTED})


class TaskNotFound(LookupError):
    pass


class IllegalTransition(ValueError):
    def __init__(self, frm: TaskStatus, to: TaskStatus) -> None:
        super().__init__(f"illegal transition {frm.value} -> {to.value}")
        self.frm = frm
        self.to = to


class ConcurrentTransition(RuntimeError):
    """Someone else moved the task between our read and our write."""


def assert_transition(frm: TaskStatus, to: TaskStatus) -> None:
    if to not in TRANSITIONS[frm]:
        raise IllegalTransition(frm, to)


class TaskMachine:
    def __init__(self, repo: TaskRepo, bus: EventBus) -> None:
        self._repo = repo
        self._bus = bus

    async def create(self, data: TaskCreate) -> Task:
        issue_url = str(data.issue_url)
        task = await self._repo.create(
            issue_url=issue_url, title=data.title or default_title(issue_url)
        )
        await self.emit(task.id, "task.created", {"title": task.title, "issue_url": issue_url})
        return task

    async def get(self, task_id: str) -> Task | None:
        return await self._repo.get(task_id)

    async def advance(
        self,
        task_id: str,
        to: TaskStatus,
        *,
        payload: dict[str, Any] | None = None,
        claim: Claim | None = None,
    ) -> Task:
        claim = claim or current_claim.get()
        task = await self._repo.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        assert_transition(task.status, to)
        updated = await self._repo.transition(task_id, expected=task.status, to=to, claim=claim)
        if updated is None:
            raise ConcurrentTransition(task_id)
        await self.emit(
            task_id,
            "task.status_changed",
            {"from": task.status.value, "to": to.value, **(payload or {})},
            claim=claim,
        )
        return updated

    async def claim(
        self,
        task: Task,
        to: TaskStatus,
        *,
        owner: str,
        ttl_seconds: float,
    ) -> Claim | None:
        """A worker takes ``task``; ``None`` if another worker got there first.

        Re-entering the same state (a solve round, a delivery attempt) is not a
        transition and leaves no status event; only the lease changes.
        """
        if to is not task.status:
            assert_transition(task.status, to)
        claim = await self._repo.claim(
            task.id, expected=task.status, to=to, owner=owner, ttl_seconds=ttl_seconds
        )
        if claim is not None and to is not task.status:
            await self.emit(
                task.id,
                "task.status_changed",
                {"from": task.status.value, "to": to.value},
                claim=claim,
            )
        return claim

    async def reclaim_expired(self, *, max_recoveries: int) -> list[Reclaimed]:
        """Take back tasks whose worker stopped renewing, and say so in the log."""
        taken = await self._repo.reclaim_expired(max_recoveries=max_recoveries)
        for item in taken:
            task = item.task
            await self.emit(
                task.id,
                "task.reclaimed",
                {
                    "previous_owner": item.previous_owner,
                    "recoveries": task.recoveries,
                    "exhausted": item.exhausted,
                },
            )
            if task.status is not item.previous_status:
                reason = "recovery_exhausted" if item.exhausted else "lease_expired"
                await self.emit(
                    task.id,
                    "task.status_changed",
                    {"from": item.previous_status.value, "to": task.status.value, "reason": reason},
                )
        return taken

    async def emit(
        self,
        task_id: str,
        type: str,
        payload: dict[str, Any],
        *,
        claim: Claim | None = None,
    ) -> Event:
        """Public because the approval gate records its own steps on the same log."""
        claim = claim or current_claim.get()
        # Three consumers, in order: audit table (source of truth), log, live bus.
        event = await self._repo.append_event(task_id, type, payload, claim=claim)
        log.info(type, task_id=task_id, event_id=event.id, **payload)
        await self._bus.publish(event)
        return event
