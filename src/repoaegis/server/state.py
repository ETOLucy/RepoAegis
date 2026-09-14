"""The outer state machine, written by hand.

``TRANSITIONS`` is the whole control-flow spec: a task may only move along an
edge listed here. ``TaskMachine.advance`` is the single place a status ever
changes, and every change leaves an event behind. That is deliberately the
entire framework; with this few states a graph library would hide more than
it shows.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Final

import structlog

from repoaegis.server.events import EventBus
from repoaegis.server.models import Event, Task, TaskCreate, TaskStatus, default_title
from repoaegis.server.storage import TaskRepo

log = structlog.get_logger(__name__)

S = TaskStatus

TRANSITIONS: Final = MappingProxyType(
    {
        S.QUEUED: frozenset({S.PLANNING, S.FAILED}),
        S.PLANNING: frozenset({S.AWAITING_APPROVAL, S.FAILED}),
        S.AWAITING_APPROVAL: frozenset({S.SOLVING, S.REJECTED}),
        # VERIFYING is reserved for the round that runs the repository's tests;
        # with only a syntax check, solving hands straight to the patch gate.
        S.SOLVING: frozenset({S.AWAITING_PATCH_APPROVAL, S.VERIFYING, S.FAILED}),
        S.AWAITING_PATCH_APPROVAL: frozenset({S.DELIVERING, S.REJECTED}),
        S.VERIFYING: frozenset({S.AWAITING_PATCH_APPROVAL, S.SOLVING, S.FAILED}),
        S.DELIVERING: frozenset({S.DONE, S.FAILED}),
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

    async def advance(
        self, task_id: str, to: TaskStatus, *, payload: dict[str, Any] | None = None
    ) -> Task:
        task = await self._repo.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        assert_transition(task.status, to)
        updated = await self._repo.transition(task_id, expected=task.status, to=to)
        if updated is None:
            raise ConcurrentTransition(task_id)
        await self.emit(
            task_id,
            "task.status_changed",
            {"from": task.status.value, "to": to.value, **(payload or {})},
        )
        return updated

    async def emit(self, task_id: str, type: str, payload: dict[str, Any]) -> Event:
        """Public because the approval gate records its own steps on the same log."""
        # Three consumers, in order: audit table (source of truth), log, live bus.
        event = await self._repo.append_event(task_id, type, payload)
        log.info(type, task_id=task_id, event_id=event.id, **payload)
        await self._bus.publish(event)
        return event
