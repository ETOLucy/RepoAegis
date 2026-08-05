import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import (
    RepoTaskState,
    TaskStatus,
    VerificationResult,
)
from repo_maintenance_agent.graph.builder import AgentNodes, build_graph
from repo_maintenance_agent.graph.state import GraphState
from repo_maintenance_agent.policies.permissions import PermissionPolicy
from repo_maintenance_agent.runtime_executor import WorkspaceGraphExecutor
from repo_maintenance_agent.tools.gateway import InMemoryOperationLog, ToolGateway
from repo_maintenance_agent.tools.process import ProcessRunner
from repo_maintenance_agent.tools.workspace import WorkspaceAdapter

Node = Callable[[GraphState], Awaitable[dict[str, object]]]


@pytest.mark.asyncio
async def test_executor_materializes_task_workspace_before_running_graph(
    tmp_path: Path,
) -> None:
    remote, commit_sha = _bare_repository(tmp_path)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    gateway = ToolGateway(
        policy=PermissionPolicy(),
        adapters={
            "workspace_materialize": WorkspaceAdapter(
                ProcessRunner(allowed_executables={"git"}),
                repository_locators={"owner/repo": str(remote)},
            ),
            "workspace_prepare": WorkspaceAdapter(
                ProcessRunner(allowed_executables={"git"}),
                repository_locators={"owner/repo": str(remote)},
            ),
        },
        operation_log=InMemoryOperationLog(),
        workspace_root=workspace_root,
    )
    graph_workspaces: list[Path] = []

    def graph_factory(workspace: Path):
        graph_workspaces.append(workspace)
        return build_graph(_successful_nodes())

    executor = WorkspaceGraphExecutor(
        gateway=gateway,
        workspace_root=workspace_root,
        graph_factory=graph_factory,
    )
    task = RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha=commit_sha,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
    )

    result = await executor.execute(task)

    assert result.status is TaskStatus.COMPLETED
    assert len(graph_workspaces) == 1
    assert graph_workspaces[0].is_relative_to(workspace_root.resolve())
    assert _git(graph_workspaces[0], "rev-parse", "HEAD") == commit_sha


def _successful_nodes() -> AgentNodes:
    return AgentNodes(
        intake=_transition_node(TaskStatus.INTAKE, "intake"),
        research=_transition_node(TaskStatus.RESEARCH, "research"),
        planning=_transition_node(TaskStatus.PLANNING, "planning"),
        approval=_transition_node(TaskStatus.CODING, "approval"),
        coding=_transition_node(TaskStatus.CODING, "coding", iteration=1),
        verification=_transition_node(
            TaskStatus.VERIFYING,
            "verification",
            verification=VerificationResult(passed=True),
        ),
        review=_transition_node(
            TaskStatus.REVIEWING,
            "review",
            review={"decision": "approve"},
        ),
        pr=_transition_node(TaskStatus.DELIVERING, "pr"),
        failure=_transition_node(TaskStatus.FAILED, "failure"),
    )


def _transition_node(target: TaskStatus, name: str, **updates: object) -> Node:
    async def node(graph_state: GraphState) -> dict[str, object]:
        task = graph_state["task"].transition(target).model_copy(update=updates)
        return {"task": task, "trace": [name]}

    return node


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

@pytest.mark.asyncio
async def test_executor_cleans_leftover_files_on_replayed_retry(
    tmp_path: Path,
) -> None:
    """A retried graph execution must start from a clean tree.

    The materialization call is replayed from the operation log on retry, so the
    reset/clean in the adapter would be skipped without the explicit
    workspace_prepare step. A previous failed attempt can leave untracked files
    behind (for example a patch that created BENCHMARK_GUIDE.md before a later
    node failed); patch application on the retry would then fail with
    "already exists in working directory".
    """
    remote, commit_sha = _bare_repository(tmp_path)
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    gateway = ToolGateway(
        policy=PermissionPolicy(),
        adapters={
            "workspace_materialize": WorkspaceAdapter(
                ProcessRunner(allowed_executables={"git"}),
                repository_locators={"owner/repo": str(remote)},
            ),
            "workspace_prepare": WorkspaceAdapter(
                ProcessRunner(allowed_executables={"git"}),
                repository_locators={"owner/repo": str(remote)},
            ),
        },
        operation_log=InMemoryOperationLog(),
        workspace_root=workspace_root,
    )
    run_count = 0
    workspaces: list[Path] = []

    async def coding_node(graph_state: GraphState) -> dict[str, object]:
        nonlocal run_count
        run_count += 1
        workspace = workspaces[0]
        leftover = workspace / "leftover.txt"
        if run_count > 1:
            assert not leftover.exists(), "retry must start from a clean tree"
        leftover.write_text("untracked", encoding="utf-8")
        task = graph_state["task"].transition(TaskStatus.CODING).model_copy(
            update={"iteration": 1}
        )
        return {"task": task, "trace": ["coding"]}

    def graph_factory(workspace: Path):
        workspaces.append(workspace)
        return build_graph(
            AgentNodes(
                intake=_transition_node(TaskStatus.INTAKE, "intake"),
                research=_transition_node(TaskStatus.RESEARCH, "research"),
                planning=_transition_node(TaskStatus.PLANNING, "planning"),
                approval=_transition_node(TaskStatus.CODING, "approval"),
                coding=coding_node,
                verification=_transition_node(
                    TaskStatus.VERIFYING,
                    "verification",
                    verification=VerificationResult(passed=True),
                ),
                review=_transition_node(
                    TaskStatus.REVIEWING,
                    "review",
                    review={"decision": "approve"},
                ),
                pr=_transition_node(TaskStatus.DELIVERING, "pr"),
                failure=_transition_node(TaskStatus.FAILED, "failure"),
            )
        )

    executor = WorkspaceGraphExecutor(
        gateway=gateway,
        workspace_root=workspace_root,
        graph_factory=graph_factory,
    )
    task = RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha=commit_sha,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
    )

    first = await executor.execute(task)
    assert first.status is TaskStatus.COMPLETED
    assert run_count == 1
    assert (workspaces[0] / "leftover.txt").exists()

    # Same task executed again (worker retry after a failed graph attempt).
    # Materialization is replayed; workspace_prepare must still clean the tree.
    second = await executor.execute(task)
    assert second.status is TaskStatus.COMPLETED
    assert run_count == 2
    assert (workspaces[0] / "leftover.txt").exists()
