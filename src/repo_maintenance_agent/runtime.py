from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.evaluation.storage import SqlEvaluationRepository
from repo_maintenance_agent.policies.permissions import PermissionPolicy
from repo_maintenance_agent.runtime_executor import WorkspaceGraphExecutor
from repo_maintenance_agent.storage.artifacts import SqlFileArtifactStore
from repo_maintenance_agent.storage.sql import (
    Base,
    SqlOperationLog,
    SqlTaskCompletion,
    SqlTaskQueue,
    SqlTaskRepository,
)
from repo_maintenance_agent.tools.gateway import ToolGateway
from repo_maintenance_agent.tools.process import ProcessRunner
from repo_maintenance_agent.tools.workspace import WorkspaceAdapter
from repo_maintenance_agent.worker import TaskExecutor


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    engine: Engine
    tasks: SqlTaskRepository
    queue: SqlTaskQueue
    evaluations: SqlEvaluationRepository
    operations: SqlOperationLog
    completion: SqlTaskCompletion
    artifacts: SqlFileArtifactStore
    executor: TaskExecutor | None


def build_runtime(
    settings: Settings,
    *,
    executor: TaskExecutor | None = None,
) -> RuntimeComponents:
    database_url = settings.database_url.get_secret_value()
    _prepare_sqlite_directory(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    Path(settings.artifact_root).mkdir(parents=True, exist_ok=True)
    artifacts = SqlFileArtifactStore(engine, Path(settings.artifact_root))
    return RuntimeComponents(
        engine=engine,
        tasks=SqlTaskRepository(engine),
        queue=SqlTaskQueue(engine),
        evaluations=SqlEvaluationRepository(engine),
        operations=SqlOperationLog(engine),
        completion=SqlTaskCompletion(engine),
        artifacts=artifacts,
        executor=executor,
    )


def build_worker_runtime(
    settings: Settings,
    *,
    graph_factory: Callable[[Path], Any] | None = None,
) -> RuntimeComponents:
    runtime = build_runtime(settings)
    if not settings.repository_locators:
        raise RuntimeError("worker repository locator registry is required")
    workspace_root = Path(settings.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    materialization_gateway = ToolGateway(
        policy=PermissionPolicy(),
        adapters={
            "workspace_materialize": WorkspaceAdapter(
                ProcessRunner(allowed_executables={"git"}),
                repository_locators=settings.repository_locators,
            )
        },
        operation_log=runtime.operations,
        workspace_root=workspace_root,
    )
    if graph_factory is None:
        from repo_maintenance_agent.production_graph import ProductionGraphFactory

        graph_factory = ProductionGraphFactory(
            settings=settings,
            artifacts=runtime.artifacts,
            operations=runtime.operations,
        )
    executor = WorkspaceGraphExecutor(
        gateway=materialization_gateway,
        workspace_root=workspace_root,
        graph_factory=graph_factory,
    )
    return replace(runtime, executor=executor)


def _prepare_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    database = url.database
    if (
        url.drivername.startswith("sqlite")
        and database is not None
        and database not in {"", ":memory:"}
    ):
        Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
