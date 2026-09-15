"""Where the agent line meets the server line.

The worker knows how to claim a task; the planner knows how to read a
repository. This module is the seam: it fetches the issue, pins a commit,
checks the code out, runs the loop, and turns each of those into an event and a
state transition. Nothing above it imports the agent, and nothing in the agent
imports the database.

Planning is the first genuinely slow phase -- a clone plus twenty model calls
can run past a minute -- so every step the loop reports becomes an event
immediately. A console that shows nothing for ninety seconds is
indistinguishable from a console showing a crash.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from repoaegis.agent.github import IssueReader
from repoaegis.agent.llm import LLM, Budget
from repoaegis.agent.loop import Planner, PlanRun
from repoaegis.agent.plan import Plan
from repoaegis.agent.tools import Workspace
from repoaegis.agent.workspace import Workspaces, parse_issue_url
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, Task, TaskStatus
from repoaegis.server.state import (
    ConcurrentTransition,
    IllegalTransition,
    TaskMachine,
    TaskNotFound,
)
from repoaegis.server.storage import TaskRepo

log = structlog.get_logger(__name__)

LLMFactory = Callable[[Budget], LLM]


class PlanningService:
    def __init__(
        self,
        machine: TaskMachine,
        repo: TaskRepo,
        gate: ApprovalGate,
        workspaces: Workspaces,
        github: IssueReader,
        llm_factory: LLMFactory,
        *,
        max_steps: int = 20,
        budget_usd: float = 0.50,
    ) -> None:
        self._machine = machine
        self._repo = repo
        self._gate = gate
        self._workspaces = workspaces
        self._github = github
        self._llm_factory = llm_factory
        self._max_steps = max_steps
        self._budget_usd = budget_usd

    async def plan(self, task: Task, *, sha: str | None = None) -> None:
        """Take a claimed task from PLANNING to the approval gate, or to FAILED.

        ``sha`` pins the checkout. Live runs leave it unset and take the default
        branch's head; the benchmark passes the commit the issue was filed
        against, because a fix evaluated on today's code is evaluated against a
        different problem.
        """
        try:
            run, resolved, issue = await self._investigate(task, sha)
        except Exception as exc:
            # Anything at all: a provider 400, a disk error, a bug of ours. The
            # one outcome that must never happen is a task left in PLANNING
            # forever, which is what an escaping exception used to produce.
            await self._fail(task, type(exc).__name__, str(exc))
            return

        await self._repo.record_run(
            task.id, sha=resolved, steps=run.steps, cost_usd=run.usage.cost_usd
        )
        if run.plan is None:
            await self._fail(
                task,
                run.stopped_by,
                "; ".join(run.problems) or "the model never produced a usable plan",
            )
            return

        await self._open_gate(task, run, run.plan, issue)

    async def _investigate(
        self, task: Task, sha: str | None
    ) -> tuple[PlanRun, str, dict[str, str]]:
        ref = parse_issue_url(task.issue_url)
        issue = await self._github.issue(ref)
        await self._machine.emit(
            task.id,
            "issue.fetched",
            {"repo": ref.slug, "number": issue.number, "title": issue.title, "state": issue.state},
        )

        sha = sha or await self._workspaces.resolve_head(ref)
        path = await self._workspaces.prepare(ref, sha, task_id=task.id)
        await self._machine.emit(
            task.id, "workspace.ready", {"repo": ref.slug, "sha": sha, "path": str(path)}
        )

        budget = Budget(self._budget_usd)
        planner = Planner(
            self._llm_factory(budget),
            Workspace(root=path),
            max_steps=self._max_steps,
            budget=budget,
            on_step=self._reporter(task.id),
        )
        run = await planner.run(title=issue.title, body=issue.body)
        await self._machine.emit(
            task.id,
            "plan.finished",
            {
                "stopped_by": run.stopped_by,
                "forced": run.forced,
                "steps": run.steps,
                "cost_usd": round(run.usage.cost_usd, 6),
                "tokens": run.usage.total_tokens,
            },
        )
        return run, sha, {"title": issue.title, "body": issue.body}

    async def _open_gate(self, task: Task, run: PlanRun, plan: Plan, issue: dict[str, str]) -> None:
        payload: dict[str, Any] = {
            "plan": plan.model_dump(),
            # The solver reads its brief from the approved envelope, so the text
            # it works from is exactly the text the reviewer saw.
            "issue": issue,
            "steps": run.steps,
            "forced": run.forced,
            "cost_usd": round(run.usage.cost_usd, 6),
        }
        await self._machine.advance(task.id, TaskStatus.AWAITING_APPROVAL, payload=payload)
        await self._gate.request(task.id, ApprovalKind.PLAN, payload, subject=plan.diagnosis[:200])

    async def _fail(self, task: Task, reason: str, detail: str) -> None:
        log.warning("planning_failed", task_id=task.id, reason=reason, detail=detail)
        try:
            await self._machine.advance(
                task.id,
                TaskStatus.FAILED,
                payload={"reason": reason, "detail": detail[:1000]},
            )
        except (IllegalTransition, ConcurrentTransition, TaskNotFound) as exc:
            # The task already moved or vanished. Raising here would re-open
            # the very hole this handler exists to close.
            log.warning("fail_transition_ignored", task_id=task.id, error=str(exc))
        # A task that will never be worked on has no use for its checkout. An
        # approved one keeps its workspace: the next round edits the code there.
        await self._workspaces.release(task.id)

    def _reporter(self, task_id: str) -> Callable[[str, dict[str, Any]], Any]:
        async def report(kind: str, payload: dict[str, Any]) -> None:
            await self._machine.emit(task_id, kind, payload)

        return report
