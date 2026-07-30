from uuid import UUID

import pytest
from pydantic import ValidationError

from repo_maintenance_agent.domain.errors import InvalidStateTransition
from repo_maintenance_agent.domain.models import (
    ApprovalDecision,
    RepoTaskState,
    RiskLevel,
    TaskStatus,
)


def test_task_scope_rejects_non_commit_sha() -> None:
    with pytest.raises(ValidationError, match="commit_sha"):
        RepoTaskState(
            tenant_id="tenant-a",
            repo_id="owner/repo",
            commit_sha="main",
            base_branch="main",
            issue={"title": "Fix it", "body": "Details"},
        )


def test_task_uses_non_guessable_identifier_and_strict_scope() -> None:
    task = RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix it", "body": "Details"},
    )

    assert UUID(task.task_id).version == 4
    assert task.status is TaskStatus.PENDING
    assert task.risk is RiskLevel.LOW


def test_task_transition_rejects_skipping_required_stages() -> None:
    task = RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="b" * 40,
        base_branch="main",
        issue={"title": "Fix it", "body": "Details"},
    )

    with pytest.raises(InvalidStateTransition, match=r"pending.*coding"):
        task.transition(TaskStatus.CODING)


def test_approval_is_bound_to_plan_hash() -> None:
    decision = ApprovalDecision(
        approved=True,
        approver="reviewer@example.invalid",
        plan_hash="c" * 64,
        reason="Reviewed",
    )

    assert decision.approved
    with pytest.raises(ValidationError, match="plan_hash"):
        ApprovalDecision(
            approved=True,
            approver="reviewer@example.invalid",
            plan_hash="short",
            reason="Reviewed",
        )
