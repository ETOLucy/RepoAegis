"""The last mile: an approved patch becomes a pull request.

This is the first stage whose work leaves the machine, and it is the reason the
approval gate was built. Everything before it happens inside a worktree that
can be deleted without trace; a push cannot be taken back. Other people get
notified, CI starts, and a retraction still leaves a record.

So delivery asks before it pushes, through the same gate as everything else --
but this time the ``push`` kind, which the default policy sends to a human and
the benchmark policy refuses outright. A scoring run that published hundreds of
branches would be a very expensive way to learn that "unattended" and "outward"
are different words.

The destination is always a fork under the token owner's account, never the
upstream project. Maintainers did not ask for machine-generated pull requests,
and several projects now refuse them outright; pushing inside your own
namespace makes the question moot.
"""

from __future__ import annotations

from typing import Any

import structlog

from repoaegis.agent.github import GitHub, GitHubWriter, PullRequest
from repoaegis.agent.plan import Plan
from repoaegis.agent.pr_text import render, title_for
from repoaegis.agent.workspace import Workspaces, authenticated_url, parse_issue_url
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, ApprovalStatus, Task, TaskStatus
from repoaegis.server.state import (
    ConcurrentTransition,
    IllegalTransition,
    TaskMachine,
    TaskNotFound,
)
from repoaegis.server.storage import ApprovalRepo, TaskRepo

log = structlog.get_logger(__name__)

BRANCH_PREFIX = "repoaegis"


class DeliveryService:
    def __init__(
        self,
        machine: TaskMachine,
        repo: TaskRepo,
        approvals: ApprovalRepo,
        gate: ApprovalGate,
        workspaces: Workspaces,
        github: GitHub,
        *,
        token: str,
        model: str = "",
        branch_prefix: str = BRANCH_PREFIX,
    ) -> None:
        self._machine = machine
        self._repo = repo
        self._approvals = approvals
        self._gate = gate
        self._workspaces = workspaces
        self._writer = GitHubWriter(github)
        self._token = token
        self._model = model
        self._prefix = branch_prefix

    async def deliver(self, task: Task) -> None:
        """Push and open a pull request, or wait for the gate, or fail.

        Returning without moving the task means a human is being waited on --
        the worker will find it again, see a pending envelope, and leave it be.
        """
        try:
            approved = await self._push_approval(task)
        except Exception as exc:
            await self._fail(task, type(exc).__name__, str(exc))
            return
        if approved is None:
            return  # a pending gate; nothing to do until someone answers

        try:
            pull = await self._publish(task)
        except Exception as exc:
            await self._fail(task, type(exc).__name__, str(exc))
            return

        await self._machine.emit(
            task.id,
            "pull_request.opened",
            {"url": pull.url, "number": pull.number, "repo": pull.repo, "branch": pull.branch},
        )
        await self._machine.advance(task.id, TaskStatus.DONE, payload={"pull_request": pull.url})
        # The checkout has done its job; the branch now lives on GitHub.
        await self._workspaces.release(task.id)

    async def _push_approval(self, task: Task) -> bool | None:
        """True once a push is authorised, None while a human is being asked."""
        existing = await self._approvals.list_for_task(task.id)
        pushes = [a for a in existing if a.kind is ApprovalKind.PUSH]
        if any(a.status is ApprovalStatus.APPROVED for a in pushes):
            return True
        if any(a.is_open for a in pushes):
            return None
        if pushes:  # every push envelope was refused or expired
            raise PermissionError("the push was refused")

        ref = parse_issue_url(task.issue_url)
        branch = self._branch_for(task)
        approval = await self._gate.request(
            task.id,
            ApprovalKind.PUSH,
            {"repo": ref.slug, "branch": branch, "issue_url": task.issue_url},
            subject=f"push {branch} to a fork of {ref.slug} and open a pull request",
        )
        if approval.status is ApprovalStatus.APPROVED:
            return True
        if approval.is_open:
            return None
        raise PermissionError(f"the push was refused by policy: {approval.reason}")

    def _branch_for(self, task: Task) -> str:
        return f"{self._prefix}/{task.id[:12]}"

    async def _publish(self, task: Task) -> PullRequest:
        upstream = parse_issue_url(task.issue_url)
        plan, summary, changed = await self._approved_work(task)
        branch = self._branch_for(task)
        title = title_for(plan, task.title)

        fork = await self._writer.ensure_fork(upstream)
        base = await self._writer.default_branch(fork)
        # Bring the fork's base up to upstream before the branch lands on it,
        # so the push carries only this task's commit and the pull request
        # compares against a current base. A diverged fork is not fatal: the
        # push may still succeed, and if it does not, the error names why.
        synced = await self._writer.sync_fork(fork, branch=base)
        await self._machine.emit(
            task.id, "fork.synced", {"repo": fork.slug, "branch": base, "result": synced}
        )

        await self._workspaces.commit(task.id, message=title, branch=branch)
        await self._workspaces.push(
            task.id,
            url=authenticated_url(fork.owner, fork.repo, self._token),
            branch=branch,
            force=True,
        )
        await self._machine.emit(
            task.id, "branch.pushed", {"repo": fork.slug, "branch": branch, "base": base}
        )

        body = render(
            plan=plan,
            summary=summary,
            changed=changed,
            issue_url=task.issue_url,
            steps=task.steps,
            cost_usd=task.cost_usd,
            model=self._model,
        )
        # A redone delivery finds the pull request its first attempt opened.
        existing = await self._writer.find_pull_request(fork, head=f"{fork.owner}:{branch}")
        if existing is not None:
            log.info("delivery.reusing_pull_request", task_id=task.id, number=existing.number)
            return existing
        return await self._writer.open_pull_request(
            fork, head=branch, base=base, title=title, body=body
        )

    async def _approved_work(self, task: Task) -> tuple[Plan, str, list[str]]:
        plan_gate = await self._approvals.latest_approved(task.id, ApprovalKind.PLAN)
        patch_gate = await self._approvals.latest_approved(task.id, ApprovalKind.PATCH)
        if plan_gate is None or patch_gate is None:
            raise KeyError("delivery needs both an approved plan and an approved patch")
        plan = Plan.model_validate(plan_gate.payload["plan"])
        payload: dict[str, Any] = patch_gate.payload
        return plan, str(payload.get("summary") or ""), list(payload.get("changed") or [])

    async def _fail(self, task: Task, reason: str, detail: str) -> None:
        log.warning("delivery_failed", task_id=task.id, reason=reason, detail=detail)
        to = TaskStatus.REJECTED if reason == "PermissionError" else TaskStatus.FAILED
        try:
            await self._machine.advance(
                task.id, to, payload={"reason": reason, "detail": detail[:1000]}
            )
        except (IllegalTransition, ConcurrentTransition, TaskNotFound) as exc:
            log.warning("fail_transition_ignored", task_id=task.id, error=str(exc))
        await self._workspaces.release(task.id)
