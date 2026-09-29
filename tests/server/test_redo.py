"""Redoing a stage must be safe: no second envelope, no half-made edits, no second PR.

A stage is redone when its worker died and the lease was reclaimed. These
tests stage the aftermath of such a death directly -- an envelope already
written, a round already recorded, a tree already dirtied, a pull request
already opened -- and check that running the stage again finishes the work
instead of doing it twice.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest

from repoaegis.agent.rounds import Attempt, TestReport
from repoaegis.server.delivery import DeliveryService
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, ApprovalStatus, Decision, TaskStatus
from repoaegis.server.policy import get_policy
from repoaegis.server.solving import ROUND_EVENT
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, TaskRepo
from tests.server import test_delivery as delivery_t
from tests.server import test_planning as planning_t
from tests.server.test_rounds_loop import (
    EDIT_AGAIN,
    FINISH_AGAIN,
    GREEN,
    RED,
    ScriptedVerifier,
    verified_service,
)
from tests.server.test_solving import (
    EDIT,
    FINISH,
    RecordingWorkspaces,
    ScriptedLLM,
    approved_task,
)


class Untouched:
    """A model that must not be called: the stage is expected to resume, not redo."""

    async def complete(self, messages: Any, *, tools: Any = None) -> Any:
        raise AssertionError("the model was called although the work was already done")


@pytest.fixture
def spaces(tmp_path: Path) -> RecordingWorkspaces:
    return RecordingWorkspaces(tmp_path)


# -- planning -----------------------------------------------------------------


async def test_planning_writes_the_envelope_before_moving_the_task(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    approvals: ApprovalRepo,
    tmp_path: Path,
) -> None:
    """A task found waiting must always have its envelope, whatever died in between."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "app.py").write_text("def handler():\n    return None\n", encoding="utf-8")
    seen: list[tuple[str, int]] = []
    original = machine.advance

    async def watching(task_id: str, to: TaskStatus, **kw: Any) -> Any:
        seen.append((to.value, len(await approvals.list_for_task(task_id))))
        return await original(task_id, to, **kw)

    machine.advance = watching  # type: ignore[method-assign]
    planning = planning_t.service(
        machine,
        repo,
        gate,
        planning_t.FakeWorkspaces(tmp_path, checkout),
        planning_t.github_for(planning_t.issue_response),
        ScriptedLLM(planning_t.submit(planning_t.PLAN)),
    )
    task = await planning_t.make_task(machine)

    await planning.plan(task)

    assert ("awaiting_approval", 1) in seen  # the envelope existed when the task moved


async def test_a_redone_planning_continues_from_the_envelope_it_left(
    machine: TaskMachine,
    repo: TaskRepo,
    gate: ApprovalGate,
    approvals: ApprovalRepo,
    tmp_path: Path,
) -> None:
    task = await planning_t.make_task(machine)
    await gate.request(task.id, ApprovalKind.PLAN, {"plan": {"diagnosis": "d"}}, subject="d")
    planning = planning_t.service(
        machine,
        repo,
        gate,
        planning_t.FakeWorkspaces(tmp_path, tmp_path),
        planning_t.github_for(planning_t.issue_response),
        Untouched(),
    )

    await planning.plan(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_APPROVAL
    assert len(await approvals.list_for_task(task.id)) == 1


async def test_a_policy_approved_plan_still_moves_the_task_to_solving(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, tmp_path: Path
) -> None:
    """Under auto-approval the envelope is decided before the task is waiting; apply catches up."""
    gate = ApprovalGate(approvals, machine, policy=get_policy("auto_approve"), ttl_seconds=3600)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "app.py").write_text("def handler():\n    return None\n", encoding="utf-8")
    planning = planning_t.service(
        machine,
        repo,
        gate,
        planning_t.FakeWorkspaces(tmp_path, checkout),
        planning_t.github_for(planning_t.issue_response),
        ScriptedLLM(planning_t.submit(planning_t.PLAN)),
    )
    task = await planning_t.make_task(machine)

    await planning.plan(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.SOLVING
    (envelope,) = await approvals.list_for_task(task.id)
    assert envelope.status is ApprovalStatus.APPROVED


# -- solving ------------------------------------------------------------------


async def test_a_redone_round_starts_from_the_last_checkpoint(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    """Round 1 finished red; round 2's worker died mid-edit. Round 2 is redone clean."""
    task = await approved_task(machine, approvals, spaces)
    solving = verified_service(
        machine, repo, approvals, gate, spaces, ScriptedLLM(EDIT, FINISH), ScriptedVerifier(RED)
    )
    await solving.solve(task)
    checkout = spaces.work_dir / task.id
    # The dead worker's half-made edits: a mangled file and a stray one.
    (checkout / "sessions.py").write_text("garbage\n", encoding="utf-8")
    (checkout / "stray.py").write_text("x\n", encoding="utf-8")

    again = verified_service(
        machine,
        repo,
        approvals,
        gate,
        spaces,
        ScriptedLLM(EDIT_AGAIN, FINISH_AGAIN),
        ScriptedVerifier(GREEN),
    )
    reloaded = await repo.get(task.id)
    assert reloaded is not None
    await again.solve(reloaded)

    # EDIT_AGAIN could only apply on top of round 1's text, so the restore worked.
    assert (checkout / "sessions.py").read_text(encoding="utf-8") == (
        "def resolve(resp):\n    return resp.fixed_twice\n"
    )
    assert not (checkout / "stray.py").exists()
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_PATCH_APPROVAL


async def test_a_green_round_on_record_goes_to_the_gate_without_the_model(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    """The worker died between recording the green round and opening the gate."""
    task = await approved_task(machine, approvals, spaces)
    (spaces.work_dir / task.id / "sessions.py").write_text(
        "def resolve(resp):\n    return resp.fixed\n", encoding="utf-8"
    )
    await spaces.checkpoint(task.id, "round-1")
    record = Attempt(
        round=1,
        hypothesis="h",
        summary="carried it through",
        changed_files=["sessions.py"],
        report=TestReport(passed=5),
    )
    await machine.emit(task.id, ROUND_EVENT, record.model_dump())
    solving = verified_service(
        machine, repo, approvals, gate, spaces, Untouched(), ScriptedVerifier()
    )

    await solving.solve(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_PATCH_APPROVAL
    (envelope,) = [
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH
    ]
    assert envelope.payload["summary"] == "carried it through"
    assert "resp.fixed" in envelope.payload["patch"]


async def test_a_redone_solving_continues_from_the_patch_envelope_it_left(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: RecordingWorkspaces,
) -> None:
    task = await approved_task(machine, approvals, spaces)
    await gate.request(task.id, ApprovalKind.PATCH, {"patch": "diff"}, subject="s")
    solving = verified_service(
        machine, repo, approvals, gate, spaces, Untouched(), ScriptedVerifier()
    )

    await solving.solve(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_PATCH_APPROVAL
    assert (
        len([a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PATCH])
        == 1
    )


# -- delivery -----------------------------------------------------------------


def api_with_existing_pull(record: list[str]) -> Any:
    """test_delivery's fake GitHub, plus an already-open pull request from our branch."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        record.append(f"{request.method} {path}")
        if path == "/user":
            return httpx.Response(200, json={"login": "tester"})
        if path == "/repos/tester/r/merge-upstream":
            return httpx.Response(200, json={"merge_type": "none"})
        if path == "/repos/tester/r/pulls" and request.method == "GET":
            return httpx.Response(
                200, json=[{"number": 4, "html_url": "https://github.com/tester/r/pull/4"}]
            )
        if path == "/repos/tester/r/pulls":
            return httpx.Response(201, json={"number": 7, "html_url": "https://github.com/x/7"})
        return httpx.Response(200, json={"default_branch": "main"})

    return delivery_t.FakeGitHub(handler)


async def test_a_redone_delivery_reuses_the_pull_request_it_opened(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    spaces_delivery: delivery_t.LocalWorkspaces,
) -> None:
    gate = ApprovalGate(approvals, machine, policy=get_policy("always_ask"), ttl_seconds=3600)
    task = await delivery_t.ready_task(machine, approvals, spaces_delivery)
    calls: list[str] = []
    delivery: DeliveryService = delivery_t.service(
        machine, repo, approvals, gate, spaces_delivery, api_with_existing_pull(calls)
    )

    await delivery.deliver(task)
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH
    )
    await gate.decide(envelope.id, Decision.APPROVE, actor="u")
    await delivery.deliver(task)

    assert "POST /repos/tester/r/pulls" not in calls
    events = {e.type: e.payload for e in await repo.list_events(task_id=task.id)}
    assert events["pull_request.opened"]["number"] == 4
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.DONE


@pytest.fixture
def spaces_delivery(tmp_path: Path) -> delivery_t.LocalWorkspaces:
    return delivery_t.LocalWorkspaces(tmp_path)
