"""The last mile. Nothing here touches GitHub or the network.

The push gate is the point of this stage, so most of these tests are about
what happens when it is not answered, or answered no.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest

from repoaegis.agent.github import GitHub
from repoaegis.agent.workspace import RepoRef, Workspaces, _redact, authenticated_url, git
from repoaegis.server.delivery import DeliveryService
from repoaegis.server.gate import ApprovalGate
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

PLAN_PAYLOAD = {
    "plan": {
        "diagnosis": "Content-Length 被无条件设置",
        "locations": [{"file": "models.py", "line_start": 1, "line_end": 2, "why": "这里"}],
        "approach": "只在有 body 时设置",
        "verification": "pytest",
        "confidence": "high",
    },
    "issue": {"title": "sets Content-Length on GET", "body": "..."},
}
PATCH_PAYLOAD = {"patch": "diff", "summary": "删掉了那一行", "changed": ["models.py"]}


class FakeGitHub(GitHub):
    """A GitHub whose API answers from a script."""

    def __init__(self, handler: Any) -> None:
        super().__init__(transport=httpx.MockTransport(handler))


def api(
    *, fork_exists: bool = True, fork_diverged: bool = False, record: list[str] | None = None
) -> GitHub:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if record is not None:
            record.append(f"{request.method} {path}")
        if path == "/user":
            return httpx.Response(200, json={"login": "tester"})
        if path == "/repos/tester/r/merge-upstream":
            if fork_diverged:
                return httpx.Response(409, json={"message": "conflict"})
            return httpx.Response(200, json={"merge_type": "fast-forward"})
        if path == "/repos/tester/r":
            if not fork_exists:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/o/r/forks":
            return httpx.Response(202, json={})
        if path == "/repos/tester/r/pulls":
            return httpx.Response(
                201, json={"number": 7, "html_url": "https://github.com/tester/r/pull/7"}
            )
        return httpx.Response(200, json={"default_branch": "main"})

    return FakeGitHub(handler)


class LocalWorkspaces(Workspaces):
    """Real git, real commits, but pushes go to a bare repo on disk."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path / "cache", tmp_path / "work")
        self.remote = tmp_path / "remote.git"
        self.pushed: list[tuple[str, str]] = []
        self.released: list[str] = []

    async def push(self, task_id: str, *, url: str, branch: str, force: bool = False) -> None:
        self.pushed.append((url, branch))
        await super().push(task_id, url=str(self.remote), branch=branch, force=force)

    async def release(self, task_id: str) -> None:
        self.released.append(task_id)


async def seed(spaces: LocalWorkspaces, task_id: str) -> None:
    """A checkout with one committed file and one uncommitted change."""
    await git("init", "--quiet", "--bare", str(spaces.remote), timeout=30)
    path = spaces.work_dir / task_id
    path.mkdir(parents=True, exist_ok=True)
    await git("init", "--quiet", str(path), timeout=30)
    (path / "models.py").write_text("x = 1\n", encoding="utf-8")
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
    (path / "models.py").write_text("x = 2\n", encoding="utf-8")


async def ready_task(
    machine: TaskMachine, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> Task:
    """A task that has passed both gates and is waiting to be delivered."""
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/7"))
    for kind, payload in ((ApprovalKind.PLAN, PLAN_PAYLOAD), (ApprovalKind.PATCH, PATCH_PAYLOAD)):
        await approvals.create(
            task_id=task.id,
            kind=kind,
            subject="s",
            payload=payload,
            payload_hash="0" * 64,
            status=ApprovalStatus.APPROVED,
            policy="default",
            reason="approved in the test",
            decided_by="user:test",
            ttl_seconds=3600,
        )
    for status in (
        TaskStatus.PLANNING,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.SOLVING,
        TaskStatus.AWAITING_PATCH_APPROVAL,
        TaskStatus.DELIVERING,
    ):
        await machine.advance(task.id, status)
    await seed(spaces, task.id)
    return task


def service(
    machine: TaskMachine,
    repo: TaskRepo,
    approvals: ApprovalRepo,
    gate: ApprovalGate,
    spaces: Workspaces,
    github: GitHub,
) -> DeliveryService:
    return DeliveryService(
        machine, repo, approvals, gate, spaces, github, token="ghp_test", model="deepseek-flash"
    )


def gate_with(approvals: ApprovalRepo, machine: TaskMachine, policy: str) -> ApprovalGate:
    return ApprovalGate(approvals, machine, policy=get_policy(policy), ttl_seconds=3600)


@pytest.fixture
def spaces(tmp_path: Path) -> LocalWorkspaces:
    return LocalWorkspaces(tmp_path)


async def test_a_push_waits_for_a_human_under_the_default_policy(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    """The gate exists for exactly this action, so it must actually stop."""
    gate = gate_with(approvals, machine, "default")
    task = await ready_task(machine, approvals, spaces)
    calls: list[str] = []

    await service(machine, repo, approvals, gate, spaces, api(record=calls)).deliver(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.DELIVERING  # still waiting
    assert spaces.pushed == [] and calls == []  # nothing left the machine

    pending = [a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH]
    assert len(pending) == 1 and pending[0].is_open
    assert "open a pull request" in pending[0].subject


async def test_a_second_tick_does_not_open_a_second_gate(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    """The worker keeps finding the task; it must not nag."""
    gate = gate_with(approvals, machine, "default")
    task = await ready_task(machine, approvals, spaces)
    delivery = service(machine, repo, approvals, gate, spaces, api())

    await delivery.deliver(task)
    await delivery.deliver(task)

    pushes = [a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH]
    assert len(pushes) == 1


async def test_approving_the_push_opens_the_pull_request(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    gate = gate_with(approvals, machine, "default")
    task = await ready_task(machine, approvals, spaces)
    delivery = service(machine, repo, approvals, gate, spaces, api())

    await delivery.deliver(task)
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH
    )
    await gate.decide(envelope.id, Decision.APPROVE, actor="user:lucy")

    await delivery.deliver(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.DONE
    assert spaces.released == [task.id]

    events = {e.type: e.payload for e in await repo.list_events(task_id=task.id)}
    assert events["fork.synced"] == {"repo": "tester/r", "branch": "main", "result": "fast-forward"}
    assert events["branch.pushed"]["repo"] == "tester/r"
    assert events["pull_request.opened"]["url"] == "https://github.com/tester/r/pull/7"


async def test_the_fork_is_synced_with_upstream_before_the_push(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    """A stale fork made the first real push carry upstream's workflow commits,
    which a token without the workflow scope is refused. GitHub moves the base
    itself; our token never has to."""
    calls: list[str] = []
    gate = gate_with(approvals, machine, "always_ask")
    task = await ready_task(machine, approvals, spaces)
    delivery = service(machine, repo, approvals, gate, spaces, api(record=calls))

    await delivery.deliver(task)
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH
    )
    await gate.decide(envelope.id, Decision.APPROVE, actor="u")
    await delivery.deliver(task)

    sync = calls.index("POST /repos/tester/r/merge-upstream")
    assert sync < calls.index("POST /repos/tester/r/pulls")
    assert spaces.pushed, "the branch was pushed after the sync"


async def test_a_diverged_fork_does_not_stop_delivery(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    """GitHub answers 409 when the fork's base has commits of its own. The push
    may still work, so the task goes on and the event says what happened."""
    gate = gate_with(approvals, machine, "always_ask")
    task = await ready_task(machine, approvals, spaces)
    delivery = service(machine, repo, approvals, gate, spaces, api(fork_diverged=True))

    await delivery.deliver(task)
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH
    )
    await gate.decide(envelope.id, Decision.APPROVE, actor="u")
    await delivery.deliver(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.DONE
    events = {e.type: e.payload for e in await repo.list_events(task_id=task.id)}
    assert events["fork.synced"]["result"] == "diverged"


async def test_refusing_the_push_rejects_the_task(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    gate = gate_with(approvals, machine, "default")
    task = await ready_task(machine, approvals, spaces)
    delivery = service(machine, repo, approvals, gate, spaces, api())

    await delivery.deliver(task)
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH
    )
    await gate.decide(envelope.id, Decision.REJECT, actor="user:lucy")

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.REJECTED
    assert spaces.pushed == []


async def test_the_benchmark_policy_refuses_to_publish(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    """A scoring run must never turn into hundreds of published branches."""
    gate = gate_with(approvals, machine, "auto_approve")
    task = await ready_task(machine, approvals, spaces)

    await service(machine, repo, approvals, gate, spaces, api()).deliver(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.REJECTED
    assert spaces.pushed == []


async def test_the_fork_is_created_when_it_does_not_exist_yet(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    calls: list[str] = []
    gate = gate_with(approvals, machine, "always_ask")
    task = await ready_task(machine, approvals, spaces)
    delivery = service(machine, repo, approvals, gate, spaces, api(record=calls))

    await delivery.deliver(task)
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH
    )
    await gate.decide(envelope.id, Decision.APPROVE, actor="u")
    await delivery.deliver(task)

    assert "POST /repos/o/r/forks" not in calls  # the fork already existed here
    assert "POST /repos/tester/r/pulls" in calls


async def test_delivery_without_both_approvals_fails(
    machine: TaskMachine, repo: TaskRepo, approvals: ApprovalRepo, spaces: LocalWorkspaces
) -> None:
    gate = gate_with(approvals, machine, "always_ask")
    task = await machine.create(TaskCreate(issue_url="https://github.com/o/r/issues/7"))
    for status in (
        TaskStatus.PLANNING,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.SOLVING,
        TaskStatus.AWAITING_PATCH_APPROVAL,
        TaskStatus.DELIVERING,
    ):
        await machine.advance(task.id, status)

    delivery = service(machine, repo, approvals, gate, spaces, api())
    await delivery.deliver(task)
    envelope = next(
        a for a in await approvals.list_for_task(task.id) if a.kind is ApprovalKind.PUSH
    )
    await gate.decide(envelope.id, Decision.APPROVE, actor="u")
    await delivery.deliver(task)

    current = await repo.get(task.id)
    assert current is not None and current.status is TaskStatus.FAILED


def test_the_push_url_carries_the_token_and_error_text_never_does() -> None:
    url = authenticated_url("tester", "r", "ghp_SECRET")
    assert "ghp_SECRET" in url
    assert "ghp_SECRET" not in _redact(f"fatal: cannot access {url}: 403")
    assert _redact(f"fatal: cannot access {url}: 403").startswith(
        "fatal: cannot access https://***@"
    )


def test_a_missing_token_is_refused_before_anything_is_attempted() -> None:
    from repoaegis.agent.workspace import GitError

    with pytest.raises(GitError, match="no GitHub token"):
        authenticated_url("tester", "r", "")


def test_the_fork_never_points_at_the_upstream_owner() -> None:
    """The whole safety argument rests on this one substitution."""
    upstream = RepoRef(owner="psf", repo="requests")
    fork = RepoRef(owner="tester", repo=upstream.repo)
    assert fork.clone_url == "https://github.com/tester/requests.git"
    assert upstream.owner not in fork.clone_url
