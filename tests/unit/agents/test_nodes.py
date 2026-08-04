import json
from pathlib import Path

import pytest

from repo_maintenance_agent.agents.nodes import AgentRuntime, build_agent_nodes
from repo_maintenance_agent.agents.schemas import (
    PatchProposal,
    PlanOutput,
    PullRequestDraft,
    ReviewOutput,
    TaskSpecOutput,
)
from repo_maintenance_agent.domain.models import (
    ApprovalDecision,
    RepoTaskState,
    RiskLevel,
    SearchHit,
    TaskStatus,
    ToolPermission,
    ToolResult,
    VerificationResult,
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
        if schema is PullRequestDraft:
            return PullRequestDraft(
                title="Fix empty configuration",
                body="Applies the verified default behavior.",
                head="repoaegis/task-key",
                base="main",
            )
        raise AssertionError(f"unexpected schema: {schema}")


class LowRiskDependencyModel(FakeModel):
    async def structured(self, *, system, input_text, schema):
        if schema is PlanOutput:
            return PlanOutput(
                steps=[
                    {
                        "description": "Update dependency constraint",
                        "paths": ["pyproject.toml"],
                        "verification": "python -m pip check",
                    }
                ],
                risk=RiskLevel.LOW,
                risk_reasons=[],
            )
        return await super().structured(system=system, input_text=input_text, schema=schema)


class RecordingReviewModel(FakeModel):
    def __init__(self) -> None:
        self.review_input: dict[str, object] = {}

    async def structured(self, *, system, input_text, schema):
        if schema is ReviewOutput:
            self.review_input = json.loads(input_text)
            return ReviewOutput(decision="approve", findings=[], summary="Scope and tests pass.")
        return await super().structured(system=system, input_text=input_text, schema=schema)


class RecordingGateway:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, call, state):
        self.calls.append((call, state))
        if call.name == "search_code":
            hit = SearchHit(
                hit_id="hit-1",
                path="src/config.py",
                content="def load_config(): ...",
                score=0.9,
                source="bm25+vector",
                line_start=10,
                line_end=20,
            )
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"hits": [hit.model_dump(mode="json")]},
            )
        if call.name == "apply_patch":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"changed_files": call.arguments["files"]},
            )
        if call.name == "run_verification":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={
                    "verification": VerificationResult(
                        passed=True,
                        commands=("pytest",),
                        summary="checks passed",
                    ).model_dump(mode="json")
                },
            )
        if call.name == "git_diff":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"diff": "diff --git a/src/config.py b/src/config.py\n+return default"},
            )
        if call.name == "read_files":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"files": {"src/config.py": "def load(): return default"}},
            )
        if call.name == "git_commit":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"commit_sha": "c" * 40},
            )
        if call.name == "git_push":
            return ToolResult(call_id=call.call_id, success=True, output={"pushed": True})
        if call.name == "create_draft_pr":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"url": "https://example.invalid/pr/1", "draft": True},
            )
        raise AssertionError(f"unexpected tool call: {call.name}")


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
            artifacts=FileArtifactStore(tmp_path),
            gateway=RecordingGateway(),
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
async def test_planning_deterministically_elevates_dependency_changes(tmp_path: Path) -> None:
    nodes = build_agent_nodes(
        AgentRuntime(
            model=LowRiskDependencyModel(),
            artifacts=FileArtifactStore(tmp_path),
            gateway=RecordingGateway(),
        )
    )
    planning = task().transition(TaskStatus.INTAKE).transition(TaskStatus.RESEARCH)

    result = await nodes.planning({"task": planning, "trace": []})
    planned = result["task"]

    assert planned.status is TaskStatus.NEEDS_APPROVAL
    assert planned.risk is RiskLevel.HIGH
    assert "dependency manifest: pyproject.toml" in planned.risk_reasons
    assert planned.declared_files == ("pyproject.toml",)
    assert planned.verification_plan == ("python -m pip check",)
    assert ToolPermission.GIT_WRITE in planned.allowed_tools


@pytest.mark.asyncio
async def test_coding_resumes_from_api_approved_state(tmp_path: Path) -> None:
    gateway = RecordingGateway()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=FakeModel(),
            artifacts=FileArtifactStore(tmp_path),
            gateway=gateway,
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
                    target_commit="a" * 40,
                    allowed_tools=(),
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
    call = gateway.calls[0][0]
    assert call.name == "apply_patch"
    assert call.arguments["files"] == ["src/config.py"]
    assert call.arguments["artifact_id"] == result["task"].patch_artifact_id
    assert call.idempotency_key is not None


@pytest.mark.asyncio
async def test_verification_runs_through_gateway(tmp_path: Path) -> None:
    gateway = RecordingGateway()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=FakeModel(),
            artifacts=FileArtifactStore(tmp_path),
            gateway=gateway,
        )
    )
    coding = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .transition(TaskStatus.CODING)
        .model_copy(update={"iteration": 1})
    )

    result = await nodes.verification({"task": coding, "trace": []})

    assert result["task"].verification is not None
    assert result["task"].verification.passed
    call = gateway.calls[0][0]
    assert call.name == "run_verification"
    assert call.idempotency_key == "verification:1"


@pytest.mark.asyncio
async def test_review_receives_real_diff_changed_source_and_acceptance_criteria(
    tmp_path: Path,
) -> None:
    model = RecordingReviewModel()
    gateway = RecordingGateway()
    nodes = build_agent_nodes(
        AgentRuntime(model=model, artifacts=FileArtifactStore(tmp_path), gateway=gateway)
    )
    reviewing = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .transition(TaskStatus.CODING)
        .transition(TaskStatus.VERIFYING)
        .model_copy(
            update={
                "task_spec": {"acceptance_criteria": ["uses defaults"]},
                "changed_files": ("src/config.py",),
                "verification": VerificationResult(passed=True, commands=("pytest",)),
            }
        )
    )

    result = await nodes.review({"task": reviewing, "trace": []})

    assert result["task"].review["decision"] == "approve"
    assert [call.name for call, _ in gateway.calls] == ["git_diff", "read_files"]
    assert model.review_input["diff"].startswith("diff --git")
    assert model.review_input["changed_source"] == {
        "src/config.py": "def load(): return default"
    }
    assert model.review_input["acceptance_criteria"] == ["uses defaults"]


@pytest.mark.asyncio
async def test_pr_node_commits_pushes_and_creates_draft_through_gateway(
    tmp_path: Path,
) -> None:
    gateway = RecordingGateway()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=FakeModel(),
            artifacts=FileArtifactStore(tmp_path),
            gateway=gateway,
        )
    )
    reviewed = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .transition(TaskStatus.CODING)
        .transition(TaskStatus.VERIFYING)
        .transition(TaskStatus.REVIEWING)
        .model_copy(
            update={
                "plan_hash": "b" * 64,
                "changed_files": ("src/config.py",),
                "repo_profile": {"workspace_branch": "repoaegis/task-key"},
                "approval": ApprovalDecision(
                    approved=True,
                    approver="reviewer@example.invalid",
                    plan_hash="b" * 64,
                    target_commit="a" * 40,
                    allowed_tools=(ToolPermission.GIT_WRITE, ToolPermission.GITHUB_WRITE),
                    reason="Approved delivery scope.",
                ),
                "allowed_tools": (ToolPermission.GIT_WRITE, ToolPermission.GITHUB_WRITE),
            }
        )
    )

    result = await nodes.pr({"task": reviewed, "trace": []})

    assert [call.name for call, _ in gateway.calls] == [
        "git_commit",
        "git_push",
        "create_draft_pr",
    ]
    assert result["task"].status is TaskStatus.DELIVERING
    assert result["task"].pr_draft["draft"] is True
    assert result["task"].pr_draft["commit_sha"] == "c" * 40
