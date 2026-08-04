import shutil
import subprocess
from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import ToolCall, ToolPermission
from repo_maintenance_agent.tools.git import GitToolAdapter
from repo_maintenance_agent.tools.process import ProcessRunner


def initialize_repository(path: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required for integration tests")
    subprocess.run([git, "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run([git, "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run([git, "config", "user.name", "Test"], cwd=path, check=True)
    (path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run([git, "add", "app.py"], cwd=path, check=True)
    subprocess.run([git, "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)
    return subprocess.run(
        [git, "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.asyncio
async def test_git_adapter_returns_structured_status_and_log(tmp_path: Path) -> None:
    commit = initialize_repository(tmp_path)
    adapter = GitToolAdapter(ProcessRunner(allowed_executables={"git"}))
    base = {
        "task_id": "task-1",
        "tenant_id": "tenant-a",
        "repo_id": "owner/repo",
        "commit_sha": commit,
        "agent": "research",
        "permission": ToolPermission.REPO_READ,
    }

    status = await adapter.execute(ToolCall(name="git_status", **base), tmp_path)
    history = await adapter.execute(
        ToolCall(name="git_log", arguments={"limit": 5}, **base),
        tmp_path,
    )

    assert status.success
    assert status.output["branch"] == "main"
    assert status.output["changes"] == []
    assert history.output["commits"][0]["subject"] == "initial"
    assert history.output["commits"][0]["sha"] == commit


@pytest.mark.asyncio
async def test_git_adapter_rejects_unregistered_subcommand(tmp_path: Path) -> None:
    commit = initialize_repository(tmp_path)
    adapter = GitToolAdapter(ProcessRunner(allowed_executables={"git"}))
    call = ToolCall(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha=commit,
        agent="research",
        name="git_reset",
        permission=ToolPermission.REPO_READ,
    )

    with pytest.raises(ValueError, match="unsupported git tool"):
        await adapter.execute(call, tmp_path)


@pytest.mark.asyncio
async def test_git_adapter_commits_allowlisted_files_and_pushes_branch(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    initial = initialize_repository(workspace)
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(workspace), str(remote)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)],
        cwd=workspace,
        check=True,
    )
    branch = "repoaegis/task-key"
    subprocess.run(
        ["git", "switch", "--create", branch],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    (workspace / "app.py").write_text("print('fixed')\n", encoding="utf-8")
    adapter = GitToolAdapter(ProcessRunner(allowed_executables={"git"}))
    base = {
        "task_id": "task-1",
        "tenant_id": "tenant-a",
        "repo_id": "owner/repo",
        "commit_sha": initial,
        "agent": "pr",
        "permission": ToolPermission.GIT_WRITE,
    }

    committed = await adapter.execute(
        ToolCall(
            name="git_commit",
            arguments={"files": ["app.py"], "message": "Fix app"},
            idempotency_key="commit:task-1:1",
            **base,
        ),
        workspace,
    )
    pushed = await adapter.execute(
        ToolCall(
            name="git_push",
            arguments={"remote": "origin", "branch": branch},
            idempotency_key="push:task-1:1",
            **base,
        ),
        workspace,
    )

    remote_sha = subprocess.run(
        ["git", "rev-parse", f"refs/heads/{branch}"],
        cwd=remote,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert committed.output["commit_sha"] == remote_sha
    assert pushed.output == {"pushed": True, "branch": branch}
