from __future__ import annotations

from pathlib import Path
from typing import Protocol

from repo_maintenance_agent.domain.models import (
    SearchQuery,
    ToolCall,
    ToolResult,
    VerificationResult,
)
from repo_maintenance_agent.domain.ports import ArtifactStore, SearchPort


class PatchApplier(Protocol):
    async def apply(
        self,
        *,
        workspace: Path,
        patch: bytes,
        declared_files: tuple[str, ...],
    ) -> tuple[str, ...]: ...


class TaskVerifier(Protocol):
    async def verify_task(self, task_id: str) -> VerificationResult: ...


class PatchArtifactAdapter:
    def __init__(self, *, artifacts: ArtifactStore, applier: PatchApplier) -> None:
        self._artifacts = artifacts
        self._applier = applier

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        if call.name != "apply_patch":
            raise ValueError(f"unsupported patch tool: {call.name}")
        artifact_id = call.arguments.get("artifact_id")
        files = call.arguments.get("files")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ValueError("patch artifact ID is required")
        if (
            not isinstance(files, list)
            or not files
            or any(not isinstance(path, str) or not path for path in files)
        ):
            raise ValueError("declared patch files are required")
        patch = await self._artifacts.get(call.tenant_id, artifact_id)
        changed_files = await self._applier.apply(
            workspace=workspace,
            patch=patch,
            declared_files=tuple(files),
        )
        return ToolResult(
            call_id=call.call_id,
            success=True,
            output={"changed_files": list(changed_files)},
        )


class VerificationAdapter:
    def __init__(self, verifier: TaskVerifier) -> None:
        self._verifier = verifier

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        del workspace
        if call.name != "run_verification":
            raise ValueError(f"unsupported verification tool: {call.name}")
        result = await self._verifier.verify_task(call.task_id)
        return ToolResult(
            call_id=call.call_id,
            success=True,
            output={"verification": result.model_dump(mode="json")},
        )


class SearchAdapter:
    def __init__(self, search: SearchPort) -> None:
        self._search = search

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        del workspace
        if call.name != "search_code":
            raise ValueError(f"unsupported search tool: {call.name}")
        text = call.arguments.get("text")
        allowed_paths = call.arguments.get("allowed_paths", [])
        top_k = call.arguments.get("top_k", 15)
        if not isinstance(text, str) or not text:
            raise ValueError("search text is required")
        if not isinstance(allowed_paths, list) or any(
            not isinstance(path, str) for path in allowed_paths
        ):
            raise ValueError("search allowed paths must be strings")
        if not isinstance(top_k, int):
            raise ValueError("search top_k must be an integer")
        query = SearchQuery(
            tenant_id=call.tenant_id,
            repo_id=call.repo_id,
            commit_sha=call.commit_sha,
            text=text,
            allowed_paths=tuple(allowed_paths),
            top_k=top_k,
        )
        hits = await self._search.search(query)
        return ToolResult(
            call_id=call.call_id,
            success=True,
            output={"hits": [hit.model_dump(mode="json") for hit in hits]},
        )


class WorkspaceReadAdapter:
    def __init__(self, *, max_total_bytes: int = 200_000) -> None:
        if max_total_bytes < 1:
            raise ValueError("workspace read limit must be positive")
        self._max_total_bytes = max_total_bytes

    async def execute(self, call: ToolCall, workspace: Path) -> ToolResult:
        if call.name != "read_files":
            raise ValueError(f"unsupported workspace read tool: {call.name}")
        raw_files = call.arguments.get("files")
        if (
            not isinstance(raw_files, list)
            or not raw_files
            or len(raw_files) > 100
            or any(not isinstance(path, str) or not path for path in raw_files)
        ):
            raise ValueError("workspace read files are required")
        root = workspace.resolve()
        remaining = self._max_total_bytes
        contents: dict[str, str | dict[str, str]] = {}
        for relative in raw_files:
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root):
                raise ValueError("workspace read path resolves outside workspace")
            if not candidate.is_file():
                contents[relative] = {"error": "not_found"}
                continue
            data = candidate.read_bytes()
            if len(data) > remaining:
                raise ValueError("workspace read exceeds total byte limit")
            remaining -= len(data)
            contents[relative] = data.decode("utf-8", errors="replace")
        return ToolResult(call_id=call.call_id, success=True, output={"files": contents})
