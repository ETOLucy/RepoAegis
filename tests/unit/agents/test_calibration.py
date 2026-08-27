"""Tests for CalibrationJudge integration in the agent pipeline."""

from __future__ import annotations

import pytest

from repo_maintenance_agent.agents.calibration import CalibrationJudge
from repo_maintenance_agent.domain.models import Evidence


class FakeModel:
    """Fake model that returns a scripted calibration output."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model unavailable")
        return schema(
            calibrated_task_type="bugfix",
            calibrated_ac=None,
            calibrated_constraints=None,
            calibrated_unknowns=None,
            calibration_reason="evidence contains error messages",
        )


@pytest.mark.asyncio
async def test_calibration_rule_based_test_evidence() -> None:
    judge = CalibrationJudge(model=None)  # rule-based only
    evidence = [
        Evidence(
            source="test_file",
            locator="tests/test_config.py:10",
            summary="test_load_config returns expected values",
        )
    ]
    result = await judge.calibrate(
        task_spec={"task_type": "bugfix"},
        evidence=evidence,
        stage="research",
    )
    assert result["calibrated_task_type"] == "test"
    assert "test files" in result["calibration_reason"]


@pytest.mark.asyncio
async def test_calibration_rule_based_error_evidence() -> None:
    judge = CalibrationJudge(model=None)
    evidence = [
        Evidence(
            source="error_log",
            locator="src/config.py:42",
            summary="KeyError: 'NoSuchKey' raised in load_config",
        )
    ]
    result = await judge.calibrate(
        task_spec={"task_type": "feature"},
        evidence=evidence,
        stage="research",
    )
    assert result["calibrated_task_type"] == "bugfix"
    assert "error" in result["calibration_reason"].lower()


@pytest.mark.asyncio
async def test_calibration_empty_evidence() -> None:
    judge = CalibrationJudge(model=None)
    result = await judge.calibrate(
        task_spec={"task_type": "bugfix"},
        evidence=[],
        stage="planning",
    )
    assert result["calibrated_task_type"] is None
    assert "insufficient evidence" in result["calibration_reason"]


@pytest.mark.asyncio
async def test_calibration_llm_fallback_to_rules() -> None:
    """When LLM calibration fails, should fall back to rule-based."""
    judge = CalibrationJudge(model=FakeModel(fail=True))
    evidence = [
        Evidence(
            source="test_file",
            locator="tests/test_api.py:10",
            summary="test_create_user returns 201",
        )
    ]
    result = await judge.calibrate(
        task_spec={"task_type": "bugfix"},
        evidence=evidence,
        stage="research",
    )
    assert result["calibrated_task_type"] == "test"
    assert judge._model is not None


@pytest.mark.asyncio
async def test_calibration_no_adjustment_needed() -> None:
    judge = CalibrationJudge(model=None)
    evidence = [
        Evidence(
            source="source_code",
            locator="src/config.py:10",
            summary="load_config function definition",
        )
    ]
    result = await judge.calibrate(
        task_spec={"task_type": "bugfix"},
        evidence=evidence,
        stage="coding",
    )
    assert result["calibrated_task_type"] is None
    assert result["calibrated_by"] == "coding"
