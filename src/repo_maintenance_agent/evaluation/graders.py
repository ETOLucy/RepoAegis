from __future__ import annotations

import math


def ndcg_at_k(*, retrieved: list[str], gold: list[str], k: int = 10) -> float:
    """NDCG at rank k (binary relevance)."""
    gold_set = set(gold)
    if not gold_set or k <= 0:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(retrieved[:k], start=1)
        if item in gold_set
    )
    ideal_count = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def relevant_file_recall(*, retrieved: list[str], gold: list[str], k: int) -> float:
    gold_set = set(gold)
    if not gold_set:
        return 1.0
    retrieved_set = set(retrieved[:k])
    return len(retrieved_set & gold_set) / len(gold_set)


def resolution_score(*, hidden_tests_passed: bool, regression: bool) -> float:
    return float(hidden_tests_passed and not regression)


def partial_resolution_score(
    *,
    hidden_tests_passed: bool,
    regression: bool,
    tests_passed: int,
    tests_total: int,
) -> float:
    if regression:
        return 0.0
    if hidden_tests_passed:
        return 1.0
    if tests_total == 0:
        return 0.0
    return tests_passed / tests_total


def unauthorized_call_rate(*, total_calls: int, denied_calls: int) -> float:
    if total_calls < 0 or denied_calls < 0 or denied_calls > total_calls:
        raise ValueError("tool call counts are inconsistent")
    return denied_calls / total_calls if total_calls else 0.0


def mean_reciprocal_rank(*, retrieved: list[str], gold: list[str]) -> float:
    gold_set = set(gold)
    for rank, item in enumerate(retrieved, start=1):
        if item in gold_set:
            return 1.0 / rank
    return 0.0
