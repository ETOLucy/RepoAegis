from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from repo_maintenance_agent.domain.errors import LeaseConflict


class QueueLease(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    task_id: str = Field(min_length=1, max_length=64)
    worker_id: str = Field(min_length=1, max_length=128)
    lease_id: str
    attempt: int = Field(ge=1)
    lease_expires_at: datetime


@dataclass(slots=True)
class _QueueEntry:
    tenant_id: str
    task_id: str
    available_at: datetime
    attempts: int = 0
    lease: QueueLease | None = None
    dead_lettered: bool = False


class InMemoryTaskQueue:
    def __init__(
        self,
        *,
        lease_duration: timedelta = timedelta(minutes=5),
        max_attempts: int = 3,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._lease_duration = lease_duration
        self._max_attempts = max_attempts
        self._entries: dict[tuple[str, str], _QueueEntry] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, tenant_id: str, task_id: str, *, now: datetime) -> bool:
        key = (tenant_id, task_id)
        async with self._lock:
            if key in self._entries:
                return False
            self._entries[key] = _QueueEntry(tenant_id, task_id, _aware(now))
            return True

    async def claim(
        self,
        worker_id: str,
        tenant_ids: frozenset[str],
        *,
        now: datetime,
    ) -> QueueLease | None:
        current_time = _aware(now)
        async with self._lock:
            candidates = (
                entry
                for entry in self._entries.values()
                if entry.tenant_id in tenant_ids
                and not entry.dead_lettered
                and entry.attempts < self._max_attempts
                and entry.available_at <= current_time
                and (entry.lease is None or entry.lease.lease_expires_at <= current_time)
            )
            entry = min(
                candidates,
                key=lambda item: (item.available_at, item.task_id),
                default=None,
            )
            if entry is None:
                return None
            entry.attempts += 1
            entry.lease = _new_lease(
                entry,
                worker_id,
                current_time + self._lease_duration,
            )
            return entry.lease

    async def heartbeat(self, lease: QueueLease, *, now: datetime) -> QueueLease:
        async with self._lock:
            entry = self._current_entry(lease)
            current_time = _aware(now)
            if lease.lease_expires_at <= current_time:
                raise LeaseConflict("queue lease expired")
            entry.lease = _new_lease(
                entry,
                lease.worker_id,
                current_time + self._lease_duration,
            )
            return entry.lease

    async def ack(self, lease: QueueLease) -> None:
        async with self._lock:
            self._current_entry(lease)
            del self._entries[(lease.tenant_id, lease.task_id)]

    async def nack(self, lease: QueueLease, *, retry_at: datetime) -> bool:
        async with self._lock:
            entry = self._current_entry(lease)
            entry.lease = None
            if entry.attempts >= self._max_attempts:
                entry.dead_lettered = True
                return False
            entry.available_at = _aware(retry_at)
            return True

    async def dead_letter_count(self, tenant_id: str) -> int:
        async with self._lock:
            return sum(
                entry.dead_lettered
                for entry in self._entries.values()
                if entry.tenant_id == tenant_id
            )

    def _current_entry(self, lease: QueueLease) -> _QueueEntry:
        entry = self._entries.get((lease.tenant_id, lease.task_id))
        if entry is None or entry.lease is None or entry.lease.lease_id != lease.lease_id:
            raise LeaseConflict("queue lease is stale")
        return entry


def _new_lease(
    entry: _QueueEntry,
    worker_id: str,
    expires_at: datetime,
) -> QueueLease:
    return QueueLease(
        tenant_id=entry.tenant_id,
        task_id=entry.task_id,
        worker_id=worker_id,
        lease_id=str(uuid4()),
        attempt=entry.attempts,
        lease_expires_at=expires_at,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("queue timestamps must include a timezone")
    return value.astimezone(UTC)
