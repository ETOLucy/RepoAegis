from pathlib import Path

import pytest

from repo_maintenance_agent.domain.errors import AuthorizationDenied
from repo_maintenance_agent.domain.models import (
    IssueSpec,
    RepoTaskState,
    TaskStatus,
    ToolCall,
    ToolPermission,
)
from repo_maintenance_agent.policies.permissions import PermissionPolicy


def _state(*, status: TaskStatus = TaskStatus.RESEARCH) -> RepoTaskState:
    return RepoTaskState(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue=IssueSpec(number=1, title="Fix", body="Details"),
        status=status,
    )


def _call(*, agent: str, permission: ToolPermission, name: str = "search_code") -> ToolCall:
    return ToolCall(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        agent=agent,
        name=name,
        permission=permission,
        arguments={"text": "load_config", "top_k": 5},
    )


def test_localizer_agent_is_authorized_for_repo_read() -> None:
    """Localizer runs as a sub-step of the research node and issues its own
    ToolCall(agent="localizer", ...) — see agents/localizer.py. It must carry
    the same REPO_READ permission as "research" or every real localization
    round (search/read/blame) is rejected before it reaches the tool adapter.
    """
    policy = PermissionPolicy()
    call = _call(agent="localizer", permission=ToolPermission.REPO_READ)
    policy.authorize(call, _state(), Path("."))


def test_localizer_agent_cannot_use_permissions_it_was_not_granted() -> None:
    policy = PermissionPolicy()
    with pytest.raises(AuthorizationDenied):
        policy.authorize(
            _call(agent="localizer", permission=ToolPermission.GIT_WRITE, name="git_commit"),
            _state(),
            Path("."),
        )


def test_unknown_agent_is_denied() -> None:
    policy = PermissionPolicy()
    call = _call(agent="not-a-real-agent", permission=ToolPermission.REPO_READ)
    with pytest.raises(AuthorizationDenied):
        policy.authorize(call, _state(), Path("."))
