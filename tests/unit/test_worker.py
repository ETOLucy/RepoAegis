import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from repo_maintenance_agent.domain.models import RepoTaskState, TaskStatus
from repo_maintenance_agent.storage.memory import InMemoryTaskRepository
from repo_maintenance_agent.storage.queue import InMemoryTaskQueue
from repo_maintenance_agent.worker import Worker, WorkerOutcome


def task() -> RepoTaskState:
    return RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
    )


class TransitionExecutor:
    def __init__(self, target: TaskStatus) -> None:
        self._target = target

    async def execute(self, state: RepoTaskState) -> RepoTaskState:
        return state.transition(self._target)


class FailingExecutor:
    async def execute(self, state: RepoTaskState) -> RepoTaskState:
        del state
        raise RuntimeError("temporary infrastructure failure")


class SlowExecutor:
    async def execute(self, state: RepoTaskState) -> RepoTaskState:
        await asyncio.sleep(0.02)
        return state.transition(TaskStatus.INTAKE)


class CountingQueue(InMemoryTaskQueue):
    def __init__(self) -> None:
        super().__init__()
        self.heartbeats = 0

    async def heartbeat(self, lease, *, now):
        self.heartbeats += 1
        return await super().heartbeat(lease, now=now)


@pytest.mark.asyncio
async def test_worker_persists_result_and_acknowledges_successful_work() -> None:
    repository = InMemoryTaskRepository()
    queue = InMemoryTaskQueue()
    now = datetime(2026, 7, 31, tzinfo=UTC)
    created = await repository.create(task())
    await queue.enqueue("tenant-a", created.task_id, now=now)
    worker = Worker(
        worker_id="worker-1",
        tenant_ids=frozenset({"tenant-a"}),
        queue=queue,
        repository=repository,
        executor=TransitionExecutor(TaskStatus.INTAKE),
        clock=lambda: now,
    )

    outcome = await worker.run_once()

    assert outcome is WorkerOutcome.COMPLETED
    assert (await repository.get("tenant-a", created.task_id)).status is TaskStatus.INTAKE
    assert await queue.claim("worker-2", frozenset({"tenant-a"}), now=now) is None


@pytest.mark.asyncio
async def test_worker_parks_task_that_requires_human_approval() -> None:
    repository = InMemoryTaskRepository()
    queue = InMemoryTaskQueue()
    now = datetime(2026, 7, 31, tzinfo=UTC)
    waiting = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
    )
    await repository.create(waiting)
    await queue.enqueue("tenant-a", waiting.task_id, now=now)
    worker = Worker(
        worker_id="worker-1",
        tenant_ids=frozenset({"tenant-a"}),
        queue=queue,
        repository=repository,
        executor=TransitionExecutor(TaskStatus.NEEDS_APPROVAL),
        clock=lambda: now,
    )

    outcome = await worker.run_once()

    assert outcome is WorkerOutcome.AWAITING_APPROVAL
    assert (
        await repository.get("tenant-a", waiting.task_id)
    ).status is TaskStatus.NEEDS_APPROVAL


@pytest.mark.asyncio
async def test_worker_retries_failure_with_bounded_exponential_backoff() -> None:
    repository = InMemoryTaskRepository()
    queue = InMemoryTaskQueue(max_attempts=2)
    now = datetime(2026, 7, 31, tzinfo=UTC)
    created = await repository.create(task())
    await queue.enqueue("tenant-a", created.task_id, now=now)
    worker = Worker(
        worker_id="worker-1",
        tenant_ids=frozenset({"tenant-a"}),
        queue=queue,
        repository=repository,
        executor=FailingExecutor(),
        clock=lambda: now,
        retry_base=timedelta(seconds=5),
        retry_cap=timedelta(seconds=30),
    )

    first = await worker.run_once()
    too_early = await queue.claim(
        "worker-2",
        frozenset({"tenant-a"}),
        now=now + timedelta(seconds=9),
    )
    retry = await queue.claim(
        "worker-2",
        frozenset({"tenant-a"}),
        now=now + timedelta(seconds=10),
    )

    assert first is WorkerOutcome.RETRY_SCHEDULED
    assert too_early is None
    assert retry is not None
    assert retry.attempt == 2


@pytest.mark.asyncio
async def test_worker_renews_lease_during_long_execution_and_acks_latest_token() -> None:
    repository = InMemoryTaskRepository()
    queue = CountingQueue()
    now = datetime(2026, 7, 31, tzinfo=UTC)
    created = await repository.create(task())
    await queue.enqueue("tenant-a", created.task_id, now=now)
    worker = Worker(
        worker_id="worker-1",
        tenant_ids=frozenset({"tenant-a"}),
        queue=queue,
        repository=repository,
        executor=SlowExecutor(),
        clock=lambda: now,
        heartbeat_interval=0.005,
    )

    outcome = await worker.run_once()

    assert outcome is WorkerOutcome.COMPLETED
    assert queue.heartbeats >= 1
    assert await queue.claim("worker-2", frozenset({"tenant-a"}), now=now) is None
