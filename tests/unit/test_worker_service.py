import asyncio
from pathlib import Path

import pytest
from pydantic import SecretStr

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.domain.models import RepoTaskState, TaskStatus
from repo_maintenance_agent.runtime import build_runtime
from repo_maintenance_agent.worker import WorkerOutcome
from repo_maintenance_agent.worker_service import run_worker_forever, run_worker_once


class IntakeExecutor:
    async def execute(self, state: RepoTaskState) -> RepoTaskState:
        return state.transition(TaskStatus.INTAKE)


@pytest.mark.asyncio
async def test_worker_service_claims_executes_and_persists_sql_task(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="test",
        database_url=SecretStr(
            f"sqlite+pysqlite:///{(tmp_path / 'runtime.db').as_posix()}"
        ),
        artifact_root=str(tmp_path / "artifacts"),
        worker_id="worker-a",
        worker_tenant_ids=("tenant-a",),
    )
    runtime = build_runtime(settings, executor=IntakeExecutor())
    task = await runtime.tasks.create(
        RepoTaskState(
            tenant_id="tenant-a",
            repo_id="owner/repo",
            commit_sha="a" * 40,
            base_branch="main",
            issue={"title": "Fix the bug", "body": "Reproduction"},
        )
    )

    outcome = await run_worker_once(settings, runtime=runtime)

    assert outcome is WorkerOutcome.COMPLETED
    persisted = await runtime.tasks.get("tenant-a", task.task_id)
    assert persisted.status is TaskStatus.INTAKE


@pytest.mark.asyncio
async def test_worker_loop_stops_while_queue_is_idle(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url=SecretStr(
            f"sqlite+pysqlite:///{(tmp_path / 'runtime.db').as_posix()}"
        ),
        artifact_root=str(tmp_path / "artifacts"),
        worker_id="worker-a",
        worker_tenant_ids=("tenant-a",),
        worker_poll_seconds=0.01,
    )
    runtime = build_runtime(settings, executor=IntakeExecutor())
    stop = asyncio.Event()

    running = asyncio.create_task(
        run_worker_forever(settings, runtime=runtime, stop=stop)
    )
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(running, timeout=1)
