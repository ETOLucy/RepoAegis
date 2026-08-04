import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.sandbox.docker import SandboxSpec
from repo_maintenance_agent.sandbox.remote import RemoteSandbox


@pytest.mark.asyncio
async def test_remote_sandbox_sends_relative_workspace_and_resource_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "task-1"
    workspace.mkdir()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/run"
        assert request.headers["Authorization"] == "Bearer runner-test-token"
        body = json.loads(request.content)
        assert body == {
            "task_id": "task-1",
            "workspace": "task-1",
            "image": "python@sha256:" + "a" * 64,
            "command": ["python", "-m", "pytest"],
            "cpu_limit": "2",
            "memory_limit": "4g",
            "pids_limit": 256,
            "timeout_seconds": 300,
            "network_enabled": False,
        }
        return httpx.Response(
            200,
            json={"returncode": 0, "stdout": "passed", "stderr": "", "duration_ms": 12},
        )

    sandbox = RemoteSandbox(
        base_url="http://sandbox-runner:8080",
        token=SecretStr("runner-test-token"),
        workspace_root=tmp_path,
        transport=httpx.MockTransport(handler),
    )

    result = await sandbox.execute(
        SandboxSpec(
            task_id="task-1",
            workspace=workspace,
            image="python@sha256:" + "a" * 64,
            command=("python", "-m", "pytest"),
        )
    )

    assert result.returncode == 0
    assert result.stdout == "passed"


@pytest.mark.asyncio
async def test_remote_sandbox_redacts_runner_transport_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "task-1"
    workspace.mkdir()

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, text="private upstream details")

    sandbox = RemoteSandbox(
        base_url="http://private-runner.internal:8080",
        token=SecretStr("runner-test-token"),
        workspace_root=tmp_path,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ToolExecutionError, match="sandbox runner request failed") as captured:
        await sandbox.execute(
            SandboxSpec(
                task_id="task-1",
                workspace=workspace,
                image="python@sha256:" + "a" * 64,
                command=("pytest",),
            )
        )

    assert "private-runner" not in str(captured.value)
    assert "upstream" not in str(captured.value)
