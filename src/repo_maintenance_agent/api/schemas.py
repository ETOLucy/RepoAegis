from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from repo_maintenance_agent.domain.models import IssueSpec, RepoTaskState, TaskStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskCreateRequest(ApiModel):
    repo_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    base_branch: str = Field(default="main", min_length=1, max_length=255)
    issue: IssueSpec


class TaskResponse(ApiModel):
    task_id: str
    repo_id: str
    commit_sha: str
    base_branch: str
    status: TaskStatus
    iteration: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_state(cls, state: RepoTaskState) -> TaskResponse:
        return cls.model_validate(state, from_attributes=True)


class ApprovalRequest(ApiModel):
    approved: bool
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=2_000)

