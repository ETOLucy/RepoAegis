from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from repo_maintenance_agent.domain.models import (
    IssueSpec,
    RepoTaskState,
    RiskLevel,
    TaskStatus,
    ToolPermission,
)
from repo_maintenance_agent.evaluation.models import (
    EvaluationAggregate,
    EvaluationCaseResult,
    EvaluationComparison,
    EvaluationObservation,
    EvaluationProvenance,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
    GateDecision,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskCreateRequest(ApiModel):
    repo_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    base_branch: str = Field(default="main", min_length=1, max_length=255)
    issue: IssueSpec


class EvidenceSummary(ApiModel):
    source: str
    locator: str
    summary: str


class TaskResponse(ApiModel):
    task_id: str
    repo_id: str
    commit_sha: str
    base_branch: str
    status: TaskStatus
    plan: tuple[dict[str, object], ...]
    risk: RiskLevel
    risk_reasons: tuple[str, ...]
    plan_hash: str | None
    declared_files: tuple[str, ...]
    allowed_tools: tuple[ToolPermission, ...]
    verification_plan: tuple[str, ...]
    evidence_summary: tuple[EvidenceSummary, ...]
    iteration: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_state(cls, state: RepoTaskState) -> TaskResponse:
        return cls(
            task_id=state.task_id,
            repo_id=state.repo_id,
            commit_sha=state.commit_sha,
            base_branch=state.base_branch,
            status=state.status,
            plan=state.plan,
            risk=state.risk,
            risk_reasons=state.risk_reasons,
            plan_hash=state.plan_hash,
            declared_files=state.declared_files,
            allowed_tools=state.allowed_tools,
            verification_plan=state.verification_plan,
            evidence_summary=tuple(
                EvidenceSummary(source=item.source, locator=item.locator, summary=item.summary)
                for item in state.evidence
            ),
            iteration=state.iteration,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )


class TaskListResponse(ApiModel):
    items: list[TaskResponse]


class ApprovalRequest(ApiModel):
    approved: bool
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    allowed_tools: tuple[ToolPermission, ...]
    reason: str = Field(min_length=1, max_length=2_000)


class EvaluationRunCreateRequest(ApiModel):
    suite: EvaluationSuite
    candidate_label: str = Field(min_length=1, max_length=256)
    provenance: EvaluationProvenance
    observations: dict[str, EvaluationObservation] = Field(max_length=10_000)
    baseline_run_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def exact_observation_set(self) -> EvaluationRunCreateRequest:
        expected = set(self.suite.case_ids)
        supplied = set(self.observations)
        if expected != supplied:
            raise ValueError("observations must exactly match evaluation case IDs")
        return self


class EvaluationReplayRequest(ApiModel):
    case_ids: tuple[str, ...] = Field(min_length=1, max_length=1_000)


class EvaluationRunResponse(ApiModel):
    run_id: str
    suite: EvaluationSuite
    candidate_label: str
    provenance: EvaluationProvenance
    status: EvaluationRunStatus
    baseline_run_id: str | None
    replay_of_run_id: str | None
    selected_case_ids: tuple[str, ...]
    results: tuple[EvaluationCaseResult, ...]
    aggregate: EvaluationAggregate | None
    comparison: EvaluationComparison | None
    gate_decision: GateDecision | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    version: int

    @classmethod
    def from_run(cls, run: EvaluationRun) -> EvaluationRunResponse:
        return cls.model_validate(run, from_attributes=True)


class EvaluationRunListResponse(ApiModel):
    items: list[EvaluationRunResponse]
