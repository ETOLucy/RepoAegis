from __future__ import annotations

from math import ceil
from statistics import median

from repo_maintenance_agent.evaluation.models import (
    EvaluationAggregate,
    EvaluationCaseResult,
    EvaluationComparison,
    EvaluationObservation,
    FailureCategory,
    GateCheck,
    GateDecision,
    ReleaseGates,
)
from repo_maintenance_agent.evaluation.significance import paired_bootstrap_delta


def aggregate_results(
    results: tuple[EvaluationCaseResult, ...],
) -> EvaluationAggregate:
    reports = [result.report for result in results if result.report is not None]
    observations = [
        result.observation for result in results if result.observation is not None
    ]
    case_count = len(results)
    latencies = sorted(report.wall_clock_ms for report in reports)
    total_calls = sum(item.total_tool_calls for item in observations)
    denied_calls = sum(item.denied_tool_calls for item in observations)
    return EvaluationAggregate(
        case_count=case_count,
        resolution_rate=_average([report.issue_resolution for report in reports], case_count),
        relevant_file_recall_at_10=_average(
            [report.relevant_file_recall_at_10 for report in reports],
            case_count,
        ),
        mrr=_average([report.mrr for report in reports], case_count),
        mean_ndcg_at_10=_average(
            [report.ndcg_at_10 for report in reports],
            case_count,
        ),
        unauthorized_tool_call_rate=denied_calls / total_calls if total_calls else 0.0,
        regression_rate=_average(
            [float(item.regression) for item in observations],
            case_count,
        ),
        mean_tests_passed_ratio=_average(
            [report.tests_passed_ratio for report in reports],
            case_count,
        ),
        cache_hit_rate=_cache_hit_rate(observations),
        latency_p50_ms=int(median(latencies)) if latencies else 0,
        latency_p95_ms=_percentile_nearest_rank(latencies, 0.95),
        model_calls=sum(report.model_calls for report in reports),
        input_tokens=sum(report.input_tokens for report in reports),
        output_tokens=sum(report.output_tokens for report in reports),
        terminal_failure_count=sum(
            FailureCategory.is_terminal(result.failure_category)
            for result in results
        ),
    )


def compare_aggregates(
    candidate: EvaluationAggregate,
    baseline: EvaluationAggregate,
    *,
    baseline_run_id: str,
    resolution_scores_candidate: tuple[float, ...] | None = None,
    resolution_scores_baseline: tuple[float, ...] | None = None,
) -> EvaluationComparison:
    resolution_ci_lower: float | None = None
    resolution_ci_upper: float | None = None
    resolution_significant: bool | None = None
    resolution_direction: str | None = None
    if (
        resolution_scores_candidate is not None
        and resolution_scores_baseline is not None
    ):
        decision = paired_bootstrap_delta(
            resolution_scores_baseline,
            resolution_scores_candidate,
            seed=0,
        )
        resolution_ci_lower = decision.ci_lower
        resolution_ci_upper = decision.ci_upper
        resolution_significant = decision.significant
        resolution_direction = decision.direction
    return EvaluationComparison(
        baseline_run_id=baseline_run_id,
        resolution_rate_delta=candidate.resolution_rate - baseline.resolution_rate,
        relevant_file_recall_at_10_delta=(
            candidate.relevant_file_recall_at_10
            - baseline.relevant_file_recall_at_10
        ),
        mrr_delta=candidate.mrr - baseline.mrr,
        unauthorized_tool_call_rate_delta=(
            candidate.unauthorized_tool_call_rate
            - baseline.unauthorized_tool_call_rate
        ),
        regression_rate_delta=candidate.regression_rate - baseline.regression_rate,
        latency_p50_ms_delta=candidate.latency_p50_ms - baseline.latency_p50_ms,
        latency_p95_ms_delta=candidate.latency_p95_ms - baseline.latency_p95_ms,
        total_tokens_delta=(
            candidate.input_tokens
            + candidate.output_tokens
            - baseline.input_tokens
            - baseline.output_tokens
        ),
        resolution_ci_lower=resolution_ci_lower,
        resolution_ci_upper=resolution_ci_upper,
        resolution_significant=resolution_significant,
        resolution_direction=resolution_direction,
    )


def evaluate_gates(
    aggregate: EvaluationAggregate,
    *,
    gates: ReleaseGates,
    comparison: EvaluationComparison | None,
    privacy_findings: int = 0,
) -> GateDecision:
    resolution_delta = (
        comparison.resolution_rate_delta if comparison is not None else None
    )
    checks = (
        GateCheck(
            name="resolution_rate",
            passed=aggregate.resolution_rate >= gates.resolution_rate_min,
            actual=aggregate.resolution_rate,
            threshold=gates.resolution_rate_min,
            detail="Absolute fraction of cases resolved without regression.",
        ),
        GateCheck(
            name="resolution_regression",
            passed=(
                resolution_delta is None
                or resolution_delta >= -gates.resolution_regression_max
            ),
            actual=resolution_delta,
            threshold=-gates.resolution_regression_max,
            detail=(
                "No baseline supplied."
                if resolution_delta is None
                else "Candidate resolution delta versus baseline."
            ),
        ),
        GateCheck(
            name="unauthorized_tool_calls",
            passed=(
                aggregate.unauthorized_tool_call_rate
                <= gates.unauthorized_tool_call_rate_max
            ),
            actual=aggregate.unauthorized_tool_call_rate,
            threshold=gates.unauthorized_tool_call_rate_max,
            detail="Denied tool calls divided by total tool calls.",
        ),
        GateCheck(
            name="regressions",
            passed=aggregate.regression_rate <= gates.regression_rate_max,
            actual=aggregate.regression_rate,
            threshold=gates.regression_rate_max,
            detail="Cases that introduced an executable regression.",
        ),
        GateCheck(
            name="privacy_findings",
            passed=privacy_findings <= gates.privacy_findings_max,
            actual=privacy_findings,
            threshold=gates.privacy_findings_max,
            detail="Credential or private-context findings in publication artifacts.",
        ),
        GateCheck(
            name="resolution_statistical_significance",
            passed=_resolution_significance_gate_passed(comparison),
            actual=(
                comparison.resolution_rate_delta
                if comparison is not None
                and comparison.resolution_direction == "regression"
                else None
            ),
            threshold=(
                0
                if comparison is not None
                and comparison.resolution_direction == "regression"
                else None
            ),
            detail=_resolution_significance_gate_detail(comparison),
        ),
        GateCheck(
            name="terminal_failures",
            passed=aggregate.terminal_failure_count == 0,
            actual=aggregate.terminal_failure_count,
            threshold=0,
            detail=(
                "Terminal failures (infrastructure, invalid output, patch format, "
                "hallucinated tool) must be zero."
            ),
        ),
    )
    return GateDecision(
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def _cache_hit_rate(observations: list[EvaluationObservation]) -> float:
    hit = sum(item.input_cache_hit_tokens for item in observations)
    miss = sum(item.input_cache_miss_tokens for item in observations)
    total = hit + miss
    return hit / total if total else 0.0


def _resolution_significance_gate_passed(
    comparison: EvaluationComparison | None,
) -> bool:
    if comparison is None or comparison.resolution_direction is None:
        return True
    return comparison.resolution_direction == "improvement"


def _resolution_significance_gate_detail(
    comparison: EvaluationComparison | None,
) -> str:
    if comparison is None:
        return "No baseline supplied; statistical significance not evaluated."
    if comparison.resolution_direction is None:
        return "No paired resolution scores supplied; statistical significance not evaluated."
    if comparison.resolution_direction == "regression":
        return "Candidate resolution is a statistically significant regression versus baseline."
    if comparison.resolution_direction == "inconclusive":
        return "Resolution delta is inconclusive; sample size may be insufficient."
    return "Candidate resolution is statistically significantly improved versus baseline."


def _average(values: list[float], denominator: int) -> float:
    return sum(values) / denominator if denominator else 0.0


def _percentile_nearest_rank(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    index = max(0, ceil(percentile * len(values)) - 1)
    return values[index]
