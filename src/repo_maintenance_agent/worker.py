from __future__ import annotations

import asyncio
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from repo_maintenance_agent.domain.errors import LeaseConflict
from repo_maintenance_agent.domain.models import RepoTaskState, TaskStatus
from repo_maintenance_agent.domain.ports import TaskRepository
from repo_maintenance_agent.storage.queue import QueueLease


class WorkerOutcome(StrEnum):
    IDLE = "idle"
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"
    LEASE_LOST = "lease_lost"


class TaskQueue(Protocol):
    async def claim(
        self,
        worker_id: str,
        tenant_ids: frozenset[str],
        *,
        now: datetime,
    ) -> QueueLease | None: ...

    async def ack(self, lease: QueueLease) -> None: ...

    async def nack(self, lease: QueueLease, *, retry_at: datetime) -> bool: ...

    async def heartbeat(self, lease: QueueLease, *, now: datetime) -> QueueLease: ...


class TaskExecutor(Protocol):
    async def execute(self, state: RepoTaskState) -> RepoTaskState: ...


class TaskCompletion(Protocol):
    async def complete(
        self,
        lease: QueueLease,
        state: RepoTaskState,
        *,
        expected_version: int,
    ) -> None: ...


@dataclass(slots=True)
class _LeaseState:
    lease: QueueLease
    error: Exception | None = None


class Worker:
    def __init__(
        self,
        *,
        worker_id: str,
        tenant_ids: frozenset[str],
        queue: TaskQueue,
        repository: TaskRepository,
        executor: TaskExecutor,
        completion: TaskCompletion,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        retry_base: timedelta = timedelta(seconds=5),
        retry_cap: timedelta = timedelta(minutes=5),
        heartbeat_interval: float = 60.0,
    ) -> None:
        if not worker_id or not tenant_ids:
            raise ValueError("worker identity and tenant scope are required")
        if retry_base <= timedelta(0) or retry_cap < retry_base:
            raise ValueError("retry durations are invalid")
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be positive")
        self._worker_id = worker_id
        self._tenant_ids = tenant_ids
        self._queue = queue
        self._repository = repository
        self._executor = executor
        self._completion = completion
        self._clock = clock
        self._retry_base = retry_base
        self._retry_cap = retry_cap
        self._heartbeat_interval = heartbeat_interval

    async def run_once(self) -> WorkerOutcome:
        now = self._clock()
        lease = await self._queue.claim(
            self._worker_id,
            self._tenant_ids,
            now=now,
        )
        if lease is None:
            return WorkerOutcome.IDLE
        lease_state = _LeaseState(lease)
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(self._renew_lease(lease_state, stop_heartbeat))
        try:
            task = await self._repository.get(lease.tenant_id, lease.task_id)
            result = await self._executor.execute(task)
            stop_heartbeat.set()
            await heartbeat
            if lease_state.error is not None:
                raise lease_state.error
            await self._completion.complete(
                lease_state.lease,
                result,
                expected_version=task.version,
            )
        except Exception:
            traceback.print_exc()
            stop_heartbeat.set()
            await heartbeat
            if isinstance(lease_state.error, LeaseConflict):
                return WorkerOutcome.LEASE_LOST
            retry_at = now + self._retry_delay(lease_state.lease.attempt)
            try:
                retrying = await self._queue.nack(
                    lease_state.lease,
                    retry_at=retry_at,
                )
            except LeaseConflict:
                return WorkerOutcome.LEASE_LOST
            return WorkerOutcome.RETRY_SCHEDULED if retrying else WorkerOutcome.DEAD_LETTERED
        if result.status is TaskStatus.NEEDS_APPROVAL:
            return WorkerOutcome.AWAITING_APPROVAL
        return WorkerOutcome.COMPLETED

    async def _renew_lease(
        self,
        state: _LeaseState,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._heartbeat_interval,
                )
            except TimeoutError:
                try:
                    state.lease = await self._queue.heartbeat(
                        state.lease,
                        now=self._clock(),
                    )
                except Exception as error:
                    state.error = error
                    stop.set()

    def _retry_delay(self, attempt: int) -> timedelta:
        seconds = min(
            self._retry_base.total_seconds() * (2**attempt),
            self._retry_cap.total_seconds(),
        )
        return timedelta(seconds=seconds)


class LangGraphExecutor:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def execute(self, state: RepoTaskState) -> RepoTaskState:
        result = await self._graph.ainvoke(
            {"task": state, "trace": []},
            config={"configurable": {"thread_id": state.task_id}},
        )
        if not isinstance(result, dict):
            raise RuntimeError("workflow returned an invalid state")
        task = result.get("task")
        if not isinstance(task, RepoTaskState):
            raise RuntimeError("workflow did not return a task")
        return task
