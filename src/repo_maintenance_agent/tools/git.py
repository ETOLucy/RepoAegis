from __future__ import annotations

from pathlib import Path, PurePosixPath

from repo_maintenance_agent.domain.errors import ToolExecutionError
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
            "git_blame": self._blame,
            "git_commit": self._commit,
            "git_push": self._push,
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
        if not isinstance(ref, str) or not ref.strip() or ref.startswith("-"):
            raise ValueError("invalid diff reference")
        # Use --no-color-moved-ws=no explicitly to avoid git version compatibility issues
        result = await self._runner.run(
            ["git", "diff", "--no-ext-diff", "--unified=3", ref, "--"],
            cwd=workspace,
            check=False,
        )
        if result.returncode != 0:
            # Fallback: try without the ref (compare working tree to HEAD)
            fallback = await self._runner.run(
                ["git", "diff", "--no-ext-diff", "--unified=3", "--"],
                cwd=workspace,
                check=False,
            )
            if fallback.returncode != 0:
                raise ToolExecutionError(
                    f"git diff failed (exit {result.returncode}): {result.stderr[-500:].strip()}"
                )
            return {"diff": fallback.stdout}
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

    async def _commit(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        files = _safe_files(call.arguments.get("files"))
        message = _required_text(call.arguments.get("message"), "commit message", 500)
        operation = _required_text(call.idempotency_key, "commit idempotency key", 200)
        await self._runner.run(["git", "add", "--", *files], cwd=workspace)
        staged = await self._runner.run(
            ["git", "diff", "--cached", "--quiet", "--"],
            cwd=workspace,
            check=False,
        )
        trailer = f"RepoAegis-Operation: {operation}"
        if staged.returncode == 0:
            existing = await self._runner.run(
                ["git", "log", "-1", "--format=%H%n%B"],
                cwd=workspace,
            )
            lines = existing.stdout.splitlines()
            if not lines or trailer not in lines[1:]:
                raise ValueError("no staged change matches the commit operation")
            return {"commit_sha": lines[0]}
        await self._runner.run(
            ["git", "commit", "-m", message, "-m", trailer],
            cwd=workspace,
            extra_env={
                "GIT_AUTHOR_NAME": "RepoAegis",
                "GIT_AUTHOR_EMAIL": "repoaegis@example.invalid",
                "GIT_COMMITTER_NAME": "RepoAegis",
                "GIT_COMMITTER_EMAIL": "repoaegis@example.invalid",
            },
        )
        result = await self._runner.run(["git", "rev-parse", "HEAD"], cwd=workspace)
        return {"commit_sha": result.stdout.strip()}

    async def _push(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        remote = call.arguments.get("remote")
        branch = _safe_branch(call.arguments.get("branch"))
        if remote != "origin":
            raise ValueError("git push remote must be origin")
        current = await self._runner.run(
            ["git", "branch", "--show-current"],
            cwd=workspace,
        )
        if current.stdout.strip() != branch:
            raise ValueError("git push branch does not match the workspace")
        await self._runner.run(
            ["git", "push", "--set-upstream", "origin", branch],
            cwd=workspace,
        )
        return {"pushed": True, "branch": branch}

    async def _blame(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        path = call.arguments.get("path")
        if not isinstance(path, str) or not path:
            raise ValueError("git blame path is required")
        candidate = (workspace / path).resolve()
        if not candidate.is_relative_to(workspace.resolve()):
            raise ValueError("git blame path resolves outside workspace")
        result = await self._runner.run(
            ["git", "blame", "--", path],
            cwd=workspace,
            check=False,
        )
        if result.returncode != 0:
            raise ToolExecutionError(f"git blame failed: {result.stderr[-500:].strip()}")
        return {"blame": result.stdout}


def _required_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-empty and at most {maximum} characters")
    return value.strip()


def _safe_files(value: object) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError("git commit files are required")
    files: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("git commit files must be strings")
        normalized = raw.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or normalized.startswith("-"):
            raise ValueError("git commit file path is unsafe")
        files.append(path.as_posix())
    return files


def _safe_branch(value: object) -> str:
    branch = _required_text(value, "branch", 255)
    if branch.startswith("-") or ".." in branch or branch.endswith(".lock"):
        raise ValueError("branch contains unsafe Git syntax")
    return branch

