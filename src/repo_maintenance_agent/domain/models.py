from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from repo_maintenance_agent.domain.errors import InvalidStateTransition


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskStatus(StrEnum):
    PENDING = "pending"
    INTAKE = "intake"
    RESEARCH = "research"
    PLANNING = "planning"
    NEEDS_APPROVAL = "needs_approval"
    CODING = "coding"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    DELIVERING = "delivering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorKind(StrEnum):
    CODE = "code_failure"
    BASELINE = "baseline_failure"
    ENVIRONMENT = "environment_failure"
    INFRASTRUCTURE = "infrastructure_failure"
    POLICY = "policy_failure"


class ToolPermission(StrEnum):
    REPO_READ = "repo_read"
    SANDBOX_WRITE = "sandbox_write"
    SANDBOX_EXECUTE = "sandbox_execute"
    GITHUB_READ = "github_read"
    GIT_WRITE = "git_write"
    GITHUB_WRITE = "github_write"
    CONTROL = "control"


class IssueSpec(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=100_000)
    number: int | None = Field(default=None, ge=1)


class Evidence(StrictModel):
    source: str = Field(min_length=1, max_length=100)
    locator: str = Field(min_length=1, max_length=2_000)
    summary: str = Field(min_length=1, max_length=10_000)
    content_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ApprovalEnvelope(StrictModel):
    plan: tuple[dict[str, Any], ...] = ()
    target_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    allowed_tools: tuple[ToolPermission, ...] = ()
    declared_files: tuple[str, ...] = ()
    verification_plan: tuple[str, ...] = ()

    @field_validator("allowed_tools")
    @classmethod
    def normalize_tools(cls, value: tuple[ToolPermission, ...]) -> tuple[ToolPermission, ...]:
        return tuple(sorted(set(value), key=str))

    @field_validator("declared_files")
    @classmethod
    def normalize_files(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class ApprovalDecision(StrictModel):
    approved: bool
    approver: str = Field(min_length=3, max_length=320)
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    allowed_tools: tuple[ToolPermission, ...]
    reason: str = Field(min_length=1, max_length=2_000)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VerificationResult(StrictModel):
    passed: bool
    error_kind: ErrorKind | None = None
    commands: tuple[str, ...] = ()
    summary: str = ""
    artifact_ids: tuple[str, ...] = ()
    failures: tuple[dict[str, str], ...] = ()


class ToolCall(StrictModel):
    call_id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    tenant_id: str
    repo_id: str
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    agent: str
    name: str
    permission: ToolPermission
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=200)


class ToolResult(StrictModel):
    call_id: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    replayed: bool = False


class SearchQuery(StrictModel):
    tenant_id: str
    repo_id: str
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    text: str = Field(min_length=1, max_length=10_000)
    allowed_paths: tuple[str, ...] = ()
    top_k: int = Field(default=15, ge=1, le=100)


class SearchHit(StrictModel):
    hit_id: str
    path: str
    content: str
    score: float
    source: str
    symbol: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)


_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.INTAKE, TaskStatus.CANCELLED}),
    TaskStatus.INTAKE: frozenset({TaskStatus.RESEARCH, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.RESEARCH: frozenset({TaskStatus.PLANNING, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.PLANNING: frozenset(
        {TaskStatus.NEEDS_APPROVAL, TaskStatus.CODING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.NEEDS_APPROVAL: frozenset(
        {TaskStatus.CODING, TaskStatus.CANCELLED, TaskStatus.FAILED}
    ),
    TaskStatus.CODING: frozenset({TaskStatus.VERIFYING, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.VERIFYING: frozenset(
        {TaskStatus.CODING, TaskStatus.REVIEWING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.REVIEWING: frozenset(
        {TaskStatus.CODING, TaskStatus.DELIVERING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.DELIVERING: frozenset(
        {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class RepoTaskState(StrictModel):
    task_id: str = Field(default_factory=lambda: str(uuid4()))
    tenant_id: str = Field(min_length=1, max_length=128)
    repo_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    base_branch: str = Field(min_length=1, max_length=255)
    issue: IssueSpec
    status: TaskStatus = TaskStatus.PENDING
    risk: RiskLevel = RiskLevel.LOW
    risk_reasons: tuple[str, ...] = ()
    iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=3, ge=1, le=10)
    version: int = Field(default=0, ge=0)
    task_spec: dict[str, Any] = Field(default_factory=dict)
    repo_profile: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[Evidence, ...] = ()
    plan: tuple[dict[str, Any], ...] = ()
    plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    declared_files: tuple[str, ...] = ()
    allowed_tools: tuple[ToolPermission, ...] = ()
    verification_plan: tuple[str, ...] = ()
    approval: ApprovalDecision | None = None
    changed_files: tuple[str, ...] = ()
    patch_artifact_id: str | None = None
    verification: VerificationResult | None = None
    review: dict[str, Any] = Field(default_factory=dict)
    pr_draft: dict[str, Any] = Field(default_factory=dict)
    errors: tuple[dict[str, Any], ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("base_branch")
    @classmethod
    def branch_must_be_safe(cls, value: str) -> str:
        if value.startswith("-") or ".." in value or value.endswith(".lock"):
            raise ValueError("base_branch contains unsafe Git syntax")
        return value

    def transition(self, target: TaskStatus) -> Self:
        if target not in _TRANSITIONS[self.status]:
            raise InvalidStateTransition(f"illegal transition: {self.status} -> {target}")
        return self.model_copy(
            update={
                "status": target,
                "version": self.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )


class TaskCreate(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    repo_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    base_branch: str = Field(default="main", min_length=1, max_length=255)
    issue: IssueSpec

