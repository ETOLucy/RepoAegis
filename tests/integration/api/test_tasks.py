from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from repo_maintenance_agent.api.app import create_app
from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.domain.models import TaskStatus
from repo_maintenance_agent.storage.memory import InMemoryTaskRepository


@asynccontextmanager
async def client(
    repository: InMemoryTaskRepository | None = None,
    *,
    production: bool = False,
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        repository=repository or InMemoryTaskRepository(),
        authenticator=StaticTokenAuthenticator(
            {
                "test-token": Principal(tenant_id="tenant-a", subject="reviewer-a"),
                "other-token": Principal(tenant_id="tenant-b", subject="reviewer-b"),
            }
        ),
        production=production,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as api:
        yield api


def payload() -> dict[str, object]:
    return {
        "repo_id": "owner/repo",
        "commit_sha": "a" * 40,
        "base_branch": "main",
        "issue": {"title": "Fix the bug", "body": "Reproduction"},
    }


@pytest.mark.asyncio
async def test_task_endpoints_require_bearer_authentication() -> None:
    async with client() as api:
        response = await api.post("/v1/tasks", json=payload())

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid or missing credentials"}


@pytest.mark.asyncio
async def test_create_and_read_task_are_tenant_scoped() -> None:
    headers = {"Authorization": "Bearer test-token"}

    async with client() as api:
        created = await api.post("/v1/tasks", json=payload(), headers=headers)
        task_id = created.json()["task_id"]
        loaded = await api.get(f"/v1/tasks/{task_id}", headers=headers)
        hidden = await api.get(
            f"/v1/tasks/{task_id}",
            headers={"Authorization": "Bearer other-token"},
        )

    assert created.status_code == 201
    assert loaded.status_code == 200
    assert hidden.status_code == 404
    assert loaded.json()["repo_id"] == "owner/repo"
    assert "tenant_id" not in loaded.json()
    assert "openai_api_key" not in loaded.text


@pytest.mark.asyncio
async def test_create_rejects_unknown_fields() -> None:
    body = payload() | {"tenant_id": "attacker", "unexpected": True}
    async with client() as api:
        response = await api.post(
            "/v1/tasks",
            json=body,
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_production_disables_interactive_api_docs() -> None:
    async with client(production=True) as api:
        docs = await api.get("/docs")
        schema = await api.get("/openapi.json")

    assert docs.status_code == 404
    assert schema.status_code == 404


@pytest.mark.asyncio
async def test_cancel_task_persists_terminal_state() -> None:
    repository = InMemoryTaskRepository()
    headers = {"Authorization": "Bearer test-token"}
    async with client(repository) as api:
        created = await api.post("/v1/tasks", json=payload(), headers=headers)
        task_id = created.json()["task_id"]
        cancelled = await api.post(f"/v1/tasks/{task_id}/cancel", headers=headers)
        repeated = await api.post(f"/v1/tasks/{task_id}/cancel", headers=headers)

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert repeated.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approved", "expected_status"),
    [(True, TaskStatus.CODING), (False, TaskStatus.FAILED)],
)
async def test_approval_decision_validates_plan_and_advances_state(
    approved: bool,
    expected_status: TaskStatus,
) -> None:
    repository = InMemoryTaskRepository()
    waiting = (
        await repository.create(
            _task_state()
            .transition(TaskStatus.INTAKE)
            .transition(TaskStatus.RESEARCH)
            .transition(TaskStatus.PLANNING)
            .transition(TaskStatus.NEEDS_APPROVAL)
            .model_copy(update={"plan_hash": "b" * 64})
        )
    )
    body = {
        "approved": approved,
        "plan_hash": "b" * 64,
        "reason": "Reviewed against the proposed change scope.",
    }

    async with client(repository) as api:
        response = await api.post(
            f"/v1/tasks/{waiting.task_id}/approval",
            json=body,
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    saved = await repository.get("tenant-a", waiting.task_id)
    assert saved.approval is not None
    assert saved.approval.approved is approved
    assert saved.approval.approver == "reviewer-a"


@pytest.mark.asyncio
async def test_approval_rejects_stale_plan_hash() -> None:
    repository = InMemoryTaskRepository()
    waiting = (
        _task_state()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .transition(TaskStatus.NEEDS_APPROVAL)
        .model_copy(update={"plan_hash": "b" * 64})
    )
    await repository.create(waiting)

    async with client(repository) as api:
        response = await api.post(
            f"/v1/tasks/{waiting.task_id}/approval",
            json={
                "approved": True,
                "plan_hash": "c" * 64,
                "reason": "This decision refers to an older plan.",
            },
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "task state conflict"}


def _task_state():
    from repo_maintenance_agent.domain.models import RepoTaskState

    return RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix the bug", "body": "Reproduction"},
    )
