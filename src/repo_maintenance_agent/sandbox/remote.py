from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.sandbox.docker import SandboxSpec
from repo_maintenance_agent.tools.process import ProcessResult


class _RunnerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    returncode: int
    stdout: str
    stderr: str
    duration_ms: int = Field(ge=0)


class RemoteSandbox:
    def __init__(
        self,
        *,
        base_url: str,
        token: SecretStr,
        workspace_root: Path,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._workspace_root = workspace_root.resolve()
        self._transport = transport

    async def execute(self, spec: SandboxSpec) -> ProcessResult:
        workspace = spec.workspace.resolve()
        if not workspace.is_relative_to(self._workspace_root):
            raise ToolExecutionError("sandbox workspace is outside the assigned root")
        payload = {
            "task_id": spec.task_id,
            "workspace": workspace.relative_to(self._workspace_root).as_posix(),
            "image": spec.image,
            "command": list(spec.command),
            "cpu_limit": spec.cpu_limit,
            "memory_limit": spec.memory_limit,
            "pids_limit": spec.pids_limit,
            "timeout_seconds": spec.timeout_seconds,
            "network_enabled": spec.network_enabled,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                transport=self._transport,
                timeout=spec.timeout_seconds + 10,
            ) as client:
                response = await client.post(
                    "/v1/run",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
                )
                response.raise_for_status()
                result = _RunnerResult.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as error:
            raise ToolExecutionError("sandbox runner request failed") from error
        return ProcessResult(**result.model_dump())
