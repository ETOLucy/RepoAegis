"""The seam between the agent and the server: does a run become events, a
gate, and a settled task -- including when it goes wrong?"""

from pathlib import Path
from typing import Any

import httpx
import pytest

from repoaegis.agent.github import GitHub
from repoaegis.agent.llm import Budget, Completion, ToolCall
from repoaegis.agent.workspace import RepoRef, Workspaces
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalStatus, Decision, TaskCreate, TaskStatus
from repoaegis.server.planning import PlanningService
from repoaegis.server.policy import get_policy
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, TaskRepo

SHA = "a" * 40

PLAN = {
    "diagnosis": "the redirect drops the fragment",
    "locations": [{"file": "app.py", "line_start": 1, "line_end": 2, "why": "here"}],
    "approach": "carry it through",
    "verification": "pytest",
    "confidence": "high",
}


class FakeWorkspaces(Workspaces):
    """Real bookkeeping, no network: the checkout is a directory we made."""

    def __init__(self, tmp_path: Path, checkout: Path) -> None:
        super().__init__(tmp_path / "cache", tmp_path / "work")
        self._checkout = checkout
        self.released: list[str] = []

    async def resolve_head(self, ref: RepoRef) -> str:
        return SHA

    async def prepare(self, ref: RepoRef, sha: str, *, task_id: str) -> Path:
        return self._checkout

    async def release(self, task_id: str) -> None:
        self.released.append(task_id)


class ScriptedLLM:
    def __init__(self, *completions: Completion) -> None:
        self.script = list(completions)

    async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
        return self.script.pop(0)


def submit(plan: dict[str, Any]) -> Completion:
    return Completion(tool_calls=(ToolCall(id="s", name="submit_plan", arguments=plan),))


def github_for(handler: Any) -> GitHub:
    return GitHub(transport=httpx.MockTransport(handler))


def issue_response(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "number": 7,
            "title": "redirect loses the fragment",
            "body": "steps to reproduce",
            "state": "open",
            "html_url": "https://github.com/o/r/issues/7",
        },
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "app.py").write_text("def handler():\n    return None\n", encoding="utf-8")
    return root


def service(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    spaces: Workspaces,
    github: GitHub,
    llm: Any,
) -> PlanningService:
    return PlanningService(machine, repo, gate, spaces, github, lambda budget: llm, max_steps=5)


async def make_task(machine: TaskMachine) -> Any:
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/7"))
    return await machine.advance(task.id, TaskStatus.PLANNING)


async def test_a_successful_run_opens_the_gate_with_the_real_plan(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    approvals: ApprovalRepo,
    tmp_path: Path,
    checkout: Path,
) -> None:
    spaces = FakeWorkspaces(tmp_path, checkout)
    planning = service(
        machine, repo, gate, spaces, github_for(issue_response), ScriptedLLM(submit(PLAN))
    )
    task = await make_task(machine)

    await planning.plan(task)

    current = await repo.get(task.id)
    assert current is not None
    assert current.status is TaskStatus.AWAITING_APPROVAL
    assert current.repo_sha == SHA and current.steps == 1

    envelopes = await approvals.list_for_task(task.id)
    assert [a.status for a in envelopes] == [ApprovalStatus.PENDING]
    assert envelopes[0].payload["plan"]["diagnosis"] == PLAN["diagnosis"]
    assert envelopes[0].subject.startswith("the redirect drops")

    kinds = [e.type for e in await repo.list_events(task_id=task.id)]
    assert "issue.fetched" in kinds and "workspace.ready" in kinds
    assert "agent.plan_ready" in kinds and "plan.finished" in kinds
    assert spaces.released == []  # an approved-path task keeps its checkout


async def test_the_loops_steps_become_events(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    tmp_path: Path,
    checkout: Path,
) -> None:
    llm = ScriptedLLM(
        Completion(tool_calls=(ToolCall(id="c", name="grep", arguments={"pattern": "handler"}),)),
        submit(PLAN),
    )
    planning = service(
        machine, repo, gate, FakeWorkspaces(tmp_path, checkout), github_for(issue_response), llm
    )
    task = await make_task(machine)

    await planning.plan(task)

    events = {e.type: e.payload for e in await repo.list_events(task_id=task.id)}
    assert events["agent.tool_call"]["tool"] == "grep"
    assert events["plan.finished"]["steps"] == 2
    assert events["plan.finished"]["forced"] is False


async def test_an_unreadable_issue_fails_the_task_and_frees_the_checkout(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    tmp_path: Path,
    checkout: Path,
) -> None:
    def missing(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    spaces = FakeWorkspaces(tmp_path, checkout)
    planning = service(machine, repo, gate, spaces, github_for(missing), ScriptedLLM())
    task = await make_task(machine)

    await planning.plan(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.FAILED
    failure = [
        e for e in await repo.list_events(task_id=task.id) if e.type == "task.status_changed"
    ]
    assert failure[-1].payload["reason"] == "GitHubError"
    assert "no such issue" in failure[-1].payload["detail"]
    assert spaces.released == [task.id]


async def test_a_plan_the_model_never_produced_fails_the_task(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    tmp_path: Path,
    checkout: Path,
) -> None:
    wandering = [
        Completion(tool_calls=(ToolCall(id=f"c{i}", name="list_files", arguments={}),))
        for i in range(5)
    ]
    spaces = FakeWorkspaces(tmp_path, checkout)
    planning = service(
        machine, repo, gate, spaces, github_for(issue_response), ScriptedLLM(*wandering)
    )
    task = await make_task(machine)

    await planning.plan(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.FAILED
    assert current.steps == 5  # the attempt is still accounted for
    assert spaces.released == [task.id]


async def test_rejecting_a_plan_frees_the_checkout(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    tmp_path: Path,
    checkout: Path,
) -> None:
    spaces = FakeWorkspaces(tmp_path, checkout)
    gate = ApprovalGate(
        approvals,
        machine,
        policy=get_policy("default"),
        ttl_seconds=3600,
        on_task_rejected=spaces.release,
    )
    planning = service(
        machine, repo, gate, spaces, github_for(issue_response), ScriptedLLM(submit(PLAN))
    )
    task = await make_task(machine)
    await planning.plan(task)

    envelope = (await approvals.list_for_task(task.id))[0]
    await gate.decide(envelope.id, Decision.REJECT, actor="user:lucy")

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.REJECTED
    assert spaces.released == [task.id]


async def test_each_task_gets_its_own_budget(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    tmp_path: Path,
    checkout: Path,
) -> None:
    """One expensive run must not starve the next: the meter is per task."""
    seen: list[Budget] = []

    def factory(budget: Budget) -> Any:
        seen.append(budget)
        return ScriptedLLM(submit(PLAN))

    planning = PlanningService(
        machine,
        repo,
        gate,
        FakeWorkspaces(tmp_path, checkout),
        github_for(issue_response),
        factory,
        max_steps=5,
        budget_usd=0.25,
    )
    await planning.plan(await make_task(machine))
    await planning.plan(await make_task(machine))

    assert [b.limit for b in seen] == [0.25, 0.25]
    assert seen[0] is not seen[1]


async def test_an_unexpected_error_fails_the_task_instead_of_wedging_it(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    tmp_path: Path,
    checkout: Path,
) -> None:
    """The bug this guards: a provider 400 escaped the handler, the worker
    logged it, and the task sat in PLANNING forever -- never failed, never
    retried, never releasing its checkout."""

    class Exploding:
        async def complete(self, messages: Any, *, tools: Any = None) -> Any:
            raise RuntimeError("Error code: 400 - invalid assistant message")

    spaces = FakeWorkspaces(tmp_path, checkout)
    planning = service(machine, repo, gate, spaces, github_for(issue_response), Exploding())
    task = await make_task(machine)

    await planning.plan(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.FAILED
    failure = [
        e for e in await repo.list_events(task_id=task.id) if e.type == "task.status_changed"
    ][-1]
    assert failure.payload["reason"] == "RuntimeError"
    assert "400" in failure.payload["detail"]
    assert spaces.released == [task.id]
