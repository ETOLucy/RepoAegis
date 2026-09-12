import asyncio

from repoaegis.server.events import EventBus
from repoaegis.server.models import TaskCreate, TaskStatus
from repoaegis.server.sse import HEARTBEAT, event_stream
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import TaskRepo

ISSUE = "https://github.com/o/r/issues/1"


async def test_replays_history_then_streams_live(
    machine: TaskMachine, repo: TaskRepo, bus: EventBus
) -> None:
    task = await machine.create(TaskCreate(issue_url=ISSUE))
    stream = event_stream(bus, repo, after_id=0, heartbeat_seconds=60)

    first = await anext(stream)
    assert first.startswith("id: 1\nevent: task.created\n")

    await machine.advance(task.id, TaskStatus.PLANNING)
    second = await asyncio.wait_for(anext(stream), 1)
    assert "event: task.status_changed" in second
    await stream.aclose()


async def test_last_event_id_skips_what_the_client_already_saw(
    machine: TaskMachine, repo: TaskRepo, bus: EventBus
) -> None:
    task = await machine.create(TaskCreate(issue_url=ISSUE))
    await machine.advance(task.id, TaskStatus.PLANNING)
    stream = event_stream(bus, repo, after_id=1, heartbeat_seconds=60)
    chunk = await anext(stream)
    assert chunk.startswith("id: 2\n")
    await stream.aclose()


async def test_heartbeat_when_idle(repo: TaskRepo, bus: EventBus) -> None:
    stream = event_stream(bus, repo, heartbeat_seconds=0.01)
    assert await anext(stream) == HEARTBEAT
    await stream.aclose()
