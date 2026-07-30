from __future__ import annotations

from repo_maintenance_agent.evaluation.graders import (
    mean_reciprocal_rank,
    relevant_file_recall,
    resolution_score,
    unauthorized_call_rate,
)
from repo_maintenance_agent.evaluation.models import EvaluationCase, EvaluationReport


def grade_case(
    case: EvaluationCase,
    *,
    retrieved_files: list[str],
    hidden_tests_passed: bool,
    regression: bool,
    total_tool_calls: int,
    denied_tool_calls: int,
    wall_clock_ms: int,
    model_calls: int,
    input_tokens: int,
    output_tokens: int,
) -> EvaluationReport:
    return EvaluationReport(
        case_id=case.case_id,
        issue_resolution=resolution_score(
            hidden_tests_passed=hidden_tests_passed,
            regression=regression,
        ),
        relevant_file_recall_at_10=relevant_file_recall(
            retrieved=retrieved_files,
            gold=list(case.gold_files),
            k=10,
        ),
        mrr=mean_reciprocal_rank(
            retrieved=retrieved_files,
            gold=list(case.gold_files),
        ),
        unauthorized_tool_call_rate=unauthorized_call_rate(
            total_calls=total_tool_calls,
            denied_calls=denied_tool_calls,
        ),
        wall_clock_ms=wall_clock_ms,
        model_calls=model_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
