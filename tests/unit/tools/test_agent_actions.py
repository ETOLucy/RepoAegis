from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import (
    SearchHit,
    ToolCall,
    ToolPermission,
    VerificationResult,
)
from repo_maintenance_agent.search.codegraph import build_repo_graph
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.tools.agent_actions import (
    GraphAdapter,
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
async def test_workspace_reader_marks_files_over_remaining_budget(tmp_path: Path) -> None:
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_bytes(b"1234")
    second.write_bytes(b"5678")
    adapter = WorkspaceReadAdapter(max_total_bytes=6)
    call = _call(
        name="read_files",
        permission=ToolPermission.REPO_READ,
        arguments={"files": ["first.py", "second.py"]},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.output == {
        "files": {
            "first.py": "1234",
            "second.py": {"error": "byte_limit"},
        }
    }


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
            "goto_definition": "localizer",
            "find_references": "localizer",
        }[name],
        name=name,
        permission=permission,
        arguments=arguments or {},
    )


@pytest.mark.asyncio
async def test_workspace_reader_marks_missing_files_without_aborting(tmp_path: Path) -> None:
    source = tmp_path / "src" / "app.py"
    source.parent.mkdir()
    source.write_bytes(b"def app():\n    return 1\n")
    adapter = WorkspaceReadAdapter(max_total_bytes=10_000)
    call = _call(
        name="read_files",
        permission=ToolPermission.REPO_READ,
        arguments={"files": ["src/app.py", "missing/release.yml", "README.md"]},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.success is True
    assert result.output["files"]["src/app.py"] == "def app():\n    return 1\n"
    assert result.output["files"]["missing/release.yml"] == {"error": "not_found"}
    assert result.output["files"]["README.md"] == {"error": "not_found"}


@pytest.mark.asyncio
async def test_graph_adapter_goto_definition_returns_matching_definitions(
    tmp_path: Path,
) -> None:
    (tmp_path / "svc.py").write_text(
        "class Service:\n    def handle(self, request):\n        return True\n",
        encoding="utf-8",
    )
    adapter = GraphAdapter(tmp_path, build_repo_graph(tmp_path))
    call = _call(
        name="goto_definition",
        permission=ToolPermission.REPO_READ,
        arguments={"symbol": "Service.handle"},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.success is True
    definitions = result.output["definitions"]
    assert len(definitions) == 1
    assert definitions[0]["path"] == "svc.py"
    assert definitions[0]["line_start"] == 2


@pytest.mark.asyncio
async def test_graph_adapter_find_references_returns_call_sites(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "def helper():\n    pass\n\ndef caller():\n    helper()\n",
        encoding="utf-8",
    )
    adapter = GraphAdapter(tmp_path, build_repo_graph(tmp_path))
    call = _call(
        name="find_references",
        permission=ToolPermission.REPO_READ,
        arguments={"symbol": "helper"},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.success is True
    references = result.output["references"]
    assert len(references) == 1
    assert references[0]["line_start"] == 5


@pytest.mark.asyncio
async def test_graph_adapter_requires_a_symbol_argument(tmp_path: Path) -> None:
    adapter = GraphAdapter(tmp_path, build_repo_graph(tmp_path))
    call = _call(name="goto_definition", permission=ToolPermission.REPO_READ, arguments={})

    with pytest.raises(ValueError, match="symbol"):
        await adapter.execute(call, tmp_path)


@pytest.mark.asyncio
async def test_graph_adapter_unknown_symbol_returns_empty_list(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def known():\n    pass\n", encoding="utf-8")
    adapter = GraphAdapter(tmp_path, build_repo_graph(tmp_path))
    call = _call(
        name="goto_definition",
        permission=ToolPermission.REPO_READ,
        arguments={"symbol": "nope"},
    )

    result = await adapter.execute(call, tmp_path)

    assert result.output["definitions"] == []
