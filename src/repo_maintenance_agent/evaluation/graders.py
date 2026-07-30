from __future__ import annotations


def relevant_file_recall(*, retrieved: list[str], gold: list[str], k: int) -> float:
    gold_set = set(gold)
    if not gold_set:
        return 1.0
    retrieved_set = set(retrieved[:k])
    return len(retrieved_set & gold_set) / len(gold_set)


def resolution_score(*, hidden_tests_passed: bool, regression: bool) -> float:
    return float(hidden_tests_passed and not regression)


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
