"""Self-contained LLM-as-Judge evaluation primitives.

This module is deliberately dependency-free: it only uses the standard
library and pydantic.  The actual LLM call is delegated to an injected
``JudgeGateway`` so callers can substitute deterministic fakes in tests
or production gateways backed by any provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_JUDGE_SYSTEM_PROMPT = """\
You are a rigorous judge for repository-maintenance agent outputs.
Rubric: {rubric_name}
Scale: {scale_min} (worst) to {scale_max} (best).
Criteria:
{criteria}
Score every criterion as an integer within the scale. Reply with JSON: \
{{"criteria_scores": {{criterion: int}}, "rationale": "one short paragraph"}}."""


class JudgeRubric(BaseModel):
    """Definition of the dimensions and scale a judge should score on."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    criteria: tuple[str, ...] = Field(min_length=1)
    scale_min: int = Field(default=1, ge=0)
    scale_max: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def scale_range_is_valid(self) -> JudgeRubric:
        if self.scale_min > self.scale_max:
            raise ValueError("scale_min must be <= scale_max")
        return self


class JudgeScore(BaseModel):
    """A single judge verdict for one case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    criteria_scores: dict[str, int]
    rationale: str = Field(min_length=1)
    judge_model: str = Field(min_length=1)
    judge_prompt_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def criteria_scores_within_scale(self) -> JudgeScore:
        invalid = [
            (criterion, value)
            for criterion, value in self.criteria_scores.items()
            if value < 1 or value > 5
        ]
        if invalid:
            raise ValueError(f"criteria_scores out of range 1..5: {invalid!r}")
        return self


@dataclass(frozen=True)
class JudgeConsistency:
    """Stability of repeated judge scores for the same case."""

    rerun_count: int
    mean_std: float


class JudgeGateway(Protocol):
    """Async gateway that turns a rubric + observation into a score."""

    async def score(
        self,
        rubric: JudgeRubric,
        *,
        system: str,
        input_text: str,
    ) -> JudgeScore: ...


async def judge_case(
    gateway: JudgeGateway,
    *,
    rubric: JudgeRubric,
    case_id: str,
    observation_text: str,
    judge_model: str,
    judge_prompt_version: str,
    reruns: int = 2,
) -> tuple[JudgeScore, JudgeConsistency]:
    """Score one case, optionally rerunning to estimate stability.

    Returns the first score together with consistency statistics.
    When ``reruns >= 2``, ``mean_std`` is the mean over criteria of the
    population standard deviation of that criterion across reruns.
    """
    if reruns < 1:
        raise ValueError("reruns must be >= 1")

    system = DEFAULT_JUDGE_SYSTEM_PROMPT.format(
        rubric_name=rubric.name,
        criteria="\n".join(f"- {criterion}" for criterion in rubric.criteria),
        scale_min=rubric.scale_min,
        scale_max=rubric.scale_max,
    )

    scores: list[JudgeScore] = []
    for _ in range(reruns):
        scores.append(
            await gateway.score(
                rubric,
                system=system,
                input_text=observation_text,
            )
        )

    mean_std = 0.0
    if reruns >= 2:
        criterion_stds: list[float] = []
        for criterion in rubric.criteria:
            values = [
                score.criteria_scores[criterion]
                for score in scores
                if criterion in score.criteria_scores
            ]
            if values:
                criterion_stds.append(pstdev(values))
        if criterion_stds:
            mean_std = sum(criterion_stds) / len(criterion_stds)

    return scores[0], JudgeConsistency(rerun_count=reruns, mean_std=mean_std)


def render_judge_table(scores: list[JudgeScore]) -> str:
    """Render judge scores as a Markdown table (Case | criteria | Judge)."""
    if not scores:
        return "No judge scores."

    criteria: list[str] = []
    for score in scores:
        for criterion in score.criteria_scores:
            if criterion not in criteria:
                criteria.append(criterion)

    header = "| Case | " + " | ".join(criteria) + " | Judge |"
    separator = "|" + "---|" * (len(criteria) + 2)
    rows = [
        "| "
        + score.case_id
        + " | "
        + " | ".join(str(score.criteria_scores.get(criterion, "—")) for criterion in criteria)
        + f" | {score.judge_model} |"
        for score in scores
    ]
    return "\n".join([header, separator, *rows])


def agreement(
    deterministic: float,
    rubric: float,
    *,
    tolerance: float = 0.5,
) -> bool:
    """判断确定性评分与 LLM rubric 评分是否一致(绝对差在容差内)。

    用于「确定性评分 vs LLM rubric 评分」的一致性分析, 呼应 AegisEvo 评测中的
    EvaluatorDisagreement pathology: 当同一 case 的两套评分分歧超过容差时,
    说明 LLM 判分器与确定性基线存在系统性不一致, 需要复核 rubric 定义或判分
    提示词。边界恰等(|deterministic - rubric| == tolerance)视为一致。
    """
    return abs(deterministic - rubric) <= tolerance


def judge_disagreement_rate(cases: list[tuple[float, float]]) -> float:
    """计算「确定性评分 vs LLM rubric 评分」的不一致率。

    ``cases`` 为 ``(deterministic, rubric)`` 评分对列表, 返回超出容差的评分对
    占比(0.0 ~ 1.0); 空列表返回 0.0。该指标对应 AegisEvo 的
    EvaluatorDisagreement pathology, 用于量化两套评分体系的整体分歧程度。
    """
    if not cases:
        return 0.0
    return sum(not agreement(deterministic, rubric) for deterministic, rubric in cases) / len(cases)
