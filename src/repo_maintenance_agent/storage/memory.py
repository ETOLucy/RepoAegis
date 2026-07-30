from __future__ import annotations

import asyncio

from repo_maintenance_agent.domain.errors import ConcurrentUpdate, ResourceNotFound
from repo_maintenance_agent.domain.models import RepoTaskState


class InMemoryTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], RepoTaskState] = {}
        self._lock = asyncio.Lock()

    async def create(self, state: RepoTaskState) -> RepoTaskState:
        key = (state.tenant_id, state.task_id)
        async with self._lock:
            if key in self._tasks:
                raise ConcurrentUpdate("task already exists")
            self._tasks[key] = state.model_copy(deep=True)
        return state

    async def get(self, tenant_id: str, task_id: str) -> RepoTaskState:
        state = self._tasks.get((tenant_id, task_id))
        if state is None:
            raise ResourceNotFound("task not found")
        return state.model_copy(deep=True)

    async def list(self, tenant_id: str, *, limit: int = 50) -> list[RepoTaskState]:
        if not 1 <= limit <= 200:
            raise ValueError("task list limit must be between 1 and 200")
        states = [state for state in self._tasks.values() if state.tenant_id == tenant_id]
        return [
            state.model_copy(deep=True)
            for state in sorted(
                states,
                key=lambda item: (item.updated_at, item.task_id),
                reverse=True,
            )[:limit]
        ]

    async def save(self, state: RepoTaskState, expected_version: int) -> RepoTaskState:
        key = (state.tenant_id, state.task_id)
        async with self._lock:
            current = self._tasks.get(key)
            if current is None:
                raise ResourceNotFound("task not found")
            if current.version != expected_version or state.version <= expected_version:
                raise ConcurrentUpdate("task version conflict")
            self._tasks[key] = state.model_copy(deep=True)
        return state
