from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from time import monotonic
from typing import Protocol

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.domain.models import RepoTaskState, ToolCall, ToolResult
from repo_maintenance_agent.domain.ports import ToolAdapter
from repo_maintenance_agent.policies.permissions import PermissionPolicy
from repo_maintenance_agent.policies.redaction import Redactor


class InMemoryOperationLog:
    def __init__(self) -> None:
        self._results: dict[str, ToolResult] = {}

    async def get(self, key: str) -> ToolResult | None:
        result = self._results.get(key)
        return result.model_copy(update={"replayed": True}) if result is not None else None

    async def put(self, key: str, result: ToolResult) -> None:
        self._results.setdefault(key, result)


class OperationLog(Protocol):
    async def get(self, key: str) -> ToolResult | None: ...

    async def put(self, key: str, result: ToolResult) -> None: ...


class ToolGateway:
    def __init__(
        self,
        *,
        policy: PermissionPolicy,
        adapters: Mapping[str, ToolAdapter],
        operation_log: OperationLog,
        workspace_root: Path,
        redactor: Redactor | None = None,
    ) -> None:
        self._policy = policy
        self._adapters = dict(adapters)
        self._operation_log = operation_log
        self._workspace_root = workspace_root.resolve()
        self._redactor = redactor or Redactor()

    async def execute(self, call: ToolCall, state: RepoTaskState) -> ToolResult:
        self._policy.authorize(call, state, self._workspace_root)

        if call.idempotency_key:
            replay = await self._operation_log.get(self._operation_key(call))
            if replay is not None:
                return replay

        adapter = self._adapters.get(call.name)
        if adapter is None:
            raise ToolExecutionError(f"tool is not registered: {call.name}")

        started = monotonic()
        try:
            raw = await adapter.execute(call, self._workspace_root)
        except TimeoutError:
            return ToolResult(
                call_id=call.call_id,
                success=False,
                error_code="tool_timeout",
                duration_ms=int((monotonic() - started) * 1000),
            )

        result = raw.model_copy(
            update={
                "output": self._redactor.redact(raw.output),
                "duration_ms": int((monotonic() - started) * 1000),
            }
        )
        if call.idempotency_key and result.success:
            await self._operation_log.put(self._operation_key(call), result)
        return result

    @staticmethod
    def _operation_key(call: ToolCall) -> str:
        return f"{call.tenant_id}:{call.task_id}:{call.name}:{call.idempotency_key}"

