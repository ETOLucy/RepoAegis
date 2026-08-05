from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.domain.models import ToolCall, ToolResult
from repo_maintenance_agent.tools.process import ProcessRunner


class WorkspaceAdapter:
    def __init__(
        self,
        runner: ProcessRunner,
        *,
        repository_locators: Mapping[str, str],
    ) -> None:
        if not repository_locators or any(not value for value in repository_locators.values()):
            raise ValueError("repository locator registry must not be empty")
        self._runner = runner
        self._repository_locators = dict(repository_locators)

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        if call.name != "workspace_materialize":
            raise ValueError(f"unsupported workspace tool: {call.name}")
        locator = self._repository_locators.get(call.repo_id)
        if locator is None:
            raise ToolExecutionError("repository is not registered")
        root = workspace.resolve()
        tenant_key = hashlib.sha256(call.tenant_id.encode()).hexdigest()[:24]
        task_key = hashlib.sha256(call.task_id.encode()).hexdigest()[:24]
        relative = Path(tenant_key) / task_key
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise ToolExecutionError("workspace path escaped root")
        branch = f"repoaegis/{task_key}"
        if target.exists():
            await self._verify_existing(target, call.commit_sha, branch)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            await self._runner.run(
                ["git", "clone", "--no-checkout", "--no-tags", "--", locator, str(target)],
                cwd=root,
            )
            await self._runner.run(
                ["git", "-c", "advice.detachedHead=false", "checkout", "--detach", call.commit_sha],
                cwd=target,
            )
            await self._assert_head(target, call.commit_sha)
            await self._runner.run(
                ["git", "switch", "--create", branch],
                cwd=target,
            )
        self._make_workspace_shared(target)
        return ToolResult(
            call_id=call.call_id,
            success=True,
            output={"workspace": relative.as_posix(), "branch": branch},
        )

    async def _verify_existing(self, target: Path, commit_sha: str, branch: str) -> None:
        await self._assert_head(target, commit_sha)
        current = await self._runner.run(
            ["git", "branch", "--show-current"],
            cwd=target,
        )
        if current.stdout.strip() != branch:
            raise ToolExecutionError("existing workspace branch does not match task")

    async def _assert_head(self, target: Path, commit_sha: str) -> None:
        result = await self._runner.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
        )
        if result.stdout.strip() != commit_sha:
            raise ToolExecutionError("materialized workspace does not match pinned commit")

    def _make_workspace_shared(self, target: Path) -> None:
        """Make the task workspace writable by the nested sandbox daemon's uid
        namespace. Rootless dind maps container uids to different host uids, so a
        workspace owned by the worker uid is not writable inside nested containers.
        The sandbox container itself remains non-root, capability-dropped, and
        network-less; this only relaxes ownership on the disposable task volume.
        """
        for path in target.rglob("*"):
            try:
                path.chmod(path.stat().st_mode | 0o222)
            except OSError:
                continue
        try:
            target.chmod(target.stat().st_mode | 0o222)
        except OSError:
            pass

