from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from repo_maintenance_agent.api.app import create_app
from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.runtime import build_runtime


@pytest.mark.asyncio
async def test_api_submission_is_claimable_from_the_shared_runtime_queue(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime.db"
    runtime = build_runtime(
        Settings(
            environment="test",
            database_url=SecretStr(f"sqlite+pysqlite:///{database.as_posix()}"),
            artifact_root=str(tmp_path / "artifacts"),
        )
    )
    app = create_app(
        repository=runtime.tasks,
        evaluation_repository=runtime.evaluations,
        authenticator=StaticTokenAuthenticator(
            {"test-token": Principal(tenant_id="tenant-a", subject="operator-a")}
        ),
        production=False,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as api:
        response = await api.post(
            "/v1/tasks",
            headers={"Authorization": "Bearer test-token"},
            json={
                "repo_id": "owner/repo",
                "commit_sha": "a" * 40,
                "base_branch": "main",
                "issue": {"title": "Fix the bug", "body": "Reproduction"},
            },
        )

    assert response.status_code == 201
    lease = await runtime.queue.claim(
        "worker-a",
        frozenset({"tenant-a"}),
        now=datetime.now(UTC),
    )
    assert lease is not None
    assert lease.task_id == response.json()["task_id"]
