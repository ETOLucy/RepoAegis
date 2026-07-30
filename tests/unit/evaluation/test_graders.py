from repo_maintenance_agent.evaluation.graders import (
    relevant_file_recall,
    resolution_score,
    unauthorized_call_rate,
)


def test_relevant_file_recall_uses_unique_gold_files() -> None:
    score = relevant_file_recall(
        retrieved=["src/a.py", "src/a.py", "src/b.py"],
        gold=["src/a.py", "src/c.py"],
        k=3,
    )

    assert score == 0.5


def test_resolution_requires_hidden_tests_and_no_regression() -> None:
    assert resolution_score(hidden_tests_passed=True, regression=False) == 1.0
    assert resolution_score(hidden_tests_passed=True, regression=True) == 0.0


def test_unauthorized_call_rate_handles_empty_trace() -> None:
    assert unauthorized_call_rate(total_calls=0, denied_calls=0) == 0.0
    assert unauthorized_call_rate(total_calls=10, denied_calls=2) == 0.2
