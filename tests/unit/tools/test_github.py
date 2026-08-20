import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from repo_maintenance_agent.domain.models import ToolCall, ToolPermission
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.tools.github import GitHubCliAdapter, LocalDraftRecordAdapter
from repo_maintenance_agent.tools.process import ProcessResult


class FakeRunner:
    def __init__(self, output: str) -> None:
        self.output = output
        self.arguments: list[str] = []
        self.secret_env: dict[str, str] = {}

    async def run(self, arguments, *, cwd, secret_env=None, check=True):
        self.arguments = arguments
        self.secret_env = dict(secret_env or {})
        return ProcessResult(0, self.output, "", 1)


@pytest.mark.asyncio
async def test_issue_reader_uses_structured_json_and_brokered_token(tmp_path: Path) -> None:
    runner = FakeRunner(json.dumps({"number": 7, "title": "Bug", "body": "Details", "labels": []}))
    adapter = GitHubCliAdapter(runner, token=SecretStr("not-a-real-token"))
    call = ToolCall(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        agent="intake",
        name="get_issue",
        permission=ToolPermission.GITHUB_READ,
        arguments={"number": 7},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.output["issue"]["title"] == "Bug"
    assert runner.arguments[:3] == ["gh", "issue", "view"]
    assert runner.secret_env == {"GH_TOKEN": "not-a-real-token"}
    assert "not-a-real-token" not in " ".join(runner.arguments)


@pytest.mark.asyncio
async def test_issue_reader_rejects_invalid_issue_number(tmp_path: Path) -> None:
    adapter = GitHubCliAdapter(FakeRunner("{}"), token=SecretStr("not-a-real-token"))
    call = ToolCall(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        agent="intake",
        name="get_issue",
        permission=ToolPermission.GITHUB_READ,
        arguments={"number": -1},
    )

    with pytest.raises(ValueError, match="issue number"):
        await adapter.execute(call, tmp_path)


@pytest.mark.asyncio
async def test_local_draft_adapter_persists_review_record_without_claiming_real_pr(
    tmp_path: Path,
) -> None:
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    adapter = LocalDraftRecordAdapter(artifacts)
    call = ToolCall(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        agent="pr",
        name="create_draft_pr",
        permission=ToolPermission.GITHUB_WRITE,
        arguments={
            "title": "Fix bug",
            "body": "Verified change.",
            "head": "repoaegis/task-key",
            "base": "main",
        },
    )

    result = await adapter.execute(call, tmp_path)

    assert result.output["draft"] is True
    assert result.output["local_record"] is True
    assert "url" not in result.output
    payload = json.loads((await artifacts.get("tenant-a", result.output["artifact_id"])).decode())
    assert payload["head"] == "repoaegis/task-key"
