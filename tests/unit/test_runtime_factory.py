from pathlib import Path

from pydantic import SecretStr

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.domain.models import RepoTaskState
from repo_maintenance_agent.runtime import build_runtime, build_worker_runtime
from repo_maintenance_agent.runtime_executor import WorkspaceGraphExecutor
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.storage.sql import SqlOperationLog


class DeterministicExecutor:
    async def execute(self, state: RepoTaskState) -> RepoTaskState:
        return state


def test_runtime_exposes_the_injected_worker_executor(tmp_path: Path) -> None:
    executor = DeterministicExecutor()
    runtime = build_runtime(
        Settings(
            environment="test",
            database_url=SecretStr(
                f"sqlite+pysqlite:///{(tmp_path / 'runtime.db').as_posix()}"
            ),
            artifact_root=str(tmp_path / "artifacts"),
        ),
        executor=executor,
    )

    assert runtime.executor is executor
    assert isinstance(runtime.operations, SqlOperationLog)


def test_worker_runtime_assembles_workspace_executor_without_model_call(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="test",
        database_url=SecretStr(
            f"sqlite+pysqlite:///{(tmp_path / 'runtime.db').as_posix()}"
        ),
        artifact_root=str(tmp_path / "artifacts"),
        workspace_root=str(tmp_path / "workspaces"),
        repository_locators={"owner/repo": str(tmp_path / "remote.git")},
        worker_tenant_ids=("tenant-a",),
    )

    runtime = build_worker_runtime(settings, graph_factory=lambda workspace: object())

    assert isinstance(runtime.executor, WorkspaceGraphExecutor)
    assert isinstance(runtime.artifacts, FileArtifactStore)
