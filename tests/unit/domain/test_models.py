from uuid import UUID

import pytest
from pydantic import ValidationError

from repo_maintenance_agent.domain.errors import InvalidStateTransition
from repo_maintenance_agent.domain.models import (
    ApprovalDecision,
    ApprovalEnvelope,
    RepoTaskState,
    RiskLevel,
    TaskStatus,
    ToolPermission,
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
        target_commit="a" * 40,
        allowed_tools=(ToolPermission.REPO_READ,),
        reason="Reviewed",
    )

    assert decision.approved
    with pytest.raises(ValidationError, match="plan_hash"):
        ApprovalDecision(
            approved=True,
            approver="reviewer@example.invalid",
            plan_hash="short",
            target_commit="a" * 40,
            allowed_tools=(ToolPermission.REPO_READ,),
            reason="Reviewed",
        )


def test_approval_envelope_hash_binds_every_reviewed_scope() -> None:
    envelope = ApprovalEnvelope(
        plan=({"description": "Update workflow", "paths": [".github/workflows/ci.yml"]},),
        target_commit="a" * 40,
        allowed_tools=(ToolPermission.REPO_READ, ToolPermission.SANDBOX_WRITE),
        declared_files=(".github/workflows/ci.yml",),
        verification_plan=("pytest tests/test_ci.py",),
    )

    digest = envelope.digest()

    assert len(digest) == 64
    assert envelope.model_copy(update={"target_commit": "b" * 40}).digest() != digest
    assert (
        envelope.model_copy(
            update={"allowed_tools": (*envelope.allowed_tools, ToolPermission.GIT_WRITE)}
        ).digest()
        != digest
    )
    assert envelope.model_copy(update={"declared_files": ("pyproject.toml",)}).digest() != digest
    assert envelope.model_copy(update={"verification_plan": ("ruff check .",)}).digest() != digest
