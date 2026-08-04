from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from repo_maintenance_agent.domain.errors import (
    ConcurrentUpdate,
    LeaseConflict,
    ResourceNotFound,
)
from repo_maintenance_agent.domain.models import (
    ApprovalDecision,
    RepoTaskState,
    TaskStatus,
    ToolResult,
)
from repo_maintenance_agent.storage.sql import (
    Base,
    SqlOperationLog,
    SqlTaskCompletion,
    SqlTaskQueue,
    SqlTaskRepository,
)


def task() -> RepoTaskState:
    return RepoTaskState(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
    )


def sqlite_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.mark.asyncio
async def test_sql_repository_round_trip_and_optimistic_lock() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    repository = SqlTaskRepository(engine)

    created = await repository.create(task())
    loaded = await repository.get("tenant-a", created.task_id)
    changed = loaded.model_copy(update={"version": 1})
    saved = await repository.save(changed, expected_version=0)

    assert saved.version == 1
    with pytest.raises(ConcurrentUpdate):
        await repository.save(changed, expected_version=0)
    with pytest.raises(ResourceNotFound):
        await repository.get("tenant-b", created.task_id)


@pytest.mark.asyncio
async def test_sql_queue_reclaims_expired_work_and_rejects_stale_ack() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    queue = SqlTaskQueue(engine, lease_duration=timedelta(seconds=30))
    now = datetime(2026, 7, 31, tzinfo=UTC)
    await queue.enqueue("tenant-a", "task-1", now=now)

    first = await queue.claim("worker-1", frozenset({"tenant-a"}), now=now)
    assert first is not None
    second = await queue.claim(
        "worker-2",
        frozenset({"tenant-a"}),
        now=now + timedelta(seconds=31),
    )

    assert second is not None
    assert second.attempt == 2
    with pytest.raises(LeaseConflict):
        await queue.ack(first)
    await queue.ack(second)


@pytest.mark.asyncio
async def test_sql_task_creation_enqueues_work_in_the_same_transaction() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    repository = SqlTaskRepository(engine)
    queue = SqlTaskQueue(engine)
    now = datetime.now(UTC) + timedelta(seconds=1)

    created = await repository.create(task())
    lease = await queue.claim("worker-1", frozenset({"tenant-a"}), now=now)

    assert lease is not None
    assert lease.task_id == created.task_id


@pytest.mark.asyncio
async def test_sql_approval_state_change_requeues_parked_task_atomically() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    repository = SqlTaskRepository(engine)
    queue = SqlTaskQueue(engine)
    now = datetime.now(UTC) + timedelta(seconds=1)
    created = await repository.create(task())
    lease = await queue.claim("worker-1", frozenset({"tenant-a"}), now=now)
    assert lease is not None
    waiting = (
        created.transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .transition(TaskStatus.NEEDS_APPROVAL)
        .model_copy(update={"plan_hash": "b" * 64})
    )
    await repository.save(waiting, expected_version=created.version)
    await queue.ack(lease)
    approved = waiting.model_copy(
        update={
            "approval": ApprovalDecision(
                approved=True,
                approver="tenant-a",
                plan_hash="b" * 64,
                reason="Reviewed and approved.",
            )
        }
    ).transition(TaskStatus.CODING)

    await repository.save(approved, expected_version=waiting.version)
    resumed = await queue.claim(
        "worker-2",
        frozenset({"tenant-a"}),
        now=now + timedelta(seconds=1),
    )

    assert resumed is not None
    assert resumed.task_id == created.task_id


@pytest.mark.asyncio
async def test_sql_operation_log_survives_reconstruction_and_keeps_first_result() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    first = SqlOperationLog(engine)
    key = "tenant-a:task-1:apply_patch:patch-1"
    original = ToolResult(
        call_id="call-1",
        success=True,
        output={"changed_files": ["src/app.py"]},
    )
    replacement = ToolResult(
        call_id="call-2",
        success=True,
        output={"changed_files": ["src/other.py"]},
    )

    await first.put(key, original)
    reconstructed = SqlOperationLog(engine)
    replay = await reconstructed.get(key)
    await reconstructed.put(key, replacement)
    replay_after_duplicate = await SqlOperationLog(engine).get(key)

    assert replay is not None
    assert replay.replayed
    assert replay.call_id == "call-1"
    assert replay_after_duplicate is not None
    assert replay_after_duplicate.call_id == "call-1"


@pytest.mark.asyncio
async def test_sql_completion_persists_state_and_consumes_lease_atomically() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    repository = SqlTaskRepository(engine)
    queue = SqlTaskQueue(engine)
    completion = SqlTaskCompletion(engine)
    created = await repository.create(task())
    now = datetime.now(UTC) + timedelta(seconds=1)
    lease = await queue.claim("worker-1", frozenset({"tenant-a"}), now=now)
    assert lease is not None
    completed = created.transition(TaskStatus.INTAKE)

    await completion.complete(
        lease,
        completed,
        expected_version=created.version,
    )

    persisted = await repository.get("tenant-a", created.task_id)
    assert persisted.status is TaskStatus.INTAKE
    assert await queue.claim(
        "worker-2",
        frozenset({"tenant-a"}),
        now=now + timedelta(minutes=10),
    ) is None


@pytest.mark.asyncio
async def test_sql_completion_rejects_expired_lease_and_rolls_back_state() -> None:
    engine = sqlite_engine()
    Base.metadata.create_all(engine)
    repository = SqlTaskRepository(engine)
    queue = SqlTaskQueue(engine, lease_duration=timedelta(seconds=1))
    completion = SqlTaskCompletion(engine)
    expired_claim_time = datetime.now(UTC) - timedelta(seconds=10)
    created = await repository.create(
        task().model_copy(
            update={
                "created_at": expired_claim_time - timedelta(seconds=1),
                "updated_at": expired_claim_time - timedelta(seconds=1),
            }
        )
    )
    lease = await queue.claim(
        "worker-1",
        frozenset({"tenant-a"}),
        now=expired_claim_time,
    )
    assert lease is not None

    with pytest.raises(LeaseConflict):
        await completion.complete(
            lease,
            created.transition(TaskStatus.INTAKE),
            expected_version=created.version,
        )

    persisted = await repository.get("tenant-a", created.task_id)
    assert persisted.status is TaskStatus.PENDING
