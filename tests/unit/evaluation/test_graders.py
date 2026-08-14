import math

import pytest

from repo_maintenance_agent.evaluation.graders import (
    ndcg_at_k,
    partial_resolution_score,
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
def test_partial_resolution_score_returns_zero_on_regression() -> None:
    assert (
        partial_resolution_score(
            hidden_tests_passed=True,
            regression=True,
            tests_passed=5,
            tests_total=5,
        )
        == 0.0
    )


def test_partial_resolution_score_returns_one_when_hidden_tests_pass() -> None:
    assert (
        partial_resolution_score(
            hidden_tests_passed=True,
            regression=False,
            tests_passed=2,
            tests_total=5,
        )
        == 1.0
    )


def test_partial_resolution_score_uses_passed_ratio() -> None:
    assert (
        partial_resolution_score(
            hidden_tests_passed=False,
            regression=False,
            tests_passed=3,
            tests_total=6,
        )
        == 0.5
    )


def test_partial_resolution_score_zero_total_is_zero() -> None:
    assert (
        partial_resolution_score(
            hidden_tests_passed=False,
            regression=False,
            tests_passed=0,
            tests_total=0,
        )
        == 0.0
    )


def test_ndcg_at_k_all_hits_is_one() -> None:
    score = ndcg_at_k(retrieved=["a", "b", "c"], gold=["a", "b", "c"], k=3)

    assert score == pytest.approx(1.0)


def test_ndcg_at_k_partial_hits_ranks_high_relevance_first() -> None:
    score = ndcg_at_k(retrieved=["a", "x", "b"], gold=["a", "b"], k=3)
    expected = 1.5 / (1 + 1 / math.log2(3))

    assert score == pytest.approx(expected)


def test_ndcg_at_k_no_hits_is_zero() -> None:
    score = ndcg_at_k(retrieved=["x", "y", "z"], gold=["a", "b"], k=3)

    assert score == 0.0


def test_ndcg_at_k_empty_gold_is_zero() -> None:
    score = ndcg_at_k(retrieved=["a", "b", "c"], gold=[], k=3)

    assert score == 0.0


def test_ndcg_at_k_truncates_to_k() -> None:
    score = ndcg_at_k(retrieved=["a", "b", "c", "d"], gold=["a", "b", "c"], k=2)

    assert score == pytest.approx(1.0)

