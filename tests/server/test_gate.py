"""What the approval gate promises: binding, idempotency, one winner, fail closed."""

import asyncio

import pytest

from repoaegis.server.gate import ApprovalClosed, ApprovalGate, PayloadMismatch
from repoaegis.server.models import (
    ApprovalKind,
    ApprovalStatus,
    Decision,
    Task,
    TaskCreate,
    TaskStatus,
)
from repoaegis.server.policy import get_policy
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, TaskRepo

PLAN = {"summary": "patch the redirect handler", "steps": ["edit", "test"]}


async def waiting_task(machine: TaskMachine) -> Task:
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/1"))
    await machine.advance(task.id, TaskStatus.PLANNING)
    return await machine.advance(task.id, TaskStatus.AWAITING_APPROVAL)


def gate_with(
    approvals: ApprovalRepo, machine: TaskMachine, policy: str, ttl: float = 3600.0
) -> ApprovalGate:
    return ApprovalGate(approvals, machine, policy=get_policy(policy), ttl_seconds=ttl)


async def open_gate(machine: TaskMachine, gate: ApprovalGate) -> tuple[Task, str]:
    task = await waiting_task(machine)
    approval = await gate.request(
        task.id, ApprovalKind.PLAN, {"plan": PLAN}, subject=str(PLAN["summary"])
    )
    return task, approval.id


async def test_approving_releases_the_task(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
) -> None:
    task, approval_id = await open_gate(machine, gate)
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.AWAITING_APPROVAL

    decided = await gate.decide(approval_id, Decision.APPROVE, actor="user:lucy")
    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == "user:lucy"

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.SOLVING


async def test_rejecting_ends_the_task(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
) -> None:
    task, approval_id = await open_gate(machine, gate)
    await gate.decide(approval_id, Decision.REJECT, actor="user:lucy")
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.REJECTED


async def test_approval_is_bound_to_the_content_it_showed(
    machine: TaskMachine, gate: ApprovalGate
) -> None:
    _, approval_id = await open_gate(machine, gate)
    with pytest.raises(PayloadMismatch):
        await gate.decide(approval_id, Decision.APPROVE, actor="u", payload_hash="0" * 64)


async def test_a_swapped_payload_cannot_ride_an_old_approval(
    machine: TaskMachine, gate: ApprovalGate
) -> None:
    """The point of the envelope: approve one thing, try to execute another."""
    _, approval_id = await open_gate(machine, gate)
    await gate.decide(approval_id, Decision.APPROVE, actor="u")

    assert await gate.guard(approval_id, {"plan": PLAN})  # the bytes that were shown
    with pytest.raises(PayloadMismatch):
        await gate.guard(approval_id, {"plan": {**PLAN, "steps": ["rm -rf /"]}})


async def test_repeating_the_same_answer_changes_nothing(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
) -> None:
    task, approval_id = await open_gate(machine, gate)
    await gate.decide(approval_id, Decision.APPROVE, actor="u")

    with pytest.raises(ApprovalClosed) as caught:
        await gate.decide(approval_id, Decision.APPROVE, actor="u")
    assert caught.value.idempotent is True

    events = await repo.list_events(task_id=task.id)
    assert [e.type for e in events].count("approval.decided") == 1


async def test_contradicting_a_decision_is_a_conflict(
    machine: TaskMachine, gate: ApprovalGate
) -> None:
    _, approval_id = await open_gate(machine, gate)
    await gate.decide(approval_id, Decision.APPROVE, actor="u")
    with pytest.raises(ApprovalClosed) as caught:
        await gate.decide(approval_id, Decision.REJECT, actor="someone-else")
    assert caught.value.idempotent is False


async def test_ten_concurrent_answers_produce_one_transition(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
) -> None:
    task, approval_id = await open_gate(machine, gate)

    async def answer(n: int) -> str:
        try:
            await gate.decide(approval_id, Decision.APPROVE, actor=f"tab:{n}")
        except ApprovalClosed:
            return "closed"
        return "won"

    results = await asyncio.gather(*(answer(n) for n in range(10)))
    assert results.count("won") == 1

    events = [e.type for e in await repo.list_events(task_id=task.id)]
    assert events.count("approval.decided") == 1
    assert events.count("task.status_changed") == 3  # planning, awaiting, solving
    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.SOLVING


async def test_an_unanswered_gate_expires_closed(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo
) -> None:
    gate = gate_with(approvals, machine, "default", ttl=0.0)
    task, approval_id = await open_gate(machine, gate)

    expired = await gate.sweep_expired()
    assert [a.id for a in expired] == [approval_id]

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.REJECTED
    assert await gate.sweep_expired() == []  # the sweep is itself idempotent


async def test_answering_an_expired_gate_is_refused(
    machine: TaskMachine, approvals: ApprovalRepo
) -> None:
    gate = gate_with(approvals, machine, "default", ttl=0.0)
    _, approval_id = await open_gate(machine, gate)
    await gate.sweep_expired()

    with pytest.raises(ApprovalClosed) as caught:
        await gate.decide(approval_id, Decision.APPROVE, actor="late")
    assert caught.value.approval.status is ApprovalStatus.EXPIRED


async def test_auto_approve_policy_never_stops(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo
) -> None:
    """The benchmark configuration: same code path, envelope decided by rule."""
    gate = gate_with(approvals, machine, "auto_approve")
    task, approval_id = await open_gate(machine, gate)

    approval = await approvals.get(approval_id)
    assert approval is not None
    assert approval.status is ApprovalStatus.APPROVED
    assert approval.decided_by == "policy:auto_approve"

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.SOLVING
