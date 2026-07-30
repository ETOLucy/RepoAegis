from pathlib import Path

import pytest

from repo_maintenance_agent.domain.errors import AuthorizationDenied
from repo_maintenance_agent.domain.models import (
    ApprovalDecision,
    RepoTaskState,
    TaskStatus,
    ToolCall,
    ToolPermission,
    ToolResult,
)
from repo_maintenance_agent.policies.permissions import PermissionPolicy
from repo_maintenance_agent.tools.gateway import InMemoryOperationLog, ToolGateway


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        self.calls += 1
        return ToolResult(call_id=call.call_id, success=True, output={"workspace": str(workspace)})


def make_state(*, approved: bool = False) -> RepoTaskState:
    state = RepoTaskState(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
        status=TaskStatus.CODING,
        plan_hash="b" * 64,
    )
    if approved:
        state = state.model_copy(
            update={
                "approval": ApprovalDecision(
                    approved=True,
                    approver="reviewer@example.invalid",
                    plan_hash="b" * 64,
                    reason="Approved",
                )
            }
        )
    return state


def make_call(**overrides: object) -> ToolCall:
    values: dict[str, object] = {
        "task_id": "task-1",
        "tenant_id": "tenant-a",
        "repo_id": "owner/repo",
        "commit_sha": "a" * 40,
        "agent": "coding",
        "name": "apply_patch",
        "permission": ToolPermission.SANDBOX_WRITE,
        "arguments": {"path": "src/app.py"},
    }
    values.update(overrides)
    return ToolCall(**values)


@pytest.mark.asyncio
async def test_gateway_denies_cross_tenant_call(tmp_path: Path) -> None:
    gateway = ToolGateway(
        policy=PermissionPolicy(),
        adapters={"apply_patch": RecordingAdapter()},
        operation_log=InMemoryOperationLog(),
        workspace_root=tmp_path,
    )

    with pytest.raises(AuthorizationDenied, match="scope mismatch"):
        await gateway.execute(make_call(tenant_id="tenant-b"), make_state())


@pytest.mark.asyncio
async def test_gateway_denies_path_traversal(tmp_path: Path) -> None:
    gateway = ToolGateway(
        policy=PermissionPolicy(),
        adapters={"apply_patch": RecordingAdapter()},
        operation_log=InMemoryOperationLog(),
        workspace_root=tmp_path,
    )

    with pytest.raises(AuthorizationDenied, match="outside"):
        await gateway.execute(
            make_call(arguments={"path": "../../private.txt"}),
            make_state(),
        )


@pytest.mark.asyncio
async def test_github_write_requires_matching_approval(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    gateway = ToolGateway(
        policy=PermissionPolicy(),
        adapters={"create_draft_pr": adapter},
        operation_log=InMemoryOperationLog(),
        workspace_root=tmp_path,
    )
    call = make_call(
        agent="pr",
        name="create_draft_pr",
        permission=ToolPermission.GITHUB_WRITE,
        arguments={},
    )

    with pytest.raises(AuthorizationDenied, match="approval"):
        await gateway.execute(call, make_state())

    result = await gateway.execute(call, make_state(approved=True))
    assert result.success
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_gateway_replays_idempotent_write_without_executing_twice(tmp_path: Path) -> None:
    adapter = RecordingAdapter()
    gateway = ToolGateway(
        policy=PermissionPolicy(),
        adapters={"apply_patch": adapter},
        operation_log=InMemoryOperationLog(),
        workspace_root=tmp_path,
    )
    call = make_call(idempotency_key="task-1:patch:1")

    first = await gateway.execute(call, make_state())
    second = await gateway.execute(call, make_state())

    assert first.success
    assert second.replayed
    assert adapter.calls == 1
