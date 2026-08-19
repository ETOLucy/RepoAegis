import json
from pathlib import Path

import pytest

from repo_maintenance_agent.agents.nodes import AgentRuntime, build_agent_nodes
from repo_maintenance_agent.agents.schemas import (
    ContextRequest,
    PatchEdit,
    PatchProposal,
    PlanOutput,
    PullRequestDraft,
    ReviewOutput,
    TaskSpecOutput,
)
from repo_maintenance_agent.domain.errors import ToolExecutionError
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
    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
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
        if schema is ContextRequest:
            return ContextRequest(ready_to_patch=True, reason="Evidence is sufficient.")
        if schema is PatchProposal:
            return PatchProposal(
                summary="Use the safe default.",
                edits=[
                    PatchEdit(
                        path="src/config.py",
                        old_text="def load(): return default",
                        new_text="def load(): return safe_default",
                    )
                ],
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
    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
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

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        if schema is ReviewOutput:
            self.review_input = json.loads(input_text)
            return ReviewOutput(decision="approve", findings=[], summary="Scope and tests pass.")
        return await super().structured(system=system, input_text=input_text, schema=schema)


class ContextSeekingModel(FakeModel):
    def __init__(self) -> None:
        self.context_requests = 0
        self.patch_input: dict[str, object] = {}

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        if schema is ContextRequest:
            self.context_requests += 1
            if self.context_requests == 1:
                return ContextRequest(
                    ready_to_patch=False,
                    search_queries=["load_config default"],
                    files=["src/config.py"],
                    reason="Need implementation and call sites.",
                )
            return ContextRequest(ready_to_patch=True, reason="Context is sufficient.")
        if schema is PatchProposal:
            self.patch_input = json.loads(input_text)
        return await super().structured(system=system, input_text=input_text, schema=schema)


class AlwaysSeekingModel(FakeModel):
    def __init__(self) -> None:
        self.context_requests = 0

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        if schema is ContextRequest:
            self.context_requests += 1
            return ContextRequest(
                ready_to_patch=False,
                search_queries=["more context"],
                files=["src/config.py"],
                reason="Keep searching.",
            )
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
    artifacts = FileArtifactStore(tmp_path)
    nodes = build_agent_nodes(
        AgentRuntime(
            model=FakeModel(),
            artifacts=artifacts,
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
                "declared_files": ("src/config.py",),
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
    assert [call.name for call, _ in gateway.calls] == ["read_files", "apply_patch"]
    call = gateway.calls[1][0]
    assert call.name == "apply_patch"
    assert call.arguments["files"] == ["src/config.py"]
    assert call.arguments["artifact_id"] == result["task"].patch_artifact_id
    assert call.idempotency_key is not None
    patch = await artifacts.get("tenant-a", result["task"].patch_artifact_id)
    assert patch.endswith(
        b"-def load(): return default\n"
        b"\\ No newline at end of file\n"
        b"+def load(): return safe_default\n"
        b"\\ No newline at end of file\n"
    )


@pytest.mark.asyncio
async def test_coding_collects_bounded_context_through_gateway_before_patch(
    tmp_path: Path,
) -> None:
    model = ContextSeekingModel()
    gateway = RecordingGateway()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=model,
            artifacts=FileArtifactStore(tmp_path),
            gateway=gateway,
            max_context_rounds=2,
            max_context_tool_calls=4,
        )
    )
    coding = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .model_copy(update={"declared_files": ("src/config.py",)})
    )

    result = await nodes.coding({"task": coding, "trace": []})

    assert [call.name for call, _ in gateway.calls] == [
        "search_code",
        "read_files",
        "read_files",
        "apply_patch",
    ]
    assert model.context_requests == 2
    context = model.patch_input["controlled_context"]
    assert context["searches"][0]["hits"][0]["path"] == "src/config.py"
    assert context["files"]["src/config.py"] == "def load(): return default"
    assert result["task"].iteration == 1


@pytest.mark.asyncio
async def test_coding_stops_context_collection_at_tool_budget(tmp_path: Path) -> None:
    model = AlwaysSeekingModel()
    gateway = RecordingGateway()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=model,
            artifacts=FileArtifactStore(tmp_path),
            gateway=gateway,
            max_context_rounds=5,
            max_context_tool_calls=1,
        )
    )
    coding = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .model_copy(update={"declared_files": ("src/config.py",)})
    )

    await nodes.coding({"task": coding, "trace": []})

    assert model.context_requests == 1
    assert [call.name for call, _ in gateway.calls] == [
        "search_code",
        "read_files",
        "apply_patch",
    ]


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


class PatchFeedbackModel(FakeModel):
    def __init__(self) -> None:
        self.patch_inputs: list[dict[str, object]] = []

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        if schema is PatchProposal:
            self.patch_inputs.append(json.loads(input_text))
            return PatchProposal(
                summary="Use the safe default.",
                edits=[
                    PatchEdit(
                        path="src/config.py",
                        old_text="def load(): return default",
                        new_text="def load(): return safe_default",
                    )
                ],
            )
        return await super().structured(system=system, input_text=input_text, schema=schema)


class WrongPathPatchFeedbackModel(PatchFeedbackModel):
    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        output = await super().structured(
            system=system,
            input_text=input_text,
            schema=schema,
        )
        if schema is PatchProposal and len(self.patch_inputs) == 1:
            return output.model_copy(
                update={
                    "edits": [
                        PatchEdit(
                            path="src/config.py",
                            old_text="missing implementation",
                            new_text="def load(): return safe_default",
                        )
                    ]
                }
            )
        return output


class UndeclaredPathModel(FakeModel):
    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        if schema is PatchProposal:
            return PatchProposal(
                summary="Modify an unapproved path.",
                edits=[
                    PatchEdit(
                        path="src/secret.py",
                        old_text="secret = 1",
                        new_text="secret = 2",
                    )
                ],
            )
        return await super().structured(system=system, input_text=input_text, schema=schema)


class CountingApplyGateway(RecordingGateway):
    def __init__(self) -> None:
        super().__init__()
        self.apply_calls = 0

    async def execute(self, call, state):
        if call.name == "apply_patch":
            self.apply_calls += 1
        return await super().execute(call, state)


@pytest.mark.asyncio
async def test_coding_rejects_unapproved_path_before_reading_it(tmp_path: Path) -> None:
    gateway = RecordingGateway()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=UndeclaredPathModel(),
            artifacts=FileArtifactStore(tmp_path),
            gateway=gateway,
            max_patch_attempts=1,
        )
    )
    coding = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .model_copy(update={"declared_files": ("src/config.py",)})
    )

    with pytest.raises(ToolExecutionError, match="approved plan"):
        await nodes.coding({"task": coding, "trace": []})

    assert gateway.calls == []


@pytest.mark.asyncio
async def test_coding_retries_patch_with_feedback(tmp_path: Path) -> None:
    model = WrongPathPatchFeedbackModel()
    gateway = CountingApplyGateway()
    nodes = build_agent_nodes(
        AgentRuntime(model=model, artifacts=FileArtifactStore(tmp_path), gateway=gateway)
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
                "declared_files": ("src/config.py",),
                "approval": ApprovalDecision(
                    approved=True,
                    approver="tenant-a",
                    plan_hash="b" * 64,
                    target_commit="a" * 40,
                    allowed_tools=(),
                    reason="Approved.",
                ),
            }
        )
        .transition(TaskStatus.CODING)
    )

    result = await nodes.coding({"task": approved, "trace": []})

    assert result["task"].status is TaskStatus.CODING
    assert gateway.apply_calls == 1
    assert len(model.patch_inputs) == 2
    assert [call.name for call, _ in gateway.calls] == [
        "read_files",
        "read_files",
        "apply_patch",
    ]
    assert model.patch_inputs[1]["controlled_context"]["files"] == {
        "src/config.py": "def load(): return default"
    }
    assert "previous_diff" in model.patch_inputs[1]
    assert "def load(): return default" in model.patch_inputs[1]["previous_diff"]
    assert "patch_feedback" in model.patch_inputs[1]
    assert "old_text was not found" in str(model.patch_inputs[1]["patch_feedback"])
    assert "nearest source excerpt" in str(model.patch_inputs[1]["patch_feedback"])
    assert "def load(): return default" in str(model.patch_inputs[1]["patch_feedback"])
    assert len(str(model.patch_inputs[1]["patch_feedback"])) < 1_000
    proposal_artifacts = sorted(tmp_path.rglob("*-proposed-edits.json"))
    assert len(proposal_artifacts) == 2
    saved_proposals = [
        json.loads(path.read_text(encoding="utf-8")) for path in proposal_artifacts
    ]
    assert {value["edits"][0]["old_text"] for value in saved_proposals} == {
        "missing implementation",
        "def load(): return default",
    }


@pytest.mark.asyncio
async def test_coding_receives_review_feedback_on_next_iteration(tmp_path: Path) -> None:
    model = PatchFeedbackModel()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=model,
            artifacts=FileArtifactStore(tmp_path),
            gateway=RecordingGateway(),
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
                "plan": ({"description": "Fix behavior", "paths": ["src/config.py"]},),
                "declared_files": ("src/config.py",),
                "iteration": 1,
                "review": {
                    "decision": "request_changes",
                    "findings": ["Preserve inherited behavior."],
                    "summary": "The first patch is too broad.",
                },
            }
        )
    )

    await nodes.coding({"task": reviewed, "trace": []})

    assert model.patch_inputs[0]["review_feedback"] == reviewed.review
    assert model.patch_inputs[0]["controlled_context"] == {
        "current_diff": "diff --git a/src/config.py b/src/config.py\n+return default",
        "files": {"src/config.py": "def load(): return default"},
        "searches": [],
    }



class LowRiskUndeclaredModel(FakeModel):
    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        if schema is PatchProposal:
            return PatchProposal(
                summary="Add a regression test.",
                edits=[
                    PatchEdit(
                        path="tests/test_demo.py", 
                        old_text=None, 
                        new_text="def test_x(): pass\n"
                    )
                ],
            )
        return await super().structured(system=system, input_text=input_text, schema=schema)


@pytest.mark.asyncio
async def test_coding_admits_low_risk_undeclared_path(tmp_path: Path) -> None:
    gateway = RecordingGateway()
    nodes = build_agent_nodes(
        AgentRuntime(
            model=LowRiskUndeclaredModel(),
            artifacts=FileArtifactStore(tmp_path),
            gateway=gateway,
            max_patch_attempts=1,
        )
    )
    coding = (
        task()
        .transition(TaskStatus.INTAKE)
        .transition(TaskStatus.RESEARCH)
        .transition(TaskStatus.PLANNING)
        .model_copy(update={"declared_files": ("src/config.py",)})
    )
    result = await nodes.coding({"task": coding, "trace": []})
    assert result["task"].declared_files == ("src/config.py", "tests/test_demo.py")
    assert [call.name for call, _ in gateway.calls][-1] == "apply_patch"
