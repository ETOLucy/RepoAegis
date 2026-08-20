from __future__ import annotations

import pytest

from repo_maintenance_agent.evaluation.aggregate import (
    aggregate_results,
    compare_aggregates,
    evaluate_gates,
)
from repo_maintenance_agent.evaluation.models import (
    EvaluationCaseResult,
    EvaluationObservation,
    EvaluationReport,
    FailureCategory,
    ReleaseGates,
)
from repo_maintenance_agent.evaluation.reports import render_markdown_report


def _result(
    case_id: str,
    *,
    resolution: float,
    latency: int,
    unauthorized_rate: float = 0.0,
    regression: bool = False,
    failure: FailureCategory = FailureCategory.NONE,
    tests_passed: int = 0,
    tests_total: int = 0,
    input_cache_hit_tokens: int = 0,
    input_cache_miss_tokens: int = 0,
    ndcg_at_10: float = 0.0,
) -> EvaluationCaseResult:
    total_calls = 10
    denied_calls = int(total_calls * unauthorized_rate)
    observation = EvaluationObservation(
        hidden_tests_passed=resolution == 1.0,
        regression=regression,
        total_tool_calls=total_calls,
        denied_tool_calls=denied_calls,
        wall_clock_ms=latency,
        model_calls=2,
        input_tokens=100,
        output_tokens=20,
        tests_passed=tests_passed,
        tests_total=tests_total,
        input_cache_hit_tokens=input_cache_hit_tokens,
        input_cache_miss_tokens=input_cache_miss_tokens,
    )
    report = EvaluationReport(
        case_id=case_id,
        issue_resolution=resolution,
        relevant_file_recall_at_10=0.5,
        mrr=0.25,
        ndcg_at_10=ndcg_at_10,
        unauthorized_tool_call_rate=unauthorized_rate,
        wall_clock_ms=latency,
        model_calls=2,
        input_tokens=100,
        output_tokens=20,
        tests_passed_ratio=tests_passed / tests_total if tests_total else 0.0,
    )
    return EvaluationCaseResult(
        case_id=case_id,
        attempts=1,
        failure_category=failure,
        observation=observation,
        report=report,
    )


def test_aggregate_comparison_and_release_gates_are_explicit() -> None:
    baseline = aggregate_results(
        (
            _result("a", resolution=1.0, latency=100),
            _result("b", resolution=1.0, latency=300),
        )
    )
    candidate = aggregate_results(
        (
            _result("a", resolution=1.0, latency=200),
            _result("b", resolution=0.0, latency=400, unauthorized_rate=0.1),
        )
    )

    comparison = compare_aggregates(candidate, baseline, baseline_run_id="baseline-1")
    decision = evaluate_gates(
        candidate,
        gates=ReleaseGates(
            resolution_regression_max=0.02,
            unauthorized_tool_call_rate_max=0.0,
        ),
        comparison=comparison,
    )

    assert candidate.resolution_rate == 0.5
    assert candidate.latency_p50_ms == 300
    assert candidate.latency_p95_ms == 400
    assert comparison.resolution_rate_delta == -0.5
    assert decision.passed is False
    assert {check.name for check in decision.checks if not check.passed} == {
        "resolution_rate",
        "resolution_regression",
        "unauthorized_tool_calls",
    }


def test_infrastructure_failure_blocks_release_and_report_is_deterministic() -> None:
    aggregate = aggregate_results(
        (
            _result(
                "infra-case",
                resolution=0.0,
                latency=50,
                failure=FailureCategory.INFRASTRUCTURE,
            ),
        )
    )
    decision = evaluate_gates(
        aggregate,
        gates=ReleaseGates(),
        comparison=None,
    )
    markdown = render_markdown_report(
        run_id="run-1",
        candidate_label="candidate",
        aggregate=aggregate,
        comparison=None,
        decision=decision,
        results=(),
    )

    assert decision.passed is False
    assert decision.checks[-1].name == "terminal_failures"
    assert "# Evaluation Report" in markdown
    assert "run-1" in markdown
    assert "FAIL" in markdown


def test_aggregate_mean_tests_passed_ratio_averages_over_case_count() -> None:
    aggregate = aggregate_results(
        (
            _result("a", resolution=0.5, latency=100, tests_passed=2, tests_total=4),
            EvaluationCaseResult(
                case_id="no-report",
                attempts=1,
                failure_category=FailureCategory.TIMEOUT,
                observation=None,
                report=None,
            ),
        )
    )

    assert aggregate.mean_tests_passed_ratio == pytest.approx(0.25)


def test_aggregate_cache_hit_rate_combines_observations() -> None:
    aggregate = aggregate_results(
        (
            _result(
                "a",
                resolution=0.0,
                latency=100,
                input_cache_hit_tokens=300,
                input_cache_miss_tokens=100,
            ),
            _result(
                "b",
                resolution=0.0,
                latency=200,
                input_cache_hit_tokens=100,
                input_cache_miss_tokens=100,
            ),
        )
    )

    assert aggregate.cache_hit_rate == pytest.approx(400 / 600)


def test_aggregate_cache_hit_rate_is_zero_without_tokens() -> None:
    aggregate = aggregate_results((_result("a", resolution=0.0, latency=100),))

    assert aggregate.cache_hit_rate == 0.0


def test_compare_aggregates_with_paired_scores_computes_resolution_ci() -> None:
    baseline = aggregate_results(
        (
            _result("a", resolution=0.0, latency=100),
            _result("b", resolution=0.0, latency=200),
        )
    )
    candidate = aggregate_results(
        (
            _result("a", resolution=1.0, latency=100),
            _result("b", resolution=1.0, latency=200),
        )
    )

    comparison = compare_aggregates(
        candidate,
        baseline,
        baseline_run_id="baseline-1",
        resolution_scores_candidate=(1.0, 1.0),
        resolution_scores_baseline=(0.0, 0.0),
    )

    assert comparison.resolution_ci_lower is not None
    assert comparison.resolution_ci_upper is not None
    assert comparison.resolution_significant is True
    assert comparison.resolution_direction == "improvement"
    assert comparison.resolution_ci_lower > 0


def test_compare_aggregates_without_paired_scores_keeps_resolution_none() -> None:
    baseline = aggregate_results((_result("a", resolution=1.0, latency=100),))
    candidate = aggregate_results((_result("a", resolution=1.0, latency=100),))

    comparison = compare_aggregates(candidate, baseline, baseline_run_id="baseline-1")

    assert comparison.resolution_ci_lower is None
    assert comparison.resolution_ci_upper is None
    assert comparison.resolution_significant is None
    assert comparison.resolution_direction is None


def test_gate_resolution_statistical_significance_blocks_regression() -> None:
    aggregate = aggregate_results((_result("a", resolution=1.0, latency=100),))
    comparison = compare_aggregates(
        aggregate,
        aggregate,
        baseline_run_id="baseline-1",
        resolution_scores_candidate=(0.0, 0.0, 0.0),
        resolution_scores_baseline=(1.0, 1.0, 1.0),
    )

    decision = evaluate_gates(aggregate, gates=ReleaseGates(), comparison=comparison)
    gate = next(
        check for check in decision.checks if check.name == "resolution_statistical_significance"
    )

    assert gate.passed is False
    assert "regression" in gate.detail.lower()


def test_gate_resolution_statistical_significance_flags_inconclusive() -> None:
    aggregate = aggregate_results((_result("a", resolution=1.0, latency=100),))
    comparison = compare_aggregates(
        aggregate,
        aggregate,
        baseline_run_id="baseline-1",
        resolution_scores_candidate=(1.0, 1.0, 1.0),
        resolution_scores_baseline=(1.0, 1.0, 1.0),
    )

    decision = evaluate_gates(aggregate, gates=ReleaseGates(), comparison=comparison)
    gate = next(
        check for check in decision.checks if check.name == "resolution_statistical_significance"
    )

    assert gate.passed is False
    assert "sample" in gate.detail.lower()


def test_gate_resolution_statistical_significance_passes_improvement() -> None:
    aggregate = aggregate_results((_result("a", resolution=1.0, latency=100),))
    comparison = compare_aggregates(
        aggregate,
        aggregate,
        baseline_run_id="baseline-1",
        resolution_scores_candidate=(1.0, 1.0, 1.0),
        resolution_scores_baseline=(0.0, 0.0, 0.0),
    )

    decision = evaluate_gates(aggregate, gates=ReleaseGates(), comparison=comparison)
    gate = next(
        check for check in decision.checks if check.name == "resolution_statistical_significance"
    )

    assert gate.passed is True


def test_gate_resolution_statistical_significance_passes_without_baseline() -> None:
    aggregate = aggregate_results((_result("a", resolution=1.0, latency=100),))

    decision = evaluate_gates(aggregate, gates=ReleaseGates(), comparison=None)
    gate = next(
        check for check in decision.checks if check.name == "resolution_statistical_significance"
    )

    assert gate.passed is True
    assert "baseline" in gate.detail.lower()


def test_aggregate_mean_ndcg_at_10_averages_over_case_count() -> None:
    aggregate = aggregate_results(
        (
            _result("a", resolution=0.0, latency=100, ndcg_at_10=0.6),
            _result("b", resolution=0.0, latency=200, ndcg_at_10=0.4),
            EvaluationCaseResult(
                case_id="no-report",
                attempts=1,
                failure_category=FailureCategory.TIMEOUT,
                observation=None,
                report=None,
            ),
        )
    )

    assert aggregate.mean_ndcg_at_10 == pytest.approx(1.0 / 3)
