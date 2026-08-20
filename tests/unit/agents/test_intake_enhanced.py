from __future__ import annotations

from pathlib import Path

import pytest

from repo_maintenance_agent.agents.nodes import AgentRuntime, build_agent_nodes
from repo_maintenance_agent.agents.schemas import (
    ContextRequest,
    PatchProposal,
    PlanOutput,
    PullRequestDraft,
    TaskSpecOutput,
)
from repo_maintenance_agent.domain.models import RepoTaskState, SearchHit, ToolResult
from repo_maintenance_agent.storage.artifacts import FileArtifactStore


class EnhancedIntakeModel:
    def __init__(self) -> None:
        self.intake_payload = None

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        if schema is TaskSpecOutput:
            self.intake_payload = input_text
            return TaskSpecOutput(
                task_type="bugfix",
                summary="Handle empty configuration",
                acceptance_criteria=["uses defaults"],
                constraints=[],
                unknowns=[],
                issue_classification="bugfix",
                search_hints=["load_config", "src/config.py"],
                key_paths=["src/config.py"],
            )
        if schema is PlanOutput:
            return PlanOutput(
                steps=[
                    {
                        "description": "Update loader",
                        "paths": ["src/config.py"],
                        "verification": "pytest",
                    }
                ],
                risk="low",
                risk_reasons=[],
            )
        if schema is ContextRequest:
            return ContextRequest(ready_to_patch=True, reason="sufficient")
        if schema is PatchProposal:
            return PatchProposal(
                summary="fix",
                edits=[
                    {
                        "path": "src/config.py",
                        "old_text": "def load(): return default",
                        "new_text": "def load(): return safe_default",
                    }
                ],
            )
        if schema is PullRequestDraft:
            return PullRequestDraft(
                title="Fix", body="Fix.", head="repoaegis/task-key", base="main"
            )
        raise AssertionError(f"unexpected schema: {schema}")


class RecordingResearchGateway:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, call, state):
        self.calls.append((call.name, call.arguments))
        if call.name == "search_code":
            hit = SearchHit(
                hit_id=f"hit-{len(self.calls)}",
                path=call.arguments.get("allowed_paths", ["src/config.py"])[0]
                if call.arguments.get("allowed_paths")
                else "src/config.py",
                content="def load_config(): ...",
                score=0.9,
                source="bm25",
                line_start=10,
                line_end=20,
            )
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"hits": [hit.model_dump(mode="json")]},
            )
        if call.name == "read_files":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"files": {"src/config.py": "def load(): return default"}},
            )
        if call.name == "apply_patch":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"changed_files": call.arguments["files"]},
            )
        raise AssertionError(f"unexpected tool: {call.name}")


def task() -> RepoTaskState:
    return RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Empty config crashes", "body": "Use defaults instead"},
    )


@pytest.mark.asyncio
async def test_intake_outputs_structured_classification(tmp_path: Path) -> None:
    model = EnhancedIntakeModel()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=model,
            artifacts=FileArtifactStore(tmp_path),
            gateway=RecordingResearchGateway(),
        )
    )
    after_intake = await nodes.intake({"task": task(), "trace": []})
    spec = after_intake["task"].task_spec
    assert spec["issue_classification"] == "bugfix"
    assert "load_config" in spec["search_hints"]
    assert spec["key_paths"] == ["src/config.py"]


@pytest.mark.asyncio
async def test_research_uses_search_hints_in_rewrite_input(tmp_path: Path) -> None:
    model = EnhancedIntakeModel()
    gateway = RecordingResearchGateway()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=model,
            artifacts=FileArtifactStore(tmp_path),
            gateway=gateway,
        )
    )
    after_intake = await nodes.intake({"task": task(), "trace": []})
    after_research = await nodes.research({"task": after_intake["task"], "trace": []})
    research = after_research["task"]
    assert research.status.name == "RESEARCH"
    assert research.repo_profile["retrieval_count"] >= 1
    search_calls = [call for call in gateway.calls if call[0] == "search_code"]
    assert search_calls, "research must issue search_code calls"
    texts = [call[1].get("text", "") for call in search_calls]
    assert any("load_config" in text or "src/config.py" in text for text in texts)
