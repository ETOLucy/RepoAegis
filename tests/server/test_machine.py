from unittest.mock import AsyncMock

import pytest

from repoaegis.server.models import TaskCreate, TaskStatus
from repoaegis.server.state import (
    ConcurrentTransition,
    IllegalTransition,
    TaskMachine,
    TaskNotFound,
)
from repoaegis.server.storage import TaskRepo

ISSUE = "https://github.com/octo/repo/issues/7"


async def test_create_records_an_event_and_derives_a_title(
    machine: TaskMachine, repo: TaskRepo
) -> None:
    task = await machine.create(TaskCreate(issue_url=ISSUE))
    assert task.status is TaskStatus.QUEUED
    assert task.title == "octo/repo#7"
    events = await repo.list_events(task_id=task.id)
    assert [e.type for e in events] == ["task.created"]


async def test_advance_changes_status_and_appends_event(
    machine: TaskMachine, repo: TaskRepo
) -> None:
    task = await machine.create(TaskCreate(issue_url=ISSUE))
    moved = await machine.advance(task.id, TaskStatus.PLANNING, payload={"note": "x"})
    assert moved.status is TaskStatus.PLANNING
    last = (await repo.list_events(task_id=task.id))[-1]
    assert last.type == "task.status_changed"
    assert last.payload == {"from": "queued", "to": "planning", "note": "x"}


async def test_illegal_advance_is_refused_and_leaves_no_trace(
    machine: TaskMachine, repo: TaskRepo
) -> None:
    task = await machine.create(TaskCreate(issue_url=ISSUE))
    with pytest.raises(IllegalTransition):
        await machine.advance(task.id, TaskStatus.SOLVING)
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.QUEUED
    assert len(await repo.list_events(task_id=task.id)) == 1


async def test_lost_race_raises_concurrent_transition(
    machine: TaskMachine, repo: TaskRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = await machine.create(TaskCreate(issue_url=ISSUE))
    stale = await repo.get(task.id)  # what we read: still queued
    # Another worker claims it between our read and our write.
    await repo.transition(task.id, expected=TaskStatus.QUEUED, to=TaskStatus.PLANNING)
    monkeypatch.setattr(repo, "get", AsyncMock(return_value=stale))

    with pytest.raises(ConcurrentTransition):
        await machine.advance(task.id, TaskStatus.PLANNING)
    # The compare-and-set refused; the other worker's write stands and no event was forged.
    current = await repo.list_tasks()
    assert current[0].status is TaskStatus.PLANNING
    assert [e.type for e in await repo.list_events(task_id=task.id)] == ["task.created"]


async def test_unknown_task(machine: TaskMachine) -> None:
    with pytest.raises(TaskNotFound):
        await machine.advance("nope", TaskStatus.PLANNING)
