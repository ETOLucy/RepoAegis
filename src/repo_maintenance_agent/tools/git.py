from __future__ import annotations

from pathlib import Path

from repo_maintenance_agent.domain.models import ToolCall, ToolResult
from repo_maintenance_agent.tools.process import ProcessRunner


class GitToolAdapter:
    def __init__(self, runner: ProcessRunner) -> None:
        self._runner = runner

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        handlers = {
            "git_status": self._status,
            "git_log": self._log,
            "git_diff": self._diff,
            "git_show": self._show,
        }
        handler = handlers.get(call.name)
        if handler is None:
            raise ValueError(f"unsupported git tool: {call.name}")
        output = await handler(call, workspace)
        return ToolResult(call_id=call.call_id, success=True, output=output)

    async def _status(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        del call
        result = await self._runner.run(
            ["git", "status", "--porcelain=v1", "--branch"],
            cwd=workspace,
        )
        lines = result.stdout.splitlines()
        branch = lines[0].removeprefix("## ").split("...", maxsplit=1)[0] if lines else ""
        return {"branch": branch, "changes": lines[1:]}

    async def _log(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        raw_limit = call.arguments.get("limit", 20)
        if not isinstance(raw_limit, int) or not 1 <= raw_limit <= 100:
            raise ValueError("git_log limit must be between 1 and 100")
        result = await self._runner.run(
            [
                "git",
                "log",
                f"--max-count={raw_limit}",
                "--format=%H%x1f%an%x1f%aI%x1f%s",
            ],
            cwd=workspace,
        )
        commits = []
        for line in result.stdout.splitlines():
            sha, author, authored_at, subject = line.split("\x1f", maxsplit=3)
            commits.append(
                {
                    "sha": sha,
                    "author": author,
                    "authored_at": authored_at,
                    "subject": subject,
                }
            )
        return {"commits": commits}

    async def _diff(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        ref = call.arguments.get("ref", call.commit_sha)
        if not isinstance(ref, str) or ref.startswith("-"):
            raise ValueError("invalid diff reference")
        result = await self._runner.run(
            ["git", "diff", "--no-ext-diff", "--unified=3", ref, "--"],
            cwd=workspace,
        )
        return {"diff": result.stdout}

    async def _show(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        ref = call.arguments.get("ref", call.commit_sha)
        if not isinstance(ref, str) or ref.startswith("-"):
            raise ValueError("invalid show reference")
        result = await self._runner.run(
            ["git", "show", "--no-ext-diff", "--format=fuller", "--stat", ref, "--"],
            cwd=workspace,
        )
        return {"show": result.stdout}

