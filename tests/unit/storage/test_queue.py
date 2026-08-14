from datetime import UTC, datetime, timedelta

import pytest

from repo_maintenance_agent.domain.errors import LeaseConflict
from repo_maintenance_agent.storage.queue import InMemoryTaskQueue


@pytest.mark.asyncio
async def test_queue_is_idempotent_and_enforces_worker_tenant_scope() -> None:
    queue = InMemoryTaskQueue()
    now = datetime(2026, 7, 31, tzinfo=UTC)

    assert await queue.enqueue("tenant-a", "task-1", now=now)
    assert not await queue.enqueue("tenant-a", "task-1", now=now)
    assert await queue.claim("worker-b", frozenset({"tenant-b"}), now=now) is None

    lease = await queue.claim("worker-a", frozenset({"tenant-a"}), now=now)

    assert lease is not None
    assert lease.task_id == "task-1"
    assert lease.attempt == 1


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_but_stale_worker_cannot_ack() -> None:
    queue = InMemoryTaskQueue(lease_duration=timedelta(seconds=30))
    now = datetime(2026, 7, 31, tzinfo=UTC)
    await queue.enqueue("tenant-a", "task-1", now=now)
    first = await queue.claim("worker-1", frozenset({"tenant-a"}), now=now)
    assert first is not None

    assert (
        await queue.claim(
            "worker-2",
            frozenset({"tenant-a"}),
            now=now + timedelta(seconds=29),
        )
        is None
    )
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
    assert (
        await queue.claim(
            "worker-3",
            frozenset({"tenant-a"}),
            now=now + timedelta(minutes=1),
        )
        is None
    )


@pytest.mark.asyncio
async def test_nack_dead_letters_after_maximum_attempts() -> None:
    queue = InMemoryTaskQueue(max_attempts=2)
    now = datetime(2026, 7, 31, tzinfo=UTC)
    await queue.enqueue("tenant-a", "task-1", now=now)

    first = await queue.claim("worker-1", frozenset({"tenant-a"}), now=now)
    assert first is not None
    assert await queue.nack(first, retry_at=now)
    second = await queue.claim("worker-2", frozenset({"tenant-a"}), now=now)
    assert second is not None

    assert not await queue.nack(second, retry_at=now)
    assert await queue.dead_letter_count("tenant-a") == 1
    assert await queue.claim("worker-3", frozenset({"tenant-a"}), now=now) is None


@pytest.mark.asyncio
async def test_heartbeat_extends_only_the_current_lease() -> None:
    queue = InMemoryTaskQueue(lease_duration=timedelta(seconds=30))
    now = datetime(2026, 7, 31, tzinfo=UTC)
    await queue.enqueue("tenant-a", "task-1", now=now)
    lease = await queue.claim("worker-1", frozenset({"tenant-a"}), now=now)
    assert lease is not None

    extended = await queue.heartbeat(lease, now=now + timedelta(seconds=20))

    assert extended.lease_expires_at == now + timedelta(seconds=50)
    with pytest.raises(LeaseConflict):
        await queue.heartbeat(lease, now=now + timedelta(seconds=21))
