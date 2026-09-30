"""Leases: who holds a task, for how long, and what happens when they stop.

Everything here is deterministic. Expiry is produced by claiming with a
negative ttl (the lease is already over), and heartbeats run at millisecond
intervals, so no test waits on a real clock.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, Claim, Decision, Task, TaskCreate, TaskStatus
from repoaegis.server.state import StaleLease, TaskMachine, current_claim
from repoaegis.server.storage import TaskRepo
from repoaegis.server.worker import Worker

S = TaskStatus
TTL = 60.0
EXPIRED = -1.0


async def queued(machine: TaskMachine) -> Task:
    return await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/1"))


async def in_state(machine: TaskMachine, *path: TaskStatus) -> Task:
    task = await queued(machine)
    for status in path:
        task = await machine.advance(task.id, status)
    return task


# -- the repository layer -----------------------------------------------------


async def test_claiming_moves_the_task_and_writes_the_lease(repo: TaskRepo, machine: TaskMachine):
    task = await queued(machine)

    claim = await repo.claim(task.id, expected=S.QUEUED, to=S.PLANNING, owner="w1", ttl_seconds=TTL)

    assert claim == Claim(task_id=task.id, token=1)
    current = await repo.get(task.id)
    assert current is not None
    assert current.status is S.PLANNING and current.lease_owner == "w1"
    assert current.lease_until is not None and current.lease_until > datetime.now(UTC)
    # A held task cannot be taken by anyone else.
    assert (
        await repo.claim(task.id, expected=S.PLANNING, to=S.PLANNING, owner="w2", ttl_seconds=TTL)
        is None
    )


async def test_each_claim_gets_the_next_token(repo: TaskRepo, machine: TaskMachine):
    task = await in_state(machine, S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING)
    first = await repo.claim(task.id, expected=S.SOLVING, to=S.SOLVING, owner="w1", ttl_seconds=TTL)
    assert first is not None and first.token == 1
    assert await repo.release(first)

    second = await repo.claim(
        task.id, expected=S.SOLVING, to=S.SOLVING, owner="w2", ttl_seconds=TTL
    )

    assert second is not None and second.token == 2
    assert not await repo.release(first)  # the old claim can no longer touch the lease


async def test_renew_works_only_for_the_current_lease(repo: TaskRepo, machine: TaskMachine):
    task = await queued(machine)
    claim = await repo.claim(task.id, expected=S.QUEUED, to=S.PLANNING, owner="w1", ttl_seconds=1)
    assert claim is not None
    before = (await repo.get(task.id)).lease_until  # type: ignore[union-attr]

    assert await repo.renew(claim, ttl_seconds=TTL)
    after = (await repo.get(task.id)).lease_until  # type: ignore[union-attr]
    assert before is not None and after is not None and after > before

    assert not await repo.renew(Claim(task_id=task.id, token=claim.token + 1), ttl_seconds=TTL)
    await repo.release(claim)
    assert not await repo.renew(claim, ttl_seconds=TTL)  # nothing to renew once released


async def test_writes_with_a_stale_claim_are_refused(repo: TaskRepo, machine: TaskMachine):
    """The fencing token: a worker that lost the task cannot write over the new holder."""
    task = await queued(machine)
    old = await repo.claim(task.id, expected=S.QUEUED, to=S.PLANNING, owner="w1", ttl_seconds=TTL)
    assert old is not None
    await repo.release(old)
    new = await repo.claim(task.id, expected=S.PLANNING, to=S.PLANNING, owner="w2", ttl_seconds=TTL)
    assert new is not None

    with pytest.raises(StaleLease):
        await repo.transition(task.id, expected=S.PLANNING, to=S.AWAITING_APPROVAL, claim=old)
    with pytest.raises(StaleLease):
        await repo.append_event(task.id, "ghost", {}, claim=old)
    with pytest.raises(StaleLease):
        await repo.record_run(task.id, sha=None, steps=9, cost_usd=1.0, claim=old)

    current = await repo.get(task.id)
    assert current is not None and current.status is S.PLANNING and current.steps == 0
    assert all(e.type != "ghost" for e in await repo.list_events(task_id=task.id))
    await repo.append_event(task.id, "real", {}, claim=new)  # the holder's writes go through


async def test_a_lease_less_write_is_refused_while_a_worker_holds_the_task(
    repo: TaskRepo, machine: TaskMachine
):
    """The human path (no claim) may only act on a task nobody is working on."""
    task = await queued(machine)
    claim = await repo.claim(task.id, expected=S.QUEUED, to=S.PLANNING, owner="w1", ttl_seconds=TTL)
    assert claim is not None and current_claim.get() is None

    with pytest.raises(StaleLease):
        await machine.advance(task.id, S.AWAITING_APPROVAL)

    # Entering a waiting state releases the lease in the same statement...
    await machine.advance(task.id, S.AWAITING_APPROVAL, claim=claim)
    current = await repo.get(task.id)
    assert current is not None and current.lease_owner is None and current.lease_until is None
    # ...after which a human's answer is accepted.
    await machine.advance(task.id, S.SOLVING)


async def test_held_tasks_are_skipped_when_looking_for_work(repo: TaskRepo, machine: TaskMachine):
    held = await in_state(machine, S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING)
    free = await in_state(machine, S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING)
    assert await repo.claim(held.id, expected=S.SOLVING, to=S.SOLVING, owner="w1", ttl_seconds=TTL)

    found = await repo.next_in(S.SOLVING)

    assert found is not None and found.id == free.id


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ((S.PLANNING,), S.QUEUED),
        ((S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING), S.SOLVING),
        ((S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING, S.VERIFYING), S.SOLVING),
        (
            (S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING, S.AWAITING_PATCH_APPROVAL, S.DELIVERING),
            S.DELIVERING,
        ),
    ],
)
async def test_an_expired_lease_sends_the_task_back_to_its_stage_entry(
    repo: TaskRepo, machine: TaskMachine, path: tuple[TaskStatus, ...], target: TaskStatus
):
    task = await in_state(machine, *path)
    stage = path[-1]
    assert await repo.claim(task.id, expected=stage, to=stage, owner="dead", ttl_seconds=EXPIRED)

    taken = await machine.reclaim_expired(max_recoveries=3)

    assert [t.task.id for t in taken] == [task.id]
    assert taken[0].previous_owner == "dead" and not taken[0].exhausted
    current = await repo.get(task.id)
    assert current is not None
    assert current.status is target and current.recoveries == 1
    assert current.lease_owner is None and current.lease_until is None
    types = [e.type for e in await repo.list_events(task_id=task.id)]
    assert "task.reclaimed" in types
    # Only a real move is announced as a status change.
    changed = [
        e for e in await repo.list_events(task_id=task.id) if e.type == "task.status_changed"
    ]
    assert (changed[-1].payload.get("reason") == "lease_expired") == (target is not stage)


async def test_a_task_that_keeps_killing_its_worker_ends_up_failed(
    repo: TaskRepo, machine: TaskMachine
):
    task = await in_state(machine, S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING)
    for _ in range(3):
        assert await repo.claim(
            task.id, expected=S.SOLVING, to=S.SOLVING, owner="d", ttl_seconds=EXPIRED
        )
        (taken,) = await machine.reclaim_expired(max_recoveries=3)
        assert not taken.exhausted and taken.task.status is S.SOLVING

    assert await repo.claim(
        task.id, expected=S.SOLVING, to=S.SOLVING, owner="d", ttl_seconds=EXPIRED
    )
    (taken,) = await machine.reclaim_expired(max_recoveries=3)

    assert taken.exhausted and taken.task.status is S.FAILED and taken.task.recoveries == 4
    last = (await repo.list_events(task_id=task.id))[-1]
    assert last.type == "task.status_changed" and last.payload["reason"] == "recovery_exhausted"


async def test_live_leases_are_left_alone(repo: TaskRepo, machine: TaskMachine):
    task = await queued(machine)
    assert await repo.claim(task.id, expected=S.QUEUED, to=S.PLANNING, owner="w1", ttl_seconds=TTL)

    assert await machine.reclaim_expired(max_recoveries=3) == []
    current = await repo.get(task.id)
    assert current is not None and current.lease_owner == "w1" and current.recoveries == 0


# -- the worker ---------------------------------------------------------------


class Idle:
    async def deliver(self, task: Task) -> None: ...

    async def solve(self, task: Task) -> None: ...


class RecordingStage:
    """A planning stand-in that notes the claim it ran under and what the row said."""

    def __init__(self, repo: TaskRepo, machine: TaskMachine) -> None:
        self._repo = repo
        self._machine = machine
        self.claims: list[Claim | None] = []
        self.owners: list[str | None] = []

    async def plan(self, task: Task) -> None:
        self.claims.append(current_claim.get())
        row = await self._repo.get(task.id)
        self.owners.append(row.lease_owner if row else None)
        await self._machine.advance(task.id, S.AWAITING_APPROVAL)  # claim comes from the context


def worker(machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate, planning: object) -> Worker:
    return Worker(
        machine,
        repo,
        gate,
        planning,  # type: ignore[arg-type]
        Idle(),  # type: ignore[arg-type]
        Idle(),  # type: ignore[arg-type]
        poll_seconds=0,
        lease_seconds=TTL,
        heartbeat_seconds=0.01,
        max_recoveries=3,
        owner="w1",
    )


async def test_a_stage_runs_under_a_lease_that_is_released_afterwards(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
):
    task = await queued(machine)
    stage = RecordingStage(repo, machine)

    assert await worker(machine, repo, gate, stage).tick() is True

    assert stage.claims == [Claim(task_id=task.id, token=1)] and stage.owners == ["w1"]
    current = await repo.get(task.id)
    assert current is not None and current.status is S.AWAITING_APPROVAL
    assert current.lease_owner is None and current_claim.get() is None


async def test_the_sweep_runs_before_new_work_is_taken(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
):
    """A planner died; the next tick takes the task back and re-plans it."""
    task = await in_state(machine, S.PLANNING)
    assert await repo.claim(
        task.id, expected=S.PLANNING, to=S.PLANNING, owner="dead", ttl_seconds=EXPIRED
    )
    stage = RecordingStage(repo, machine)

    assert await worker(machine, repo, gate, stage).tick() is True

    assert stage.claims == [Claim(task_id=task.id, token=2)]  # the dead worker's was 1
    types = [e.type for e in await repo.list_events(task_id=task.id)]
    assert types.index("task.reclaimed") < len(types) - 1  # reclaimed first, then re-planned
    assert types[-1] == "task.status_changed"
    current = await repo.get(task.id)
    assert current is not None and current.recoveries == 1


class Slow:
    """A stage that loses its lease to another worker, then tries to keep going."""

    def __init__(self, repo: TaskRepo) -> None:
        self._repo = repo
        self.finished = False

    async def plan(self, task: Task) -> None:
        mine = current_claim.get()
        assert mine is not None
        await self._repo.release(mine)  # stands in for "the lease expired and was swept"
        assert await self._repo.claim(
            task.id, expected=S.PLANNING, to=S.PLANNING, owner="thief", ttl_seconds=TTL
        )
        await asyncio.sleep(5)  # the heartbeat should interrupt long before this ends
        self.finished = True


async def test_a_lost_lease_cancels_the_stage(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
):
    task = await queued(machine)
    stage = Slow(repo)

    await asyncio.wait_for(worker(machine, repo, gate, stage).tick(), timeout=2)

    assert not stage.finished
    current = await repo.get(task.id)
    assert current is not None and current.lease_owner == "thief"  # the thief's lease is intact


class WritesDirectly:
    """A stage that writes through the repository, not the machine, with no claim in hand."""

    def __init__(self, repo: TaskRepo, machine: TaskMachine) -> None:
        self._repo = repo
        self._machine = machine

    async def plan(self, task: Task) -> None:
        # What PlanningService does to record what a run cost: no claim passed.
        await self._repo.record_run(task.id, sha="a" * 40, steps=3, cost_usd=0.01)
        await self._machine.advance(task.id, S.AWAITING_APPROVAL)


async def test_repository_writes_inside_a_stage_carry_the_context_claim(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
):
    """The first real run died here: record_run had no claim and was refused as a stranger."""
    task = await queued(machine)

    assert await worker(machine, repo, gate, WritesDirectly(repo, machine)).tick() is True

    current = await repo.get(task.id)
    assert current is not None
    assert current.status is S.AWAITING_APPROVAL and current.steps == 3


async def test_a_planning_task_nobody_holds_is_planned_again(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
):
    """Left behind by a stage that ended without settling the task."""
    task = await in_state(machine, S.PLANNING)  # no lease: an orphan
    stage = RecordingStage(repo, machine)

    assert await worker(machine, repo, gate, stage).tick() is True

    assert stage.claims == [Claim(task_id=task.id, token=1)]
    current = await repo.get(task.id)
    assert current is not None and current.status is S.AWAITING_APPROVAL


class CountingDelivery:
    def __init__(self) -> None:
        self.calls = 0

    async def deliver(self, task: Task) -> None:
        self.calls += 1


async def test_a_delivery_waiting_on_the_push_gate_is_not_claimed_every_tick(
    machine: TaskMachine, repo: TaskRepo, gate: ApprovalGate
):
    """The drill saw 120 claims in a minute while a human was being asked."""
    task = await in_state(
        machine, S.PLANNING, S.AWAITING_APPROVAL, S.SOLVING, S.AWAITING_PATCH_APPROVAL, S.DELIVERING
    )
    envelope = await gate.request(task.id, ApprovalKind.PUSH, {"branch": "b"}, subject="push it")
    assert envelope.is_open  # the default policy asks a human about pushes
    delivery = CountingDelivery()
    w = Worker(
        machine,
        repo,
        gate,
        Idle(),  # type: ignore[arg-type]
        Idle(),  # type: ignore[arg-type]
        delivery,  # type: ignore[arg-type]
        poll_seconds=0,
        lease_seconds=TTL,
        heartbeat_seconds=0.01,
        owner="w1",
    )

    for _ in range(5):
        await w.tick()

    current = await repo.get(task.id)
    assert current is not None and current.lease_token == 0 and delivery.calls == 0

    await gate.decide(envelope.id, Decision.APPROVE, actor="user:lucy")
    assert await w.tick() is True
    assert delivery.calls == 1
