from pathlib import Path

import pytest

from repo_maintenance_agent.agents.nodes import AgentRuntime, build_agent_nodes
from repo_maintenance_agent.agents.schemas import PatchProposal, PlanOutput, TaskSpecOutput
from repo_maintenance_agent.domain.models import (
    ApprovalDecision,
    RepoTaskState,
    RiskLevel,
    SearchHit,
    TaskStatus,
)
from repo_maintenance_agent.storage.artifacts import FileArtifactStore


class FakeModel:
    async def structured(self, *, system, input_text, schema):
        if schema is TaskSpecOutput:
            return TaskSpecOutput(
                task_type="bugfix",
                summary="Handle empty configuration",
                acceptance_criteria=["uses defaults", "adds regression test"],
                constraints=["no public API change"],
                unknowns=[],
            )
        if schema is PlanOutput:
            return PlanOutput(
                steps=[
                    {
                        "description": "Update loader",
                        "paths": ["src/config.py"],
                        "verification": "pytest tests/test_config.py",
                    }
                ],
                risk=RiskLevel.HIGH,
                risk_reasons=["configuration behavior"],
            )
        if schema is PatchProposal:
            return PatchProposal(
                summary="Use the safe default.",
                unified_diff="--- a/src/config.py\n+++ b/src/config.py\n",
                changed_files=["src/config.py"],
            )
        raise AssertionError(f"unexpected schema: {schema}")


class FakeSearch:
    async def search(self, query):
        return [
            SearchHit(
                hit_id="hit-1",
                path="src/config.py",
                content="def load_config(): ...",
                score=0.9,
                source="bm25+vector",
                line_start=10,
                line_end=20,
            )
        ]


class UnusedVerifier:
    async def verify(self, task):
        raise AssertionError("verification was not expected")


class FakePatchApplier:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, bytes, tuple[str, ...]]] = []

    async def apply(self, *, workspace, patch, declared_files):
        self.calls.append((workspace, patch, declared_files))
        return declared_files


def task() -> RepoTaskState:
    return RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Empty config crashes", "body": "Use defaults instead"},
    )


@pytest.mark.asyncio
async def test_intake_research_and_planning_produce_evidence_backed_approval(
    tmp_path: Path,
) -> None:
    nodes = build_agent_nodes(
        AgentRuntime(
            model=FakeModel(),
            search=FakeSearch(),
            artifacts=FileArtifactStore(tmp_path),
            verifier=UnusedVerifier(),
            workspace=tmp_path,
            patch_applier=FakePatchApplier(),
        )
    )
    after_intake = await nodes.intake({"task": task(), "trace": []})
    after_research = await nodes.research({"task": after_intake["task"], "trace": []})
    after_plan = await nodes.planning({"task": after_research["task"], "trace": []})
    planned = after_plan["task"]

    assert planned.status is TaskStatus.NEEDS_APPROVAL
    assert planned.risk is RiskLevel.HIGH
    assert planned.task_spec["task_type"] == "bugfix"
    assert planned.evidence[0].locator == "src/config.py:10-20"
    assert planned.plan_hash is not None
    assert after_plan["trace"] == ["planning"]


@pytest.mark.asyncio
async def test_coding_resumes_from_api_approved_state(tmp_path: Path) -> None:
    patch_applier = FakePatchApplier()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=FakeModel(),
            search=FakeSearch(),
            artifacts=FileArtifactStore(tmp_path),
            verifier=UnusedVerifier(),
            workspace=tmp_path,
            patch_applier=patch_applier,
        )
    )
    approved = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .transition(TaskStatus.NEEDS_APPROVAL)
        .model_copy(
            update={
                "plan_hash": "b" * 64,
                "approval": ApprovalDecision(
                    approved=True,
                    approver="tenant-a",
                    plan_hash="b" * 64,
                    reason="Approved after reviewing the scoped plan.",
                ),
            }
        )
        .transition(TaskStatus.CODING)
    )

    result = await nodes.coding({"task": approved, "trace": []})

    assert result["task"].status is TaskStatus.CODING
    assert result["task"].iteration == 1
    assert result["task"].patch_artifact_id is not None
    assert patch_applier.calls[0][0] == tmp_path
    assert patch_applier.calls[0][2] == ("src/config.py",)
