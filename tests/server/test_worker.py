from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalStatus, TaskCreate, TaskStatus
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, TaskRepo
from repoaegis.server.worker import Worker


async def test_tick_walks_a_queued_task_to_the_approval_gate(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, gate: ApprovalGate
) -> None:
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/1"))
    worker = Worker(machine, repo, gate, poll_seconds=0)

    assert await worker.tick() is True
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_APPROVAL
    events = await repo.list_events(task_id=task.id)
    assert [e.type for e in events] == [
        "task.created",
        "task.status_changed",
        "task.status_changed",
        "approval.requested",
    ]

    open_gates = await approvals.list_for_task(task.id)
    assert [a.status for a in open_gates] == [ApprovalStatus.PENDING]

    assert await worker.tick() is False  # queue drained
