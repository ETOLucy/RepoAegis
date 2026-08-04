from pathlib import Path

from pydantic import SecretStr

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.domain.models import RepoTaskState
from repo_maintenance_agent.runtime import build_runtime


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
