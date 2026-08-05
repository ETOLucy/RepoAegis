import hashlib
import subprocess
from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import (
    RepoTaskState,
    ToolCall,
    ToolPermission,
)
from repo_maintenance_agent.policies.permissions import PermissionPolicy
from repo_maintenance_agent.tools.gateway import InMemoryOperationLog, ToolGateway
from repo_maintenance_agent.tools.process import ProcessRunner
from repo_maintenance_agent.tools.workspace import WorkspaceAdapter


@pytest.mark.asyncio
async def test_workspace_materializes_pinned_commit_through_gateway(
    tmp_path: Path,
) -> None:
    remote, commit_sha = _bare_repository(tmp_path)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    state = RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha=commit_sha,
        base_branch="main",
        issue={"title": "Fix the bug", "body": "Reproduction"},
    )
    call = ToolCall(
        task_id=state.task_id,
        tenant_id=state.tenant_id,
        repo_id=state.repo_id,
        commit_sha=state.commit_sha,
        agent="control",
        name="workspace_materialize",
        permission=ToolPermission.CONTROL,
        idempotency_key=f"workspace:{state.task_id}:{state.commit_sha}",
    )
    gateway = ToolGateway(
        policy=PermissionPolicy(),
        adapters={
            "workspace_materialize": WorkspaceAdapter(
                ProcessRunner(allowed_executables={"git"}),
                repository_locators={"owner/repo": str(remote)},
            )
        },
        operation_log=InMemoryOperationLog(),
        workspace_root=workspace_root,
    )

    result = await gateway.execute(call, state)
    replay = await gateway.execute(call, state)

    assert result.success
    assert replay.replayed
    relative = result.output["workspace"]
    assert isinstance(relative, str)
    materialized = workspace_root / relative
    assert materialized.is_dir()
    workspace = (workspace_root / relative).resolve()
    assert workspace.is_relative_to(workspace_root.resolve())
    assert _git(workspace, "rev-parse", "HEAD") == commit_sha
    assert _git(workspace, "branch", "--show-current") == result.output["branch"]
    task_key = hashlib.sha256(state.task_id.encode()).hexdigest()[:24]
    assert result.output["branch"] == f"repoaegis/{task_key}"


def _bare_repository(root: Path) -> tuple[Path, str]:
    source = root / "source"
    source.mkdir()
    _git(source, "init", "--initial-branch=main")
    _git(source, "config", "user.email", "fixture@example.invalid")
    _git(source, "config", "user.name", "Fixture")
    (source / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "fixture")
    commit_sha = _git(source, "rev-parse", "HEAD")
    remote = root / "remote.git"
    _git(root, "clone", "--bare", str(source), str(remote))
    return remote, commit_sha


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()
