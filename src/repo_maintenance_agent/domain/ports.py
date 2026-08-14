from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from repo_maintenance_agent.domain.models import (
    RepoTaskState,
    SearchHit,
    SearchQuery,
    ToolCall,
    ToolResult,
)


class ToolAdapter(Protocol):
    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult: ...


class SearchPort(Protocol):
    async def search(self, query: SearchQuery) -> list[SearchHit]: ...


class TaskRepository(Protocol):
    async def create(self, state: RepoTaskState) -> RepoTaskState: ...

    async def get(self, tenant_id: str, task_id: str) -> RepoTaskState: ...

    async def list(self, tenant_id: str, *, limit: int = 50) -> list[RepoTaskState]: ...

    async def save(self, state: RepoTaskState, expected_version: int) -> RepoTaskState: ...


class ArtifactStore(Protocol):
    async def put(
        self, tenant_id: str, task_id: str, name: str, content: bytes, media_type: str
    ) -> str: ...

    async def get(self, tenant_id: str, artifact_id: str) -> bytes: ...


class ModelPort(Protocol):
    async def structured(
        self, *, system: str, input_text: str, schema: type[BaseModelLike]
    ) -> BaseModelLike: ...


class BaseModelLike(Protocol):
    @classmethod
    def model_validate(cls, obj: Any) -> BaseModelLike: ...
