from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr

from repo_maintenance_agent.domain.models import ToolCall, ToolResult
from repo_maintenance_agent.domain.ports import ArtifactStore
from repo_maintenance_agent.tools.process import ProcessResult


class Runner(Protocol):
    async def run(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        secret_env: dict[str, str] | None = None,
        check: bool = True,
    ) -> ProcessResult: ...


class GitHubCliAdapter:
    def __init__(self, runner: Runner, *, token: SecretStr) -> None:
        self._runner = runner
        self._token = token

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        if call.name == "get_issue":
            output = await self._get_issue(call, workspace)
        elif call.name == "get_pr_checks":
            output = await self._get_pr_checks(call, workspace)
        elif call.name == "create_draft_pr":
            output = await self._create_draft_pr(call, workspace)
        else:
            raise ValueError(f"unsupported GitHub tool: {call.name}")
        return ToolResult(call_id=call.call_id, success=True, output=output)

    async def _get_issue(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        number = _positive_number(call.arguments.get("number"), "issue number")
        result = await self._run(
            [
                "gh",
                "issue",
                "view",
                str(number),
                "--repo",
                call.repo_id,
                "--json",
                "number,title,body,labels,comments,state,url",
            ],
            workspace,
        )
        issue = json.loads(result.stdout)
        if not isinstance(issue, dict):
            raise ValueError("GitHub issue response was not an object")
        return {"issue": issue}

    async def _get_pr_checks(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        number = _positive_number(call.arguments.get("number"), "pull request number")
        result = await self._run(
            [
                "gh",
                "pr",
                "checks",
                str(number),
                "--repo",
                call.repo_id,
                "--json",
                "name,state,bucket,link,workflow",
            ],
            workspace,
        )
        checks = json.loads(result.stdout)
        return {"checks": checks if isinstance(checks, list) else []}

    async def _create_draft_pr(self, call: ToolCall, workspace: Path) -> dict[str, object]:
        title = _required_text(call.arguments.get("title"), "title", 256)
        body = _required_text(call.arguments.get("body"), "body", 50_000)
        head = _safe_branch(call.arguments.get("head"), "head")
        base = _safe_branch(call.arguments.get("base"), "base")
        private_dir = workspace / ".repo-agent"
        private_dir.mkdir(mode=0o700, exist_ok=True)
        body_file = private_dir / f"{call.call_id}.pr.md"
        body_file.write_text(body, encoding="utf-8")
        try:
            result = await self._run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--draft",
                    "--repo",
                    call.repo_id,
                    "--title",
                    title,
                    "--body-file",
                    str(body_file),
                    "--head",
                    head,
                    "--base",
                    base,
                ],
                workspace,
            )
        finally:
            body_file.unlink(missing_ok=True)
        return {"url": result.stdout.strip(), "draft": True}

    async def _run(self, arguments: list[str], workspace: Path) -> ProcessResult:
        return await self._runner.run(
            arguments,
            cwd=workspace,
            secret_env={"GH_TOKEN": self._token.get_secret_value()},
        )


class LocalDraftRecordAdapter:
    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        del workspace
        if call.name != "create_draft_pr":
            raise ValueError(f"unsupported local draft tool: {call.name}")
        record = {
            "schema_version": "repoaegis-local-draft/v1",
            "repo_id": call.repo_id,
            "commit_sha": call.commit_sha,
            "title": _required_text(call.arguments.get("title"), "title", 256),
            "body": _required_text(call.arguments.get("body"), "body", 50_000),
            "head": _safe_branch(call.arguments.get("head"), "head"),
            "base": _safe_branch(call.arguments.get("base"), "base"),
        }
        artifact_id = await self._artifacts.put(
            call.tenant_id,
            call.task_id,
            "draft-pr.json",
            (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            "application/json",
        )
        return ToolResult(
            call_id=call.call_id,
            success=True,
            output={"draft": True, "local_record": True, "artifact_id": artifact_id},
        )


def _positive_number(value: object, label: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _required_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be non-empty and at most {maximum} characters")
    return value.strip()


def _safe_branch(value: object, label: str) -> str:
    branch = _required_text(value, label, 255)
    if branch.startswith("-") or ".." in branch or branch.endswith(".lock"):
        raise ValueError(f"{label} contains unsafe Git syntax")
    return branch
