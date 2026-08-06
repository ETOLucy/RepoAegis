from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from time import monotonic
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from repo_maintenance_agent.agents.nodes import AgentRuntime, build_agent_nodes
from repo_maintenance_agent.domain.models import (
    ApprovalDecision,
    ApprovalEnvelope,
    Evidence,
    IssueSpec,
    RepoTaskState,
    TaskStatus,
    ToolPermission,
)
from repo_maintenance_agent.evaluation.models import ModelUsage
from repo_maintenance_agent.evaluation.swebench import (
    SWEbenchPrediction,
    write_predictions,
)
from repo_maintenance_agent.graph.state import GraphState
from repo_maintenance_agent.models.usage import UsageLedger
from repo_maintenance_agent.policies.permissions import PermissionPolicy
from repo_maintenance_agent.search.adapters.local import LocalLexicalSearch
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.tools.agent_actions import (
    PatchArtifactAdapter,
    SearchAdapter,
    WorkspaceReadAdapter,
)
from repo_maintenance_agent.tools.gateway import InMemoryOperationLog, ToolGateway
from repo_maintenance_agent.tools.git import GitToolAdapter
from repo_maintenance_agent.tools.patch import GitPatchApplier
from repo_maintenance_agent.tools.process import ProcessRunner


class SWEbenchTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    instance_id: str = Field(min_length=1, max_length=256)
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    problem_statement: str = Field(min_length=1, max_length=100_000)


class SWEbenchDevelopmentFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    instance_id: str = Field(min_length=1, max_length=256)
    source_run_id: str = Field(min_length=1, max_length=256)
    prediction_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    official_report_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    failing_tests: tuple[str, ...] = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=10_000)

    def digest(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return "sha256:" + hashlib.sha256(canonical).hexdigest()


class PatchAgent(Protocol):
    async def run(
        self,
        task: SWEbenchTask,
        workspace: Path,
        ledger: UsageLedger,
        development_feedback: SWEbenchDevelopmentFeedback | None = None,
    ) -> None: ...


class RuntimeExecutor(Protocol):
    @property
    def model_name_or_path(self) -> str: ...

    def development_feedback_digest(self, instance_id: str) -> str | None: ...

    async def execute(self, task: SWEbenchTask, ledger: UsageLedger) -> SWEbenchPrediction: ...


class SWEbenchGenerationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["swebench-generation-evidence/v1"] = (
        "swebench-generation-evidence/v1"
    )
    protocol_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    arm: Literal["baseline", "candidate"]
    instance_id: str = Field(min_length=1, max_length=256)
    model_name_or_path: str = Field(min_length=1, max_length=256)
    runtime_completed: Literal[True] = True
    prediction: SWEbenchPrediction
    usage: ModelUsage
    latency_ms: int = Field(ge=0)
    development_feedback_digest: str | None = Field(
        default=None, pattern=r"^sha256:[a-f0-9]{64}$"
    )


class RepoAegisPatchAgent:
    def __init__(
        self,
        *,
        model_factory: Callable[[UsageLedger], Any],
        artifact_root: Path,
        max_iterations: int = 3,
        max_context_rounds: int = 1,
        max_context_tool_calls: int = 8,
        max_patch_attempts: int = 2,
    ) -> None:
        if not 1 <= max_iterations <= 10:
            raise ValueError("maximum iterations must be between 1 and 10")
        self._model_factory = model_factory
        self._artifacts = FileArtifactStore(artifact_root)
        self._max_iterations = max_iterations
        self._max_context_rounds = max_context_rounds
        self._max_context_tool_calls = max_context_tool_calls
        self._max_patch_attempts = max_patch_attempts

    async def run(
        self,
        task: SWEbenchTask,
        workspace: Path,
        ledger: UsageLedger,
        development_feedback: SWEbenchDevelopmentFeedback | None = None,
    ) -> None:
        git_runner = ProcessRunner(allowed_executables={"git"})
        gateway = ToolGateway(
            policy=PermissionPolicy(),
            adapters={
                "search_code": SearchAdapter(LocalLexicalSearch(workspace)),
                "read_files": WorkspaceReadAdapter(),
                "apply_patch": PatchArtifactAdapter(
                    artifacts=self._artifacts,
                    applier=GitPatchApplier(git_runner),
                ),
                "git_diff": GitToolAdapter(git_runner),
            },
            operation_log=InMemoryOperationLog(),
            workspace_root=workspace,
        )
        nodes = build_agent_nodes(
            AgentRuntime(
                model=self._model_factory(ledger),
                artifacts=self._artifacts,
                gateway=gateway,
                max_context_rounds=self._max_context_rounds,
                max_context_tool_calls=self._max_context_tool_calls,
                max_patch_attempts=self._max_patch_attempts,
            )
        )
        issue_title = task.problem_statement.splitlines()[0][:500]
        repo_task = RepoTaskState(
            task_id=f"swebench:{task.instance_id}",
            tenant_id="swebench-evaluation",
            repo_id=task.repo,
            commit_sha=task.base_commit,
            base_branch="swebench",
            issue=IssueSpec(title=issue_title, body=task.problem_statement),
            max_iterations=self._max_iterations,
        )
        state: GraphState = {"task": repo_task, "trace": []}
        state = await _invoke(nodes.intake, state)
        state = await _invoke(nodes.research, state)
        if development_feedback is not None:
            state = _attach_development_feedback(state, development_feedback)
        state = await _invoke(nodes.planning, state)
        state = _approve_generation_scope(state)

        while state["task"].iteration < state["task"].max_iterations:
            state = await _invoke(nodes.coding, state)
            state = _defer_to_official_verifier(state)
            state = await _invoke(nodes.review, state)
            decision = state["task"].review.get("decision")
            if decision == "approve":
                return
            if decision != "request_changes":
                break
        raise RuntimeError("RepoAegis review did not approve the generated patch")


class GitSWEbenchRuntime:
    def __init__(
        self,
        *,
        repository_locators: Mapping[str, str],
        workspace_root: Path,
        model_name_or_path: str,
        patch_agent: PatchAgent,
        runner: ProcessRunner,
        development_feedback: Mapping[str, SWEbenchDevelopmentFeedback] | None = None,
    ) -> None:
        if not model_name_or_path.strip():
            raise ValueError("model name must not be empty")
        self._repository_locators = dict(repository_locators)
        self._workspace_root = workspace_root.resolve()
        self._model_name_or_path = model_name_or_path
        self._patch_agent = patch_agent
        self._runner = runner
        self._development_feedback = dict(development_feedback or {})

    @property
    def model_name_or_path(self) -> str:
        return self._model_name_or_path

    def development_feedback_digest(self, instance_id: str) -> str | None:
        feedback = self._development_feedback.get(instance_id)
        return feedback.digest() if feedback is not None else None

    async def execute(
        self, task: SWEbenchTask, ledger: UsageLedger
    ) -> SWEbenchPrediction:
        workspace = await self._materialize(task)
        await self._patch_agent.run(
            task,
            workspace,
            ledger,
            self._development_feedback.get(task.instance_id),
        )
        result = await self._runner.run(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--binary",
                "--unified=3",
                task.base_commit,
                "--",
            ],
            cwd=workspace,
        )
        if not result.stdout.strip():
            raise RuntimeError("agent generation produced no tracked patch")
        return SWEbenchPrediction(
            instance_id=task.instance_id,
            model_patch=result.stdout,
            model_name_or_path=self._model_name_or_path,
        )

    async def _materialize(self, task: SWEbenchTask) -> Path:
        locator = self._repository_locators.get(task.repo)
        if locator is None:
            raise ValueError(f"repository is not registered: {task.repo}")
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        task_key = hashlib.sha256(task.instance_id.encode()).hexdigest()[:24]
        workspace = (self._workspace_root / task_key).resolve()
        if not workspace.is_relative_to(self._workspace_root):
            raise ValueError("SWE-bench workspace escaped its root")
        if workspace.exists():
            current_commit = await self._current_commit(workspace)
            if current_commit is None:
                shutil.rmtree(workspace, onexc=_retry_readonly_removal)
            else:
                if current_commit != task.base_commit:
                    raise RuntimeError(
                        "SWE-bench workspace is not pinned to the task base commit"
                    )
                await self._runner.run(
                    ["git", "reset", "--hard", task.base_commit], cwd=workspace
                )
                await self._runner.run(["git", "clean", "-fd"], cwd=workspace)
                return workspace

        await self._runner.run(
            [
                "git",
                "clone",
                "--no-checkout",
                "--no-tags",
                "--filter=blob:none",
                "--",
                locator,
                str(workspace),
            ],
            cwd=self._workspace_root,
        )
        await self._runner.run(
            [
                "git",
                "-c",
                "advice.detachedHead=false",
                "checkout",
                "--detach",
                task.base_commit,
            ],
            cwd=workspace,
        )
        await self._assert_commit(workspace, task.base_commit)
        return workspace

    async def _assert_commit(self, workspace: Path, expected: str) -> None:
        if await self._current_commit(workspace) != expected:
            raise RuntimeError("SWE-bench workspace is not pinned to the task base commit")

    async def _current_commit(self, workspace: Path) -> str | None:
        result = await self._runner.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=workspace,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None


async def generate_prediction(
    task: SWEbenchTask,
    runtime: RuntimeExecutor,
    ledger: UsageLedger,
) -> SWEbenchPrediction:
    return await runtime.execute(task, ledger)


async def run_predictions(
    tasks: Sequence[SWEbenchTask],
    *,
    runtime: RuntimeExecutor,
    ledger: UsageLedger,
    evidence_directory: Path,
    output_path: Path,
    protocol_digest: str,
    arm: Literal["baseline", "candidate"],
) -> tuple[SWEbenchPrediction, ...]:
    evidence_directory.mkdir(parents=True, exist_ok=True)
    predictions: list[SWEbenchPrediction] = []
    for task in tasks:
        development_feedback_digest = runtime.development_feedback_digest(
            task.instance_id
        )
        evidence_path = evidence_directory / (
            hashlib.sha256(task.instance_id.encode()).hexdigest() + ".json"
        )
        if evidence_path.exists():
            evidence = SWEbenchGenerationEvidence.model_validate_json(
                evidence_path.read_text(encoding="utf-8")
            )
            _validate_resume(
                evidence,
                task=task,
                protocol_digest=protocol_digest,
                arm=arm,
                model_name_or_path=runtime.model_name_or_path,
                development_feedback_digest=development_feedback_digest,
            )
            predictions.append(evidence.prediction)
            continue

        before = ledger.snapshot()
        started = monotonic()
        prediction = await generate_prediction(task, runtime, ledger)
        evidence = SWEbenchGenerationEvidence(
            protocol_digest=protocol_digest,
            arm=arm,
            instance_id=task.instance_id,
            model_name_or_path=runtime.model_name_or_path,
            prediction=prediction,
            usage=_usage_difference(ledger.snapshot(), before),
            latency_ms=int((monotonic() - started) * 1_000),
            development_feedback_digest=development_feedback_digest,
        )
        _atomic_json(evidence_path, evidence)
        predictions.append(prediction)
    write_predictions(output_path, predictions)
    return tuple(predictions)


async def _invoke(node: Any, state: GraphState) -> GraphState:
    update = await node(state)
    task = update.get("task")
    if not isinstance(task, RepoTaskState):
        raise RuntimeError("RepoAegis node did not return a task state")
    trace = update.get("trace", [])
    return {"task": task, "trace": [*state.get("trace", []), *trace]}


def _approve_generation_scope(state: GraphState) -> GraphState:
    task = state["task"]
    allowed_tools = (ToolPermission.REPO_READ, ToolPermission.SANDBOX_WRITE)
    envelope = ApprovalEnvelope(
        plan=task.plan,
        target_commit=task.commit_sha,
        allowed_tools=allowed_tools,
        declared_files=task.declared_files,
        verification_plan=task.verification_plan,
    )
    plan_hash = envelope.digest()
    narrowed = task.model_copy(
        update={"allowed_tools": allowed_tools, "plan_hash": plan_hash}
    )
    decision = ApprovalDecision(
        approved=True,
        approver="swebench-protocol",
        plan_hash=plan_hash,
        target_commit=task.commit_sha,
        allowed_tools=allowed_tools,
        reason="Frozen protocol authorizes local patch generation only.",
    )
    if narrowed.status not in {TaskStatus.PLANNING, TaskStatus.NEEDS_APPROVAL}:
        raise RuntimeError("RepoAegis planning ended in an invalid generation state")
    return {
        "task": narrowed.model_copy(update={"approval": decision}).transition(
            TaskStatus.CODING
        ),
        "trace": [*state.get("trace", []), "generation_approval"],
    }


def _attach_development_feedback(
    state: GraphState,
    feedback: SWEbenchDevelopmentFeedback,
) -> GraphState:
    task = state["task"]
    if feedback.instance_id != task.task_id.removeprefix("swebench:"):
        raise ValueError("development feedback does not match the SWE-bench task")
    payload = feedback.model_dump(mode="json")
    item = Evidence(
        source="official-swebench-calibration",
        locator=f"{feedback.source_run_id}#{feedback.official_report_digest}",
        summary=json.dumps(payload, sort_keys=True),
        content_hash=feedback.digest().removeprefix("sha256:"),
    )
    updated = task.model_copy(
        update={
            "evidence": (*task.evidence, item),
            "repo_profile": task.repo_profile | {"development_feedback": payload},
        }
    )
    return {"task": updated, "trace": [*state.get("trace", []), "development_feedback"]}


def _defer_to_official_verifier(state: GraphState) -> GraphState:
    task = state["task"]
    if task.status is not TaskStatus.CODING or task.verification is not None:
        raise RuntimeError("generated patch is not ready for official verification")
    return {
        "task": task.transition(TaskStatus.VERIFYING),
        "trace": [*state.get("trace", []), "official_verification_deferred"],
    }


def _usage_difference(current: ModelUsage, previous: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_cache_hit_tokens=(
            current.input_cache_hit_tokens - previous.input_cache_hit_tokens
        ),
        input_cache_miss_tokens=(
            current.input_cache_miss_tokens - previous.input_cache_miss_tokens
        ),
        output_tokens=current.output_tokens - previous.output_tokens,
        reasoning_tokens=current.reasoning_tokens - previous.reasoning_tokens,
        estimated_cost_cny=Decimal(current.estimated_cost_cny)
        - Decimal(previous.estimated_cost_cny),
    )


def _validate_resume(
    evidence: SWEbenchGenerationEvidence,
    *,
    task: SWEbenchTask,
    protocol_digest: str,
    arm: str,
    model_name_or_path: str,
    development_feedback_digest: str | None,
) -> None:
    if (
        evidence.protocol_digest != protocol_digest
        or evidence.arm != arm
        or evidence.instance_id != task.instance_id
        or evidence.prediction.instance_id != task.instance_id
        or evidence.model_name_or_path != model_name_or_path
        or evidence.prediction.model_name_or_path != model_name_or_path
        or evidence.development_feedback_digest != development_feedback_digest
    ):
        raise ValueError("saved SWE-bench evidence does not match this run")


def _atomic_json(path: Path, evidence: SWEbenchGenerationEvidence) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    payload = json.dumps(
        evidence.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    temporary.write_text(payload + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _retry_readonly_removal(
    function: Callable[..., Any], path: str, error: BaseException
) -> None:
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    function(path)
