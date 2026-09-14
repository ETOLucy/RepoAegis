"""From an approved plan to a reviewed diff, with a real git checkout under it."""

from pathlib import Path
from typing import Any

import pytest

from repoaegis.agent.llm import Completion, ToolCall, Usage
from repoaegis.agent.workspace import RepoRef, Workspaces, git, remove_tree
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import (
    ApprovalKind,
    ApprovalStatus,
    Decision,
    Task,
    TaskCreate,
    TaskStatus,
)
from repoaegis.server.solving import SolvingService
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, TaskRepo

SHA = "b" * 40
SOURCE = "def resolve(resp):\n    return resp\n"

PLAN_PAYLOAD = {
    "plan": {
        "diagnosis": "the fragment is dropped",
        "locations": [{"file": "sessions.py", "line_start": 1, "line_end": 2, "why": "here"}],
        "approach": "carry it through",
        "verification": "pytest",
        "confidence": "high",
    },
    "issue": {"title": "redirect loses the fragment", "body": "report body"},
}


class RecordingWorkspaces(Workspaces):
    """Real diffs and real worktree paths; only the network is stubbed out."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path / "cache", tmp_path / "work")
        self.released: list[str] = []
        self.prepared: list[str] = []

    async def resolve_head(self, ref: RepoRef) -> str:
        return SHA

    async def prepare(self, ref: RepoRef, sha: str, *, task_id: str) -> Path:
        self.prepared.append(task_id)
        return await seed_checkout(self.work_dir / task_id)

    async def release(self, task_id: str) -> None:
        self.released.append(task_id)


async def seed_checkout(path: Path) -> Path:
    """A git repository with one committed file, so a diff has a baseline."""
    path.mkdir(parents=True, exist_ok=True)
    await git("init", "--quiet", str(path), timeout=30)
    (path / "sessions.py").write_text(SOURCE, encoding="utf-8")
    await git("add", "-A", cwd=path, timeout=30)
    await git(
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "--quiet",
        "-m",
        "base",
        cwd=path,
        timeout=30,
    )
    return path


class ScriptedLLM:
    def __init__(self, *completions: Completion) -> None:
        self.script = list(completions)

    async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
        return self.script.pop(0)


def call(name: str, arguments: dict[str, Any], id: str = "c") -> Completion:
    return Completion(
        tool_calls=(ToolCall(id=id, name=name, arguments=arguments),),
        usage=Usage(cache_miss_tokens=100, completion_tokens=20, cost_usd=0.002),
    )


EDIT = call("replace", {"path": "sessions.py", "old": "return resp", "new": "return resp.fixed"})
FINISH = call("finish", {"summary": "carried the fragment through"}, id="fin")


@pytest.fixture
def spaces(tmp_path: Path) -> RecordingWorkspaces:
    return RecordingWorkspaces(tmp_path)


async def approved_task(
    machine: TaskMachine, approvals: ApprovalRepo, spaces: RecordingWorkspaces
) -> Task:
    """A task that has passed the plan gate and has a checkout waiting."""
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/7"))
    await machine.advance(task.id, TaskStatus.PLANNING)
    await machine.advance(task.id, TaskStatus.AWAITING_APPROVAL)
    await approvals.create(
        task_id=task.id,
        kind=ApprovalKind.PLAN,
        subject="the fragment is dropped",
        payload=PLAN_PAYLOAD,
        payload_hash="0" * 64,
        status=ApprovalStatus.APPROVED,
        policy="default",
        reason="approved in the test",
        decided_by="user:test",
        ttl_seconds=3600,
    )
    await machine.advance(task.id, TaskStatus.SOLVING)
    await seed_checkout(spaces.work_dir / task.id)
    return task


def service(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: Workspaces,
    llm: Any,
) -> SolvingService:
    return SolvingService(machine, repo, approvals, gate, spaces, lambda budget: llm, max_steps=5)


async def test_a_solved_task_opens_the_patch_gate_with_a_real_diff(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    task = await approved_task(machine, approvals, spaces)
    solving = service(machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH))

    await solving.solve(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_PATCH_APPROVAL

    patch_gate = [a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH]
    assert len(patch_gate) == 1 and patch_gate[0].status is ApprovalStatus.PENDING
    patch = patch_gate[0].payload["patch"]
    assert "--- a/sessions.py" in patch and "+++ b/sessions.py" in patch
    assert "-    return resp" in patch and "+    return resp.fixed" in patch
    assert patch_gate[0].payload["changed"] == ["sessions.py"]
    assert patch_gate[0].subject == "carried the fragment through"


async def test_the_solver_is_briefed_from_the_approved_envelope(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    """What the reviewer agreed to is exactly what the solver is handed."""

    seen: list[Any] = []

    class Capturing(ScriptedLLM):
        async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
            seen.extend(messages)
            return await super().complete(messages, tools=tools)

    task = await approved_task(machine, approvals, spaces)
    await service(machine, repo, approvals, gate, spaces, Capturing(FINISH)).solve(task)

    briefing = str(seen[1]["content"])
    assert "redirect loses the fragment" in briefing  # issue title from the envelope
    assert "the fragment is dropped" in briefing  # the approved diagnosis
    assert "sessions.py:1-2" in briefing


async def test_a_task_without_an_approved_plan_fails(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/7"))
    await machine.advance(task.id, TaskStatus.PLANNING)
    await machine.advance(task.id, TaskStatus.AWAITING_APPROVAL)
    await machine.advance(task.id, TaskStatus.SOLVING)

    await service(machine, repo, approvals, gate, spaces, ScriptedLLM()).solve(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.FAILED
    assert spaces.released == [task.id]


async def test_a_run_that_changes_nothing_fails_instead_of_asking_for_review(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    task = await approved_task(machine, approvals, spaces)
    await service(machine, repo, approvals, gate, spaces, ScriptedLLM(FINISH)).solve(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.FAILED
    failure = [
        e for e in await repo.list_events(task_id=task.id) if e.type == "task.status_changed"
    ]
    assert failure[-1].payload["reason"] in {"empty_patch", "model"}
    assert not [a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH]


async def test_a_missing_checkout_is_rebuilt_from_the_pinned_commit(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    """A restart or a cleaned disk should cost a clone, not the run."""
    task = await approved_task(machine, approvals, spaces)
    await repo.record_run(task.id, sha=SHA, steps=3, cost_usd=0.001)
    remove_tree(spaces.work_dir / task.id)
    assert not (spaces.work_dir / task.id).exists()

    reloaded = await repo.get(task.id)
    assert reloaded is not None
    await service(machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH)).solve(reloaded)

    assert spaces.prepared == [task.id]
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_PATCH_APPROVAL


async def test_cost_accumulates_across_both_stages(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    task = await approved_task(machine, approvals, spaces)
    await repo.record_run(task.id, sha=SHA, steps=5, cost_usd=0.0007)  # what planning spent
    reloaded = await repo.get(task.id)
    assert reloaded is not None

    await service(machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH)).solve(reloaded)

    current = await repo.get(task.id)
    assert current is not None
    assert current.steps == 7  # 5 planning + 2 solving
    assert current.cost_usd == pytest.approx(0.0007 + 0.004)


async def test_approving_the_patch_moves_the_task_to_delivering(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    spaces: RecordingWorkspaces,
) -> None:
    from repoaegis.server.policy import get_policy

    gate = ApprovalGate(
        approvals,
        machine,
        policy=get_policy("default"),
        ttl_seconds=3600,
        on_task_rejected=spaces.release,
    )
    task = await approved_task(machine, approvals, spaces)
    await service(machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH)).solve(task)

    patch_gate = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH
    )
    await gate.decide(patch_gate.id, Decision.APPROVE, actor="user:lucy")

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.DELIVERING
    assert spaces.released == []  # delivering still needs the checkout


async def test_rejecting_the_patch_ends_the_task_and_frees_the_checkout(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    spaces: RecordingWorkspaces,
) -> None:
    from repoaegis.server.policy import get_policy

    gate = ApprovalGate(
        approvals,
        machine,
        policy=get_policy("default"),
        ttl_seconds=3600,
        on_task_rejected=spaces.release,
    )
    task = await approved_task(machine, approvals, spaces)
    await service(machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH)).solve(task)

    patch_gate = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH
    )
    await gate.decide(patch_gate.id, Decision.REJECT, actor="user:lucy")

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.REJECTED
    assert spaces.released == [task.id]
