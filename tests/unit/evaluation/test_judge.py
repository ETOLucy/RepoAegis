"""Unit tests for the self-contained LLM-as-Judge module."""

from __future__ import annotations

import math
from statistics import pstdev

import pytest
from pydantic import ValidationError

from repo_maintenance_agent.evaluation.judge import (
    JudgeConsistency,
    JudgeGateway,
    JudgeRubric,
    JudgeScore,
    agreement,
    judge_case,
    judge_disagreement_rate,
    render_judge_table,
)


class _FakeJudgeGateway:
    """Deterministic gateway that cycles through scripted scores."""

    def __init__(self, script: list[dict[str, int]]) -> None:
        self._script = script
        self._index = 0
        self.calls: list[tuple[str, str]] = []

    async def score(
        self,
        rubric: JudgeRubric,
        *,
        system: str,
        input_text: str,
    ) -> JudgeScore:
        self.calls.append((system, input_text))
        criteria_scores = self._script[self._index % len(self._script)]
        self._index += 1
        return JudgeScore(
            case_id="case-1",
            criteria_scores=criteria_scores,
            rationale="deterministic fake verdict",
            judge_model="fake-judge",
            judge_prompt_version="prompt-v1",
        )


def _rubric() -> JudgeRubric:
    return JudgeRubric(
        name="repo-maintenance",
        criteria=("instruction_following", "code_quality", "tool_use"),
    )


async def test_judge_case_returns_first_score_and_consistency() -> None:
    gateway: JudgeGateway = _FakeJudgeGateway(
        [
            {"instruction_following": 5, "code_quality": 4, "tool_use": 5},
            {"instruction_following": 3, "code_quality": 2, "tool_use": 5},
        ]
    )
    rubric = _rubric()

    score, consistency = await judge_case(
        gateway,
        rubric=rubric,
        case_id="case-1",
        observation_text="agent output for case-1",
        judge_model="fake-judge",
        judge_prompt_version="prompt-v1",
        reruns=2,
    )

    assert isinstance(consistency, JudgeConsistency)
    assert score.case_id == "case-1"
    assert score.criteria_scores == {
        "instruction_following": 5,
        "code_quality": 4,
        "tool_use": 5,
    }
    assert score.judge_model == "fake-judge"
    assert score.judge_prompt_version == "prompt-v1"

    expected_mean_std = (pstdev([5, 3]) + pstdev([4, 2]) + pstdev([5, 5])) / 3
    assert consistency.rerun_count == 2
    assert math.isclose(consistency.mean_std, expected_mean_std, abs_tol=1e-9)

    assert len(gateway.calls) == 2
    assert all("instruction_following" in system for system, _ in gateway.calls)


async def test_judge_case_single_rerun_has_zero_mean_std() -> None:
    gateway: JudgeGateway = _FakeJudgeGateway([{"instruction_following": 5}])
    rubric = JudgeRubric(name="minimal", criteria=("instruction_following",))

    score, consistency = await judge_case(
        gateway,
        rubric=rubric,
        case_id="case-1",
        observation_text="agent output",
        judge_model="fake-judge",
        judge_prompt_version="prompt-v1",
        reruns=1,
    )

    assert consistency.rerun_count == 1
    assert consistency.mean_std == 0.0
    assert score.criteria_scores == {"instruction_following": 5}
    assert len(gateway.calls) == 1


async def test_judge_case_rejects_reruns_below_one() -> None:
    gateway: JudgeGateway = _FakeJudgeGateway([{"instruction_following": 5}])
    with pytest.raises(ValueError, match="reruns"):
        await judge_case(
            gateway,
            rubric=_rubric(),
            case_id="case-1",
            observation_text="agent output",
            judge_model="fake-judge",
            judge_prompt_version="prompt-v1",
            reruns=0,
        )


def test_rubric_rejects_empty_criteria() -> None:
    with pytest.raises(ValidationError, match="criteria"):
        JudgeRubric(name="empty", criteria=())


def test_rubric_rejects_min_greater_than_max() -> None:
    with pytest.raises(ValidationError, match="scale_min"):
        JudgeRubric(name="bad-scale", criteria=("quality",), scale_min=5, scale_max=1)


def test_score_rejects_out_of_range_values() -> None:
    with pytest.raises(ValidationError, match=r"out of range 1\.\.5"):
        JudgeScore(
            case_id="case-1",
            criteria_scores={"instruction_following": 6},
            rationale="too high",
            judge_model="fake-judge",
            judge_prompt_version="prompt-v1",
        )
    with pytest.raises(ValidationError, match=r"out of range 1\.\.5"):
        JudgeScore(
            case_id="case-1",
            criteria_scores={"instruction_following": 0},
            rationale="too low",
            judge_model="fake-judge",
            judge_prompt_version="prompt-v1",
        )


def test_render_judge_table_includes_header_and_case_ids() -> None:
    scores = [
        JudgeScore(
            case_id="case-1",
            criteria_scores={"instruction_following": 5, "code_quality": 3},
            rationale="solid",
            judge_model="fake-judge",
            judge_prompt_version="prompt-v1",
        ),
        JudgeScore(
            case_id="case-2",
            criteria_scores={"instruction_following": 4, "code_quality": 4},
            rationale="good",
            judge_model="fake-judge",
            judge_prompt_version="prompt-v1",
        ),
    ]

    table = render_judge_table(scores)

    assert "| Case | instruction_following | code_quality | Judge |" in table
    assert "case-1" in table
    assert "case-2" in table
    assert "fake-judge" in table


def test_render_judge_table_handles_empty_scores() -> None:
    assert render_judge_table([]) == "No judge scores."


def test_agreement_within_tolerance() -> None:
    assert agreement(3.0, 3.4) is True
    assert agreement(3.0, 2.6) is True


def test_agreement_beyond_tolerance() -> None:
    assert agreement(3.0, 3.6) is False
    assert agreement(3.0, 2.4) is False


def test_agreement_at_tolerance_boundary_is_agree() -> None:
    assert agreement(3.0, 3.5) is True
    assert agreement(3.0, 2.5) is True


def test_agreement_respects_custom_tolerance() -> None:
    assert agreement(3.0, 4.0, tolerance=1.0) is True
    assert agreement(3.0, 4.1, tolerance=1.0) is False


def test_judge_disagreement_rate_empty_cases_is_zero() -> None:
    assert judge_disagreement_rate([]) == 0.0


def test_judge_disagreement_rate_all_agree_is_zero() -> None:
    cases = [(4.0, 4.2), (2.0, 1.9), (5.0, 5.0), (4.0, 4.5)]
    assert judge_disagreement_rate(cases) == 0.0


def test_judge_disagreement_rate_partial_disagreement() -> None:
    cases = [(4.0, 4.2), (3.0, 4.0), (1.0, 4.0)]
    assert judge_disagreement_rate(cases) == 2 / 3
