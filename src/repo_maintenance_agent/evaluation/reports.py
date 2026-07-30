from __future__ import annotations

from repo_maintenance_agent.evaluation.models import (
    EvaluationAggregate,
    EvaluationCaseResult,
    EvaluationComparison,
    GateDecision,
)


def render_markdown_report(
    *,
    run_id: str,
    candidate_label: str,
    aggregate: EvaluationAggregate,
    comparison: EvaluationComparison | None,
    decision: GateDecision,
    results: tuple[EvaluationCaseResult, ...],
) -> str:
    status = "PASS" if decision.passed else "FAIL"
    baseline = comparison.baseline_run_id if comparison is not None else "not supplied"
    lines = [
        "# Evaluation Report",
        "",
        f"- Run: `{run_id}`",
        f"- Candidate: `{candidate_label}`",
        f"- Baseline: `{baseline}`",
        f"- Release gate: **{status}**",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Cases | {aggregate.case_count} |",
        f"| Resolution rate | {aggregate.resolution_rate:.4f} |",
        f"| Recall@10 | {aggregate.relevant_file_recall_at_10:.4f} |",
        f"| MRR | {aggregate.mrr:.4f} |",
        f"| Unauthorized call rate | {aggregate.unauthorized_tool_call_rate:.4f} |",
        f"| Regression rate | {aggregate.regression_rate:.4f} |",
        f"| Latency p50 | {aggregate.latency_p50_ms} ms |",
        f"| Latency p95 | {aggregate.latency_p95_ms} ms |",
        "",
        "## Gate Checks",
        "",
        "| Check | Result | Actual | Threshold |",
        "|---|---|---:|---:|",
    ]
    lines.extend(
        (
            f"| {check.name} | {'pass' if check.passed else 'fail'} | "
            f"{_display(check.actual)} | {_display(check.threshold)} |"
        )
        for check in decision.checks
    )
    if results:
        lines.extend(
            [
                "",
                "## Cases",
                "",
                "| Case | Result | Attempts | Failure |",
                "|---|---|---:|---|",
            ]
        )
        lines.extend(
            (
                f"| `{result.case_id}` | "
                f"{'pass' if result.report and result.report.issue_resolution == 1 else 'fail'} | "
                f"{result.attempts} | {result.failure_category.value} |"
            )
            for result in results
        )
    return "\n".join(lines) + "\n"


def _display(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
