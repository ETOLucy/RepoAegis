"""The second half of the seam: an approved plan becomes a reviewed patch.

The approved envelope is where this stage reads its instructions. That is not
just convenient -- it is the point of the gate. What the reviewer agreed to is
exactly what the solver is handed, and the envelope's hash still covers it, so
nothing can be quietly swapped in between the approval and the work.

The checkout is rebuilt if it is missing. A task's pinned commit lives on the
task, so a server restart, a cleaned disk or a machine change costs a clone
rather than the run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from repoaegis.agent.llm import Budget
from repoaegis.agent.plan import Plan
from repoaegis.agent.solve import Solver, SolveRun
from repoaegis.agent.tools import Workspace
from repoaegis.agent.workspace import Workspaces, parse_issue_url
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, Task, TaskStatus
from repoaegis.server.planning import LLMFactory
from repoaegis.server.state import (
    ConcurrentTransition,
    IllegalTransition,
    TaskMachine,
    TaskNotFound,
)
from repoaegis.server.storage import ApprovalRepo, TaskRepo

log = structlog.get_logger(__name__)

MAX_PATCH_CHARS = 400_000


class SolvingService:
    def __init__(
        self,
        machine: TaskMachine,
        repo: TaskRepo,
        approvals: ApprovalRepo,
        gate: ApprovalGate,
        workspaces: Workspaces,
        llm_factory: LLMFactory,
        *,
        max_steps: int = 30,
        budget_usd: float = 0.50,
    ) -> None:
        self._machine = machine
        self._repo = repo
        self._approvals = approvals
        self._gate = gate
        self._workspaces = workspaces
        self._llm_factory = llm_factory
        self._max_steps = max_steps
        self._budget_usd = budget_usd

    async def solve(self, task: Task) -> None:
        """Take an approved task from SOLVING to the patch gate, or to FAILED."""
        try:
            run, patch = await self._implement(task)
        except Exception as exc:
            # See PlanningService: a wedged task is worse than a failed one.
            await self._fail(task, type(exc).__name__, str(exc))
            return

        await self._account(task, run)
        if not run.ok:
            await self._fail(
                task,
                run.stopped_by,
                "; ".join(run.problems) or f"the solver stopped at {run.stopped_by}",
            )
            return
        if not patch.strip():
            await self._fail(task, "empty_patch", "the solver reported success but changed nothing")
            return
        if len(patch) > MAX_PATCH_CHARS:
            await self._fail(
                task, "patch_too_large", f"{len(patch)} characters; the limit is {MAX_PATCH_CHARS}"
            )
            return

        await self._open_gate(task, run, patch)

    async def _implement(self, task: Task) -> tuple[SolveRun, str]:
        approved = await self._approvals.latest_approved(task.id, ApprovalKind.PLAN)
        if approved is None:
            raise KeyError("no approved plan for this task")
        plan = Plan.model_validate(approved.payload["plan"])
        issue = approved.payload.get("issue", {})

        path = await self._ensure_checkout(task)
        budget = Budget(self._budget_usd)
        solver = Solver(
            self._llm_factory(budget),
            Workspace(root=path),
            max_steps=self._max_steps,
            budget=budget,
            on_step=self._reporter(task.id),
        )
        run = await solver.run(
            title=str(issue.get("title") or task.title),
            body=str(issue.get("body") or ""),
            plan=plan,
        )
        await self._machine.emit(
            task.id,
            "solve.finished",
            {
                "stopped_by": run.stopped_by,
                "forced": run.forced,
                "steps": run.steps,
                "changed": list(run.changed),
                "cost_usd": round(run.usage.cost_usd, 6),
                "tokens": run.usage.total_tokens,
            },
        )
        patch = await self._workspaces.diff(task.id) if run.changed else ""
        return run, patch

    async def _ensure_checkout(self, task: Task) -> Any:
        path = self._workspaces.checkout_of(task.id)
        if path.is_dir():
            return path
        if not task.repo_sha:
            raise KeyError("the task has no pinned commit to rebuild its checkout from")
        log.info("workspace.rebuilding", task_id=task.id, sha=task.repo_sha[:12])
        ref = parse_issue_url(task.issue_url)
        return await self._workspaces.prepare(ref, task.repo_sha, task_id=task.id)

    async def _open_gate(self, task: Task, run: SolveRun, patch: str) -> None:
        payload: dict[str, Any] = {
            "patch": patch,
            "summary": run.summary,
            "changed": list(run.changed),
            "steps": run.steps,
            "forced": run.forced,
            "cost_usd": round(run.usage.cost_usd, 6),
        }
        await self._machine.advance(
            task.id, TaskStatus.AWAITING_PATCH_APPROVAL, payload={"changed": list(run.changed)}
        )
        await self._gate.request(
            task.id, ApprovalKind.PATCH, payload, subject=run.summary[:200] or "patch"
        )

    async def _account(self, task: Task, run: SolveRun) -> None:
        """Costs accumulate across stages; one task, one bill."""
        current = await self._repo.get(task.id)
        before = current or task
        await self._repo.record_run(
            task.id,
            sha=before.repo_sha,
            steps=before.steps + run.steps,
            cost_usd=before.cost_usd + run.usage.cost_usd,
        )

    async def _fail(self, task: Task, reason: str, detail: str) -> None:
        log.warning("solving_failed", task_id=task.id, reason=reason, detail=detail)
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
        await self._workspaces.release(task.id)

    def _reporter(self, task_id: str) -> Callable[[str, dict[str, Any]], Any]:
        async def report(kind: str, payload: dict[str, Any]) -> None:
            await self._machine.emit(task_id, kind, payload)

        return report
