"""Tests for repo_maintenance_agent.inspect.scorers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from inspect_ai.scorer import Scorer, Target

from repo_maintenance_agent.inspect.scorers import (
    _progress_ratio,
    repoaegis_swe_progress_scorer,
)


def test_progress_ratio_partial() -> None:
    assert _progress_ratio(passed_ftp=1, passed_p2p=0, total_ftp=2, total_p2p=1) == pytest.approx(
        1 / 3, abs=1e-4
    )


def test_progress_ratio_all_pass() -> None:
    assert _progress_ratio(passed_ftp=2, passed_p2p=1, total_ftp=2, total_p2p=1) == 1.0


def test_progress_ratio_no_tests_is_zero() -> None:
    assert _progress_ratio(passed_ftp=0, passed_p2p=0, total_ftp=0, total_p2p=0) == 0.0


def test_progress_ratio_clamped_and_negative_tolerant() -> None:
    # Over-counted passes clamp to 1.0; negative totals never drive a >1 ratio.
    assert _progress_ratio(passed_ftp=5, passed_p2p=0, total_ftp=2, total_p2p=0) == 1.0
    assert _progress_ratio(passed_ftp=-1, passed_p2p=0, total_ftp=1, total_p2p=0) == 0.0
    assert _progress_ratio(passed_ftp=0, passed_p2p=0, total_ftp=-3, total_p2p=0) == 0.0


def test_scorer_factory_returns_callable_scorer() -> None:
    scorer_obj = repoaegis_swe_progress_scorer()

    assert callable(scorer_obj)
    assert isinstance(scorer_obj, Scorer)


def test_scorer_uses_precomputed_passed_ratio() -> None:
    scorer_obj = repoaegis_swe_progress_scorer(pass_threshold=0.5)
    state = SimpleNamespace(
        metadata={
            "passed_ratio": 0.75,
            "FAIL_TO_PASS": ["a", "b"],
            "PASS_TO_PASS": ["c"],
        }
    )

    score = asyncio.run(scorer_obj(state, Target("")))

    assert score.value == 0.75
    assert score.metadata["passed"] is True
    assert score.metadata["total_tests"] == 3


def test_scorer_computes_ratio_from_passed_counts() -> None:
    scorer_obj = repoaegis_swe_progress_scorer()
    state = SimpleNamespace(
        metadata={
            "passed_ftp": 1,
            "passed_p2p": 0,
            "FAIL_TO_PASS": ["a", "b"],
            "PASS_TO_PASS": ["c"],
        }
    )

    score = asyncio.run(scorer_obj(state, Target("")))

    assert score.value == pytest.approx(1 / 3, abs=1e-4)
    assert score.metadata["passed"] is False


def test_scorer_below_threshold_not_passed() -> None:
    scorer_obj = repoaegis_swe_progress_scorer(pass_threshold=0.9)
    state = SimpleNamespace(metadata={"passed_ratio": 0.5, "FAIL_TO_PASS": [], "PASS_TO_PASS": []})

    score = asyncio.run(scorer_obj(state, Target("")))

    assert score.value == 0.5
    assert score.metadata["passed"] is False


def test_scorer_no_evidence_scores_zero() -> None:
    scorer_obj = repoaegis_swe_progress_scorer()
    state = SimpleNamespace(metadata={})

    score = asyncio.run(scorer_obj(state, Target("")))

    assert score.value == 0.0
    assert score.metadata["passed"] is False


def test_scorer_tolerates_malformed_passed_ratio() -> None:
    scorer_obj = repoaegis_swe_progress_scorer()
    state = SimpleNamespace(
        metadata={"passed_ratio": "not-a-number", "FAIL_TO_PASS": ["a"], "PASS_TO_PASS": []}
    )

    score = asyncio.run(scorer_obj(state, Target("")))

    assert score.value == 0.0
