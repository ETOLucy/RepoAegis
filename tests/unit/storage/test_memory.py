from pathlib import Path

import pytest

from repo_maintenance_agent.domain.errors import ConcurrentUpdate, ResourceNotFound
from repo_maintenance_agent.domain.models import RepoTaskState
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.storage.memory import InMemoryTaskRepository


def task() -> RepoTaskState:
    return RepoTaskState(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
    )


@pytest.mark.asyncio
async def test_task_repository_hides_cross_tenant_resource() -> None:
    repository = InMemoryTaskRepository()
    await repository.create(task())

    with pytest.raises(ResourceNotFound):
        await repository.get("tenant-b", "task-1")


@pytest.mark.asyncio
async def test_task_repository_uses_optimistic_version() -> None:
    repository = InMemoryTaskRepository()
    stored = await repository.create(task())
    changed = stored.model_copy(update={"version": 1})

    saved = await repository.save(changed, expected_version=0)
    assert saved.version == 1
    with pytest.raises(ConcurrentUpdate):
        await repository.save(changed, expected_version=0)


@pytest.mark.asyncio
async def test_artifact_store_sanitizes_name_and_enforces_tenant(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    artifact_id = await store.put(
        "tenant-a",
        "task-1",
        "../../patch.diff",
        b"diff content",
        "text/x-diff",
    )

    assert await store.get("tenant-a", artifact_id) == b"diff content"
    assert not (tmp_path.parent / "patch.diff").exists()
    with pytest.raises(ResourceNotFound):
        await store.get("tenant-b", artifact_id)
