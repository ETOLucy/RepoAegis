from __future__ import annotations

import asyncio
from collections import defaultdict

from repo_maintenance_agent.evaluation.harness import (
    EvaluationHarness,
    InfrastructureFailure,
)
from repo_maintenance_agent.evaluation.models import (
    EvaluationCase,
    EvaluationObservation,
    EvaluationProvenance,
    EvaluationRunStatus,
    EvaluationSuite,
    FailureCategory,
)


def _case(case_id: str) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        repo_id="owner/repository",
        base_commit="a" * 40,
        gold_files=(f"src/{case_id}.py",),
        hidden_test_commands=(("pytest", case_id),),
        timeout_seconds=2,
    )


def _suite(*case_ids: str, concurrency: int = 2, max_attempts: int = 2) -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="suite",
        name="Regression suite",
        version="v1",
        cases=tuple(_case(case_id) for case_id in case_ids),
        concurrency=concurrency,
        max_attempts=max_attempts,
    )


def _provenance() -> EvaluationProvenance:
    return EvaluationProvenance(
        model="deterministic",
        provider="fixture",
        prompt_version="p1",
        tool_schema_version="t1",
        policy_version="policy1",
        dataset_version="v1",
        environment_fingerprint="test-platform",
        seed=11,
    )


class TrackingExecutor:
    def __init__(self, failures: dict[str, int] | None = None) -> None:
        self.active = 0
        self.max_active = 0
        self.attempts: defaultdict[str, int] = defaultdict(int)
        self.failures = failures or {}

    async def execute(
        self,
        case: EvaluationCase,
        *,
        seed: int,
    ) -> EvaluationObservation:
        del seed
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.attempts[case.case_id] += 1
        try:
            await asyncio.sleep(0.01)
            if self.attempts[case.case_id] <= self.failures.get(case.case_id, 0):
                raise InfrastructureFailure("fixture infrastructure unavailable")
            return EvaluationObservation(
                retrieved_files=case.gold_files,
                hidden_tests_passed=True,
                wall_clock_ms=10,
                model_calls=1,
                input_tokens=20,
                output_tokens=5,
            )
        finally:
            self.active -= 1


async def test_harness_bounds_concurrency_retries_and_preserves_case_order() -> None:
    executor = TrackingExecutor(failures={"case-b": 1})
    harness = EvaluationHarness(executor)

    run = await harness.run(
        tenant_id="tenant-a",
        suite=_suite("case-a", "case-b", "case-c", concurrency=2),
        candidate_label="candidate",
        provenance=_provenance(),
    )

    assert run.status is EvaluationRunStatus.COMPLETED
    assert [result.case_id for result in run.results] == [
        "case-a",
        "case-b",
        "case-c",
    ]
    assert executor.max_active == 2
    assert executor.attempts["case-b"] == 2
    assert run.results[1].attempts == 2
    assert run.aggregate is not None
    assert run.aggregate.resolution_rate == 1.0
    assert run.gate_decision is not None
    assert run.gate_decision.passed is True


async def test_harness_classifies_terminal_infrastructure_failure() -> None:
    executor = TrackingExecutor(failures={"broken": 5})
    harness = EvaluationHarness(executor)

    run = await harness.run(
        tenant_id="tenant-a",
        suite=_suite("broken", max_attempts=2),
        candidate_label="candidate",
        provenance=_provenance(),
    )

    result = run.results[0]
    assert result.failure_category is FailureCategory.INFRASTRUCTURE
    assert result.attempts == 2
    assert result.error_summary == "fixture infrastructure unavailable"
    assert run.status is EvaluationRunStatus.FAILED
    assert run.gate_decision is not None
    assert run.gate_decision.passed is False


async def test_harness_redacts_secrets_from_persisted_error_summaries() -> None:
    class SecretFailureExecutor:
        async def execute(
            self,
            case: EvaluationCase,
            *,
            seed: int,
        ) -> EvaluationObservation:
            del case, seed
            raise RuntimeError(
                "provider rejected sk-exampleSecret123 and Bearer private-token-value"
            )

    run = await EvaluationHarness(SecretFailureExecutor()).run(
        tenant_id="tenant-a",
        suite=_suite("broken"),
        candidate_label="candidate",
        provenance=_provenance(),
    )

    summary = run.results[0].error_summary
    assert summary == "provider rejected [REDACTED] and Bearer [REDACTED]"
    assert "exampleSecret123" not in summary
    assert "private-token-value" not in summary


async def test_replay_creates_a_new_run_with_selected_failed_cases() -> None:
    failing = EvaluationHarness(TrackingExecutor(failures={"bad": 5}))
    source = await failing.run(
        tenant_id="tenant-a",
        suite=_suite("good", "bad"),
        candidate_label="candidate",
        provenance=_provenance(),
    )
    replay_harness = EvaluationHarness(TrackingExecutor())

    replay = await replay_harness.replay(source, case_ids=("bad",))

    assert replay.run_id != source.run_id
    assert replay.replay_of_run_id == source.run_id
    assert replay.selected_case_ids == ("bad",)
    assert [result.case_id for result in replay.results] == ["bad"]
    assert replay.candidate_label == "candidate / replay"
