from __future__ import annotations

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
    )
    report = EvaluationReport(
        case_id=case_id,
        issue_resolution=resolution,
        relevant_file_recall_at_10=0.5,
        mrr=0.25,
        unauthorized_tool_call_rate=unauthorized_rate,
        wall_clock_ms=latency,
        model_calls=2,
        input_tokens=100,
        output_tokens=20,
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
