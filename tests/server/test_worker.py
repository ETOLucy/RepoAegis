from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, Task, TaskCreate, TaskStatus
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, TaskRepo
from repoaegis.server.worker import Worker


class RecordingPlanning:
    """Stands in for the planning service: the worker only decides *which* task."""

    def __init__(self, machine: TaskMachine, gate: ApprovalGate) -> None:
        self._machine = machine
        self._gate = gate
        self.planned: list[str] = []

    async def plan(self, task: Task) -> None:
        self.planned.append(task.id)
        payload = {"plan": {"diagnosis": "d", "locations": [], "approach": "a"}}
        await self._machine.advance(task.id, TaskStatus.AWAITING_APPROVAL, payload=payload)
        await self._gate.request(task.id, ApprovalKind.PLAN, payload, subject="d")


async def test_tick_claims_one_task_and_hands_it_to_planning(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, gate: ApprovalGate
) -> None:
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/1"))
    planning = RecordingPlanning(machine, gate)
    worker = Worker(machine, repo, gate, planning, poll_seconds=0)  # type: ignore[arg-type]

    assert await worker.tick() is True
    assert planning.planned == [task.id]

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_APPROVAL
    assert [e.type for e in await repo.list_events(task_id=task.id)] == [
        "task.created",
        "task.status_changed",
        "task.status_changed",
        "approval.requested",
    ]
    assert len(await approvals.list_for_task(task.id)) == 1

    assert await worker.tick() is False  # queue drained


async def test_tick_does_nothing_when_the_queue_is_empty(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
) -> None:
    planning = RecordingPlanning(machine, gate)
    worker = Worker(machine, repo, gate, planning, poll_seconds=0)  # type: ignore[arg-type]
    assert await worker.tick() is False
    assert planning.planned == []
