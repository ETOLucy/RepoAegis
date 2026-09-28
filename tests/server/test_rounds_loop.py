"""The solve/verify loop, driven by the state machine one round per call.

Builds on the fixtures in ``test_solving``: a real git checkout, a scripted
model, and the plan envelope a reviewer already approved.
"""

from pathlib import Path
from typing import Any

import pytest

from repoaegis.agent.llm import Completion
from repoaegis.agent.plan import Plan
from repoaegis.agent.rounds import Failure, TestReport
from repoaegis.agent.tools import Workspace
from repoaegis.agent.workspace import Workspaces
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, TaskStatus
from repoaegis.server.solving import SolvingService
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, TaskRepo
from tests.server.test_solving import (
    EDIT,
    FINISH,
    RecordingWorkspaces,
    ScriptedLLM,
    approved_task,
    call,
    service,
)


@pytest.fixture
def spaces(tmp_path: Path) -> RecordingWorkspaces:
    return RecordingWorkspaces(tmp_path)


class ScriptedVerifier:
    """Reports in order; records what it was handed."""

    def __init__(self, *reports: TestReport) -> None:
        self.script = list(reports)
        self.seen: list[Path] = []

    async def run(self, ws: Workspace, plan: Plan) -> TestReport:
        self.seen.append(ws.root)
        return self.script.pop(0)


RED = TestReport(
    passed=4,
    failed=1,
    failures=[Failure(test="tests/test_redirects.py::test_fragment", kind="AssertionError")],
    log_path=".repoaegis/runs/1/pytest.log",
)
GREEN = TestReport(passed=5)

EDIT_AGAIN = call(
    "replace", {"path": "sessions.py", "old": "resp.fixed", "new": "resp.fixed_twice"}, id="c2"
)
FINISH_AGAIN = call(
    "finish", {"summary": "second try", "hypothesis": "the first fix missed the query"}, id="fin2"
)


def verified_service(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: Workspaces,
    llm: Any,
    verifier: Any,
    *,
    max_rounds: int = 3,
) -> SolvingService:
    return SolvingService(
        machine,
        repo,
        approvals,
        gate,
        spaces,
        lambda budget: llm,
        verifier=verifier,
        max_steps=5,
        max_rounds=max_rounds,
    )


async def test_a_red_report_sends_the_task_back_to_solving_with_the_round_on_record(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    task = await approved_task(machine, approvals, spaces)
    verifier = ScriptedVerifier(RED)
    solving = verified_service(
        machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH), verifier
    )

    await solving.solve(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.SOLVING
    assert verifier.seen == [spaces.work_dir / task.id]
    assert not [a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH]

    statuses = [
        (e.payload["from"], e.payload["to"])
        for e in await repo.list_events(task_id=task.id)
        if e.type == "task.status_changed"
    ]
    assert statuses[-2:] == [("solving", "verifying"), ("verifying", "solving")]

    rounds = await solving.attempts(task.id)
    assert len(rounds) == 1
    assert rounds[0].round == 1
    assert rounds[0].hypothesis == "the fragment was dropped"
    assert rounds[0].changed_files == ["sessions.py"]
    assert rounds[0].report == RED
    assert spaces.released == []  # the tree carries the edits into the next round


async def test_the_next_round_is_briefed_with_the_record_and_opens_the_gate_when_green(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    seen: list[list[Any]] = []

    class Capturing(ScriptedLLM):
        async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
            seen.append(list(messages))
            return await super().complete(messages, tools=tools)

    task = await approved_task(machine, approvals, spaces)
    verifier = ScriptedVerifier(RED, GREEN)
    llm = Capturing(EDIT, FINISH, EDIT_AGAIN, FINISH_AGAIN)
    solving = verified_service(machine, repo, approvals, gate, spaces, llm, verifier)

    await solving.solve(task)  # round 1: red
    reloaded = await repo.get(task.id)
    assert reloaded is not None and reloaded.status is TaskStatus.SOLVING
    await solving.solve(reloaded)  # round 2: green

    # Round 2's first call saw the record of round 1, and nothing of its conversation.
    first_of_round_two = seen[2]
    assert len(first_of_round_two) == 2
    briefing = str(first_of_round_two[1]["content"])
    assert "This is round 2" in briefing
    assert "hypothesis: the fragment was dropped" in briefing
    assert "FAIL tests/test_redirects.py::test_fragment" in briefing
    assert "+    return resp.fixed" in briefing  # the diff the tree already carries

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_PATCH_APPROVAL
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH
    )
    assert envelope.payload["rounds"] == 2
    assert envelope.payload["hypothesis"] == "the first fix missed the query"
    assert envelope.payload["report"]["failed"] == 0
    assert [a["hypothesis"] for a in envelope.payload["attempts"]] == [
        "the fragment was dropped",
        "the first fix missed the query",
    ]
    assert "resp.fixed_twice" in envelope.payload["patch"]
    assert envelope.payload["changed"] == ["sessions.py"]
    assert current.steps == 4  # two rounds of two steps each


async def test_running_out_of_rounds_opens_the_gate_with_the_red_report(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    """Out of rounds is not a failure: the reviewer sees what did not work."""
    task = await approved_task(machine, approvals, spaces)
    solving = verified_service(
        machine,
        repo,
        approvals,
        gate,
        spaces,
        ScriptedLLM(EDIT, FINISH),
        ScriptedVerifier(RED),
        max_rounds=1,
    )

    await solving.solve(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_PATCH_APPROVAL
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH
    )
    assert envelope.payload["rounds"] == 1
    assert envelope.payload["report"]["failed"] == 1
    moved = [
        e.payload
        for e in await repo.list_events(task_id=task.id)
        if e.type == "task.status_changed" and e.payload["to"] == "awaiting_patch_approval"
    ]
    assert moved[-1]["tests_ok"] is False


async def test_a_crashing_verifier_fails_the_task_instead_of_wedging_it(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    class Broken:
        async def run(self, ws: Workspace, plan: Plan) -> TestReport:
            raise RuntimeError("docker is not running")

    task = await approved_task(machine, approvals, spaces)
    solving = verified_service(
        machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH), Broken()
    )

    await solving.solve(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.FAILED
    last = [e for e in await repo.list_events(task_id=task.id) if e.type == "task.status_changed"]
    assert last[-1].payload["reason"] == "verifier_error"
    assert "docker is not running" in last[-1].payload["detail"]
    assert spaces.released == [task.id]


async def test_without_a_verifier_the_gate_says_no_tests_were_run(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    task = await approved_task(machine, approvals, spaces)
    await service(machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH)).solve(task)

    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH
    )
    assert envelope.payload["report"] is None
    assert envelope.payload["rounds"] == 1
    assert envelope.payload["hypothesis"] == "the fragment was dropped"
    assert envelope.payload["attempts"][0]["report"] is None
