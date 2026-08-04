from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from repo_maintenance_agent.sandbox.runner_api import create_sandbox_runner_app
from repo_maintenance_agent.tools.process import ProcessResult


class RecordingSandbox:
    def __init__(self) -> None:
        self.specs = []

    async def execute(self, spec):
        self.specs.append(spec)
        return ProcessResult(returncode=0, stdout="passed", stderr="", duration_ms=8)


@asynccontextmanager
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://sandbox-runner"
    ) as api:
        yield api


def payload() -> dict[str, object]:
    return {
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


@pytest.mark.asyncio
async def test_runner_requires_auth_and_executes_scoped_spec(tmp_path: Path) -> None:
    (tmp_path / "task-1").mkdir()
    sandbox = RecordingSandbox()
    app = create_sandbox_runner_app(
        sandbox=sandbox,
        token=SecretStr("runner-test-token"),
        workspace_root=tmp_path,
    )

    async with client(app) as api:
        denied = await api.post("/v1/run", json=payload())
        accepted = await api.post(
            "/v1/run",
            json=payload(),
            headers={"Authorization": "Bearer runner-test-token"},
        )

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["stdout"] == "passed"
    assert sandbox.specs[0].workspace == (tmp_path / "task-1").resolve()


@pytest.mark.asyncio
async def test_runner_rejects_workspace_escape_before_execution(tmp_path: Path) -> None:
    sandbox = RecordingSandbox()
    app = create_sandbox_runner_app(
        sandbox=sandbox,
        token=SecretStr("runner-test-token"),
        workspace_root=tmp_path,
    )

    async with client(app) as api:
        response = await api.post(
            "/v1/run",
            json=payload() | {"workspace": "../outside"},
            headers={"Authorization": "Bearer runner-test-token"},
        )

    assert response.status_code == 422
    assert sandbox.specs == []
