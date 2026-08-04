from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class ContextRequest(AgentOutput):
    ready_to_patch: bool
    search_queries: list[str] = Field(default_factory=list, max_length=5)
    files: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def request_has_an_action(self) -> ContextRequest:
        if not self.ready_to_patch and not self.search_queries and not self.files:
            raise ValueError("context request must search, read, or be ready to patch")
        return self


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
