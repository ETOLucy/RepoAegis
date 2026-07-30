from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from repo_maintenance_agent.domain.errors import ConcurrentUpdate, ResourceNotFound
from repo_maintenance_agent.evaluation.models import (
    EvaluationCase,
    EvaluationProvenance,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
)
from repo_maintenance_agent.evaluation.storage import InMemoryEvaluationRepository


def _run(tenant_id: str, run_id: str, created_at: datetime) -> EvaluationRun:
    case = EvaluationCase(
        case_id="case",
        repo_id="owner/repo",
        base_commit="a" * 40,
        gold_files=(),
        hidden_test_commands=(("pytest",),),
    )
    return EvaluationRun(
        tenant_id=tenant_id,
        run_id=run_id,
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
        created_at=created_at,
    )


async def test_in_memory_evaluation_repository_is_tenant_scoped_and_ordered() -> None:
    repository = InMemoryEvaluationRepository()
    now = datetime.now(UTC)
    await repository.create(_run("tenant-a", "older", now - timedelta(minutes=1)))
    await repository.create(_run("tenant-a", "newer", now))
    await repository.create(_run("tenant-b", "other", now + timedelta(minutes=1)))

    listed = await repository.list("tenant-a", limit=1)

    assert [run.run_id for run in listed] == ["newer"]
    with pytest.raises(ResourceNotFound):
        await repository.get("tenant-b", "newer")


async def test_in_memory_evaluation_repository_uses_optimistic_versions() -> None:
    repository = InMemoryEvaluationRepository()
    original = await repository.create(
        _run("tenant-a", "run-1", datetime.now(UTC))
    )
    updated = original.model_copy(
        update={"status": EvaluationRunStatus.RUNNING, "version": 1}
    )

    saved = await repository.save(updated, expected_version=0)

    assert saved.status is EvaluationRunStatus.RUNNING
    with pytest.raises(ConcurrentUpdate):
        await repository.save(
            saved.model_copy(update={"version": 2}),
            expected_version=0,
        )
