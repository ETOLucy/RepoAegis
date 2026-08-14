from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from repo_maintenance_agent.domain.errors import ConcurrentUpdate, ResourceNotFound
from repo_maintenance_agent.evaluation.models import (
    EvaluationCase,
    EvaluationProvenance,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
)
from repo_maintenance_agent.evaluation.storage import (
    EvaluationRunRow,
    SqlEvaluationRepository,
)
from repo_maintenance_agent.storage.sql import Base


def _repository() -> SqlEvaluationRepository:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[EvaluationRunRow.__table__])
    return SqlEvaluationRepository(engine)


def _run(tenant_id: str = "tenant-a") -> EvaluationRun:
    case = EvaluationCase(
        case_id="case",
        repo_id="owner/repo",
        base_commit="a" * 40,
        gold_files=(),
        hidden_test_commands=(("pytest",),),
    )
    return EvaluationRun(
        tenant_id=tenant_id,
        run_id="run-1",
        suite=EvaluationSuite(
            suite_id="suite",
            name="Suite",
            version="v1",
            cases=(case,),
        ),
        candidate_label="candidate",
        provenance=EvaluationProvenance(
            model="model",
            provider="fixture",
            prompt_version="p1",
            tool_schema_version="t1",
            policy_version="policy1",
            dataset_version="v1",
            environment_fingerprint="test",
            seed=1,
        ),
        created_at=datetime.now(UTC),
    )


async def test_sql_evaluation_repository_round_trip_and_tenant_isolation() -> None:
    repository = _repository()
    created = await repository.create(_run())

    loaded = await repository.get("tenant-a", created.run_id)
    listed = await repository.list("tenant-a", limit=10)

    assert loaded == created
    assert listed == [created]
    with pytest.raises(ResourceNotFound):
        await repository.get("tenant-b", created.run_id)


async def test_sql_evaluation_repository_rejects_stale_update() -> None:
    repository = _repository()
    original = await repository.create(_run())
    running = original.model_copy(
        update={"status": EvaluationRunStatus.RUNNING, "version": 1}
    )
    await repository.save(running, expected_version=0)

    with pytest.raises(ConcurrentUpdate):
        await repository.save(
            running.model_copy(update={"version": 2}),
            expected_version=0,
        )
