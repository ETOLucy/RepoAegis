from repoaegis.server.models import TaskCreate, TaskStatus
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import TaskRepo
from repoaegis.server.worker import Worker


async def test_tick_walks_a_queued_task_to_the_approval_gate(
    machine: TaskMachine, repo: TaskRepo
) -> None:
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/1"))
    worker = Worker(machine, repo, poll_seconds=0)

    assert await worker.tick() is True
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_APPROVAL
    events = await repo.list_events(task_id=task.id)
    assert [e.type for e in events] == [
        "task.created",
        "task.status_changed",
        "task.status_changed",
    ]
    assert "plan" in events[-1].payload

    assert await worker.tick() is False  # queue drained
