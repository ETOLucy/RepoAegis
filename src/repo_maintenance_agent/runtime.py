from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.evaluation.storage import SqlEvaluationRepository
from repo_maintenance_agent.storage.sql import Base, SqlTaskQueue, SqlTaskRepository
from repo_maintenance_agent.worker import TaskExecutor


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    engine: Engine
    tasks: SqlTaskRepository
    queue: SqlTaskQueue
    evaluations: SqlEvaluationRepository
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
    return RuntimeComponents(
        engine=engine,
        tasks=SqlTaskRepository(engine),
        queue=SqlTaskQueue(engine),
        evaluations=SqlEvaluationRepository(engine),
        executor=executor,
    )


def _prepare_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    database = url.database
    if (
        url.drivername.startswith("sqlite")
        and database is not None
        and database not in {"", ":memory:"}
    ):
        Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
