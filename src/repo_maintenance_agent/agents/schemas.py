from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from repo_maintenance_agent.domain.models import RiskLevel


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskSpecOutput(AgentOutput):
    task_type: Literal["bugfix", "feature", "test", "documentation", "dependency", "refactor"]
    summary: str = Field(min_length=1, max_length=2_000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=20)


class PlanStep(AgentOutput):
    description: str = Field(min_length=1, max_length=2_000)
    paths: list[str] = Field(min_length=1, max_length=50)
    verification: str = Field(min_length=1, max_length=2_000)


class PlanOutput(AgentOutput):
    steps: list[PlanStep] = Field(min_length=1, max_length=30)
    risk: RiskLevel
    risk_reasons: list[str] = Field(default_factory=list, max_length=20)


class PatchProposal(AgentOutput):
    summary: str = Field(min_length=1, max_length=2_000)
    unified_diff: str = Field(min_length=1, max_length=500_000)
    changed_files: list[str] = Field(min_length=1, max_length=100)


class ReviewOutput(AgentOutput):
    decision: Literal["approve", "request_changes"]
    findings: list[str] = Field(default_factory=list, max_length=50)
    summary: str = Field(min_length=1, max_length=5_000)


class PullRequestDraft(AgentOutput):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1, max_length=50_000)
    head: str = Field(min_length=1, max_length=255)
    base: str = Field(min_length=1, max_length=255)
