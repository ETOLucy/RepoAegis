from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import (
    SearchHit,
    ToolCall,
    ToolPermission,
    VerificationResult,
)
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.tools.agent_actions import (
    PatchArtifactAdapter,
    SearchAdapter,
    VerificationAdapter,
    WorkspaceReadAdapter,
)


class RecordingPatchApplier:
    def __init__(self) -> None:
        self.patch = b""
        self.files: tuple[str, ...] = ()

    async def apply(self, *, workspace, patch, declared_files):
        self.patch = patch
        self.files = declared_files
        return declared_files


class PassingVerifier:
    def __init__(self) -> None:
        self.task_ids: list[str] = []

    async def verify_task(self, task_id: str) -> VerificationResult:
        self.task_ids.append(task_id)
        return VerificationResult(passed=True, commands=("pytest",), summary="passed")


class FixedSearch:
    async def search(self, query):
        return [
            SearchHit(
                hit_id="hit-1",
                path="src/app.py",
                content="def app(): ...",
                score=0.8,
                source="lexical",
                line_start=1,
                line_end=1,
            )
        ]


@pytest.mark.asyncio
async def test_patch_adapter_reads_artifact_and_applies_declared_files(
    tmp_path: Path,
) -> None:
    artifacts = FileArtifactStore(tmp_path / "artifacts")
    artifact_id = await artifacts.put(
        "tenant-a",
        "task-1",
        "proposed.patch",
        b"patch-bytes",
        "text/x-diff",
    )
    applier = RecordingPatchApplier()
    adapter = PatchArtifactAdapter(artifacts=artifacts, applier=applier)
    call = _call(
        name="apply_patch",
        permission=ToolPermission.SANDBOX_WRITE,
        arguments={"artifact_id": artifact_id, "files": ["src/app.py"]},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.success
    assert result.output == {"changed_files": ["src/app.py"]}
    assert applier.patch == b"patch-bytes"
    assert applier.files == ("src/app.py",)


@pytest.mark.asyncio
async def test_verification_adapter_returns_structured_verifier_result(
    tmp_path: Path,
) -> None:
    verifier = PassingVerifier()
    adapter = VerificationAdapter(verifier)
    call = _call(
        name="run_verification",
        permission=ToolPermission.SANDBOX_EXECUTE,
    )

    result = await adapter.execute(call, tmp_path)

    assert result.success
    assert result.output["verification"]["passed"] is True
    assert verifier.task_ids == ["task-1"]


@pytest.mark.asyncio
async def test_search_adapter_returns_structured_hits(tmp_path: Path) -> None:
    adapter = SearchAdapter(FixedSearch())
    call = _call(
        name="search_code",
        permission=ToolPermission.REPO_READ,
        arguments={"text": "app", "allowed_paths": [], "top_k": 5},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.success
    assert result.output["hits"][0]["path"] == "src/app.py"


@pytest.mark.asyncio
async def test_workspace_reader_returns_bounded_changed_source(tmp_path: Path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_bytes(b"def app():\n    return 1\n")
    adapter = WorkspaceReadAdapter(max_total_bytes=1_000)
    call = _call(
        name="read_files",
        permission=ToolPermission.REPO_READ,
        arguments={"files": ["src/app.py"]},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.output == {"files": {"src/app.py": "def app():\n    return 1\n"}}


@pytest.mark.asyncio
async def test_workspace_reader_rejects_symlink_or_path_escape(tmp_path: Path) -> None:
    adapter = WorkspaceReadAdapter(max_total_bytes=1_000)
    call = _call(
        name="read_files",
        permission=ToolPermission.REPO_READ,
        arguments={"files": ["../outside.py"]},
    )

    with pytest.raises(ValueError, match="outside"):
        await adapter.execute(call, tmp_path)


def _call(
    *,
    name: str,
    permission: ToolPermission,
    arguments: dict[str, object] | None = None,
) -> ToolCall:
    return ToolCall(
        task_id="task-1",
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        agent={
            "apply_patch": "coding",
            "run_verification": "verification",
            "search_code": "research",
            "read_files": "review",
        }[name],
        name=name,
        permission=permission,
        arguments=arguments or {},
    )
