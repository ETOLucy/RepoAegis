from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.domain.models import (
    RepoTaskState,
    ToolCall,
    ToolPermission,
)
from repo_maintenance_agent.tools.gateway import ToolGateway
from repo_maintenance_agent.worker import LangGraphExecutor


class WorkspaceGraphExecutor:
    def __init__(
        self,
        *,
        gateway: ToolGateway,
        workspace_root: Path,
        graph_factory: Callable[[Path], Any],
    ) -> None:
        self._gateway = gateway
        self._workspace_root = workspace_root.resolve()
        self._graph_factory = graph_factory

    async def execute(self, state: RepoTaskState) -> RepoTaskState:
        materialize = ToolCall(
            task_id=state.task_id,
            tenant_id=state.tenant_id,
            repo_id=state.repo_id,
            commit_sha=state.commit_sha,
            agent="control",
            name="workspace_materialize",
            permission=ToolPermission.CONTROL,
            idempotency_key=f"workspace:{state.task_id}:{state.commit_sha}",
        )
        result = await self._gateway.execute(materialize, state)
        if not result.success:
            raise ToolExecutionError("workspace materialization failed")
        workspace = self._resolve_workspace(result.output.get("workspace"))
        branch = result.output.get("branch")
        if not isinstance(branch, str) or not branch:
            raise ToolExecutionError("workspace tool returned an invalid branch")
        prepare = ToolCall(
            task_id=state.task_id,
            tenant_id=state.tenant_id,
            repo_id=state.repo_id,
            commit_sha=state.commit_sha,
            agent="control",
            name="workspace_prepare",
            permission=ToolPermission.CONTROL,
        )
        prepared = await self._gateway.execute(prepare, state)
        if not prepared.success:
            raise ToolExecutionError("workspace preparation failed")
        state = state.model_copy(
            update={"repo_profile": state.repo_profile | {"workspace_branch": branch}}
        )
        graph = self._graph_factory(workspace)
        return await LangGraphExecutor(graph).execute(state)

    def _resolve_workspace(self, value: object) -> Path:
        if not isinstance(value, str) or not value:
            raise ToolExecutionError("workspace tool returned an invalid path")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ToolExecutionError("workspace tool returned an unsafe path")
        workspace = (self._workspace_root / relative).resolve()
        if not workspace.is_relative_to(self._workspace_root) or not workspace.is_dir():
            raise ToolExecutionError("workspace tool returned an unavailable path")
        return workspace
