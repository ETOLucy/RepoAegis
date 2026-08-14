from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from repo_maintenance_agent.api.app import create_app
from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.domain.models import Evidence, RiskLevel, TaskStatus, ToolPermission
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
async def test_task_list_is_tenant_scoped_and_bounded() -> None:
    headers = {"Authorization": "Bearer test-token"}
    async with client() as api:
        created = await api.post("/v1/tasks", json=payload(), headers=headers)
        listed = await api.get("/v1/tasks?limit=1", headers=headers)
        other = await api.get(
            "/v1/tasks?limit=1",
            headers={"Authorization": "Bearer other-token"},
        )

    assert listed.status_code == 200
    assert listed.json()["items"][0]["task_id"] == created.json()["task_id"]
    assert other.json() == {"items": []}


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
        "target_commit": "a" * 40,
        "allowed_tools": [],
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
                "target_commit": "a" * 40,
                "allowed_tools": [],
                "reason": "This decision refers to an older plan.",
            },
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "task state conflict"}


@pytest.mark.asyncio
async def test_approval_rejects_mismatched_target_or_tool_scope() -> None:
    repository = InMemoryTaskRepository()
    waiting = (
        _task_state()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .transition(TaskStatus.NEEDS_APPROVAL)
        .model_copy(
            update={
                "plan_hash": "b" * 64,
                "allowed_tools": (ToolPermission.REPO_READ, ToolPermission.GIT_WRITE),
            }
        )
    )
    await repository.create(waiting)
    body = {
        "approved": True,
        "plan_hash": "b" * 64,
        "target_commit": "c" * 40,
        "allowed_tools": ["repo_read", "git_write"],
        "reason": "Reviewed the complete envelope.",
    }

    async with client(repository) as api:
        wrong_commit = await api.post(
            f"/v1/tasks/{waiting.task_id}/approval",
            json=body,
            headers={"Authorization": "Bearer test-token"},
        )
        wrong_tools = await api.post(
            f"/v1/tasks/{waiting.task_id}/approval",
            json=body | {"target_commit": "a" * 40, "allowed_tools": ["repo_read"]},
            headers={"Authorization": "Bearer test-token"},
        )

    assert wrong_commit.status_code == 409
    assert wrong_tools.status_code == 409


@pytest.mark.asyncio
async def test_task_response_exposes_reviewable_approval_envelope() -> None:
    repository = InMemoryTaskRepository()
    waiting = (
        _task_state()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .transition(TaskStatus.NEEDS_APPROVAL)
        .model_copy(
            update={
                "plan": ({"description": "Update CI", "paths": [".github/workflows/ci.yml"]},),
                "risk": RiskLevel.HIGH,
                "risk_reasons": ("CI configuration: .github/workflows/ci.yml",),
                "plan_hash": "b" * 64,
                "declared_files": (".github/workflows/ci.yml",),
                "allowed_tools": (ToolPermission.REPO_READ, ToolPermission.GIT_WRITE),
                "verification_plan": ("pytest tests/test_ci.py",),
                "evidence": (
                    Evidence(source="bm25", locator="ci.py:1-8", summary="CI helper"),
                ),
            }
        )
    )
    await repository.create(waiting)

    async with client(repository) as api:
        response = await api.get(
            f"/v1/tasks/{waiting.task_id}",
            headers={"Authorization": "Bearer test-token"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["plan"] == [{"description": "Update CI", "paths": [".github/workflows/ci.yml"]}]
    assert body["risk"] == "high"
    assert body["risk_reasons"] == ["CI configuration: .github/workflows/ci.yml"]
    assert body["plan_hash"] == "b" * 64
    assert body["declared_files"] == [".github/workflows/ci.yml"]
    assert body["allowed_tools"] == ["repo_read", "git_write"]
    assert body["verification_plan"] == ["pytest tests/test_ci.py"]
    assert body["evidence_summary"] == [
        {"source": "bm25", "locator": "ci.py:1-8", "summary": "CI helper"}
    ]


def _task_state():
    from repo_maintenance_agent.domain.models import RepoTaskState

    return RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix the bug", "body": "Reproduction"},
    )
