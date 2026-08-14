from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from repo_maintenance_agent.evaluation.aggregate import (
    aggregate_results,
    compare_aggregates,
    evaluate_gates,
)
from repo_maintenance_agent.evaluation.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationObservation,
    EvaluationProvenance,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
    FailureCategory,
)
from repo_maintenance_agent.evaluation.runner import grade_case
from repo_maintenance_agent.policies.redaction import Redactor

_REDACTOR = Redactor()


class InfrastructureFailure(RuntimeError):
    pass


class PolicyFailure(RuntimeError):
    pass


class InvalidOutputFailure(RuntimeError):
    pass


class CaseExecutor(Protocol):
    async def execute(
        self,
        case: EvaluationCase,
        *,
        seed: int,
    ) -> EvaluationObservation: ...


class ObservationExecutor:
    def __init__(self, observations: dict[str, EvaluationObservation]) -> None:
        self._observations = observations

    async def execute(
        self,
        case: EvaluationCase,
        *,
        seed: int,
    ) -> EvaluationObservation:
        del seed
        observation = self._observations.get(case.case_id)
        if observation is None:
            raise InvalidOutputFailure("observation is unavailable for evaluation")
        return observation


class EvaluationHarness:
    def __init__(
        self,
        executor: CaseExecutor,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._executor = executor
        self._clock = clock

    async def run(
        self,
        *,
        tenant_id: str,
        suite: EvaluationSuite,
        candidate_label: str,
        provenance: EvaluationProvenance,
        baseline: EvaluationRun | None = None,
        selected_case_ids: tuple[str, ...] = (),
        replay_of_run_id: str | None = None,
    ) -> EvaluationRun:
        cases = _select_cases(suite, selected_case_ids)
        started_at = self._clock()
        run = EvaluationRun(
            tenant_id=tenant_id,
            suite=suite,
            candidate_label=candidate_label,
            provenance=provenance,
            status=EvaluationRunStatus.RUNNING,
            baseline_run_id=baseline.run_id if baseline is not None else None,
            replay_of_run_id=replay_of_run_id,
            selected_case_ids=selected_case_ids,
            started_at=started_at,
        )
        semaphore = asyncio.Semaphore(suite.concurrency)

        async def execute_indexed(
            index: int,
            case: EvaluationCase,
        ) -> tuple[int, EvaluationCaseResult]:
            async with semaphore:
                result = await self._execute_case(
                    case,
                    seed=provenance.seed + index,
                    max_attempts=suite.max_attempts,
                )
            return index, result

        indexed = await asyncio.gather(
            *(execute_indexed(index, case) for index, case in enumerate(cases))
        )
        results = tuple(result for _, result in sorted(indexed))
        aggregate = aggregate_results(results)
        comparison = (
            compare_aggregates(
                aggregate,
                baseline.aggregate,
                baseline_run_id=baseline.run_id,
            )
            if baseline is not None and baseline.aggregate is not None
            else None
        )
        decision = evaluate_gates(
            aggregate,
            gates=suite.gates,
            comparison=comparison,
            privacy_findings=run.privacy_findings,
        )
        status = (
            EvaluationRunStatus.FAILED
            if aggregate.terminal_failure_count
            else EvaluationRunStatus.COMPLETED
        )
        return run.model_copy(
            update={
                "status": status,
                "results": results,
                "aggregate": aggregate,
                "comparison": comparison,
                "gate_decision": decision,
                "completed_at": self._clock(),
                "version": 1,
            }
        )

    async def replay(
        self,
        source: EvaluationRun,
        *,
        case_ids: tuple[str, ...] | None = None,
    ) -> EvaluationRun:
        selected = case_ids or tuple(
            result.case_id
            for result in source.results
            if result.failure_category is not FailureCategory.NONE
            or result.report is None
            or result.report.issue_resolution < 1
        )
        if not selected:
            raise ValueError("replay requires at least one failed or selected case")
        return await self.run(
            tenant_id=source.tenant_id,
            suite=source.suite,
            candidate_label=f"{source.candidate_label} / replay",
            provenance=source.provenance,
            selected_case_ids=selected,
            replay_of_run_id=source.run_id,
        )

    async def _execute_case(
        self,
        case: EvaluationCase,
        *,
        seed: int,
        max_attempts: int,
    ) -> EvaluationCaseResult:
        started_at = self._clock()
        for attempt in range(1, max_attempts + 1):
            try:
                observation = await asyncio.wait_for(
                    self._executor.execute(case, seed=seed),
                    timeout=case.timeout_seconds,
                )
                report = grade_case(
                    case,
                    retrieved_files=list(observation.retrieved_files),
                    hidden_tests_passed=observation.hidden_tests_passed,
                    regression=observation.regression,
                    total_tool_calls=observation.total_tool_calls,
                    denied_tool_calls=observation.denied_tool_calls,
                    wall_clock_ms=observation.wall_clock_ms,
                    model_calls=observation.model_calls,
                    input_tokens=observation.input_tokens,
                    output_tokens=observation.output_tokens,
                )
                return EvaluationCaseResult(
                    case_id=case.case_id,
                    attempts=attempt,
                    observation=observation,
                    report=report,
                    started_at=started_at,
                    completed_at=self._clock(),
                )
            except TimeoutError as error:
                category = FailureCategory.TIMEOUT
                summary = "case execution timed out"
                retryable = True
                last_error: Exception = error
            except InfrastructureFailure as error:
                category = FailureCategory.INFRASTRUCTURE
                summary = _bounded_summary(error)
                retryable = True
                last_error = error
            except PolicyFailure as error:
                category = FailureCategory.POLICY
                summary = _bounded_summary(error)
                retryable = False
                last_error = error
            except InvalidOutputFailure as error:
                category = FailureCategory.INVALID_OUTPUT
                summary = _bounded_summary(error)
                retryable = False
                last_error = error
            except Exception as error:
                category = FailureCategory.EXECUTION
                summary = _bounded_summary(error)
                retryable = False
                last_error = error

            if not retryable or attempt == max_attempts:
                del last_error
                return EvaluationCaseResult(
                    case_id=case.case_id,
                    attempts=attempt,
                    failure_category=category,
                    error_summary=summary,
                    started_at=started_at,
                    completed_at=self._clock(),
                )
        raise AssertionError("evaluation attempt loop did not return")


def _select_cases(
    suite: EvaluationSuite,
    selected_case_ids: tuple[str, ...],
) -> tuple[EvaluationCase, ...]:
    if not selected_case_ids:
        return suite.cases
    requested = set(selected_case_ids)
    unknown = requested - set(suite.case_ids)
    if unknown:
        raise ValueError(f"unknown evaluation case IDs: {', '.join(sorted(unknown))}")
    return tuple(case for case in suite.cases if case.case_id in requested)


def _bounded_summary(error: Exception) -> str:
    text = str(error).strip() or type(error).__name__
    return str(_REDACTOR.redact(text))[:2_000]
