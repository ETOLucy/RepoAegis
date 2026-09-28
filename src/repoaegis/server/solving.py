"""The second half of the seam: an approved plan becomes a reviewed patch.

The approved envelope is where this stage reads its instructions. That is not
just convenient -- it is the point of the gate. What the reviewer agreed to is
exactly what the solver is handed, and the envelope's hash still covers it, so
nothing can be quietly swapped in between the approval and the work.

Solving is a loop of rounds, and the loop is driven by the state machine
rather than by a ``while`` here: one call to ``solve`` is one round. It edits,
hands the tree to the verifier, and either opens the patch gate or moves the
task back to ``SOLVING`` for the worker to pick up again. Each round is a
fresh model session; what carries over is the ``round.finished`` events, read
back as ``Attempt`` records and put in front of the next round. That is the
short-term memory of a task, and it lives in the events table -- so a restart
between rounds costs nothing, and the console can show the reasoning as it
happened.

The checkout is rebuilt if it is missing. A task's pinned commit lives on the
task, so a server restart, a cleaned disk or a machine change costs a clone
rather than the run.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from repoaegis.agent.llm import Budget
from repoaegis.agent.plan import Plan
from repoaegis.agent.rounds import Attempt, TestReport, Verifier
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
ROUND_EVENT = "round.finished"


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
        verifier: Verifier | None = None,
        max_steps: int = 30,
        max_rounds: int = 3,
        budget_usd: float = 0.50,
    ) -> None:
        self._machine = machine
        self._repo = repo
        self._approvals = approvals
        self._gate = gate
        self._workspaces = workspaces
        self._llm_factory = llm_factory
        self._verifier = verifier
        self._max_steps = max_steps
        self._max_rounds = max_rounds
        self._budget_usd = budget_usd

    async def solve(self, task: Task) -> None:
        """Run one round: SOLVING -> (VERIFYING ->) patch gate, SOLVING again, or FAILED."""
        try:
            plan, issue = await self._approved_plan(task)
            path = await self._ensure_checkout(task)
            attempts = await self.attempts(task.id)
            run = await self._implement(task, plan, issue, path, attempts)
            # Earlier rounds' edits are in the tree too, so the diff is the
            # whole patch, not this round's share of it.
            patch = await self._workspaces.diff(task.id) if run.changed or attempts else ""
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

        if self._verifier is None:
            # No sandbox configured: a syntax check is all the verification
            # there is, and the reviewer is told so by the missing report.
            attempt = Attempt(
                round=len(attempts) + 1, hypothesis=run.hypothesis, changed_files=list(run.changed)
            )
            await self._open_gate(task, run, patch, [*attempts, attempt])
            return
        await self._verify(task, run, patch, plan, path, attempts)

    async def attempts(self, task_id: str) -> list[Attempt]:
        """Earlier rounds of this task, oldest first, read back from the event log."""
        events = await self._repo.list_events(task_id=task_id)
        return [Attempt.model_validate(e.payload) for e in events if e.type == ROUND_EVENT]

    async def _approved_plan(self, task: Task) -> tuple[Plan, dict[str, Any]]:
        approved = await self._approvals.latest_approved(task.id, ApprovalKind.PLAN)
        if approved is None:
            raise KeyError("no approved plan for this task")
        plan = Plan.model_validate(approved.payload["plan"])
        issue = approved.payload.get("issue", {})
        return plan, dict(issue)

    async def _implement(
        self, task: Task, plan: Plan, issue: dict[str, Any], path: Path, attempts: list[Attempt]
    ) -> SolveRun:
        budget = Budget(self._budget_usd)
        solver = Solver(
            self._llm_factory(budget),
            Workspace(root=path),
            max_steps=self._max_steps,
            budget=budget,
            on_step=self._reporter(task.id),
        )
        diff = await self._workspaces.diff(task.id) if attempts else ""
        run = await solver.run(
            title=str(issue.get("title") or task.title),
            body=str(issue.get("body") or ""),
            plan=plan,
            attempts=attempts,
            diff=diff,
        )
        await self._machine.emit(
            task.id,
            "solve.finished",
            {
                "round": len(attempts) + 1,
                "stopped_by": run.stopped_by,
                "forced": run.forced,
                "steps": run.steps,
                "changed": list(run.changed),
                "hypothesis": run.hypothesis[:500],
                "cost_usd": round(run.usage.cost_usd, 6),
                "tokens": run.usage.total_tokens,
            },
        )
        return run

    async def _verify(
        self,
        task: Task,
        run: SolveRun,
        patch: str,
        plan: Plan,
        path: Path,
        attempts: list[Attempt],
    ) -> None:
        """Run the tests, record the round, and decide where the task goes next."""
        assert self._verifier is not None
        round_no = len(attempts) + 1
        await self._machine.advance(task.id, TaskStatus.VERIFYING, payload={"round": round_no})
        try:
            report = await self._verifier.run(Workspace(root=path), plan)
        except Exception as exc:
            await self._fail(task, "verifier_error", f"{type(exc).__name__}: {exc}")
            return

        attempt = Attempt(
            round=round_no,
            hypothesis=run.hypothesis,
            changed_files=list(run.changed),
            report=report,
        )
        # The record is the handoff. Emitting it is what makes the next round
        # -- and the console -- able to see this one.
        await self._machine.emit(task.id, ROUND_EVENT, attempt.model_dump())
        history = [*attempts, attempt]

        if report.ok or round_no >= self._max_rounds:
            # Out of rounds is not a failure: a human seeing a red report and
            # the three hypotheses that did not fix it is the useful outcome.
            await self._open_gate(task, run, patch, history)
            return
        await self._machine.advance(
            task.id,
            TaskStatus.SOLVING,
            payload={"round": round_no, "failed": report.failed, "errors": report.errors},
        )

    async def _ensure_checkout(self, task: Task) -> Path:
        path = self._workspaces.checkout_of(task.id)
        if path.is_dir():
            return path
        if not task.repo_sha:
            raise KeyError("the task has no pinned commit to rebuild its checkout from")
        log.info("workspace.rebuilding", task_id=task.id, sha=task.repo_sha[:12])
        ref = parse_issue_url(task.issue_url)
        return await self._workspaces.prepare(ref, task.repo_sha, task_id=task.id)

    async def _open_gate(
        self, task: Task, run: SolveRun, patch: str, history: list[Attempt]
    ) -> None:
        changed = sorted({f for a in history for f in a.changed_files} | set(run.changed))
        last: TestReport | None = history[-1].report if history else None
        payload: dict[str, Any] = {
            "patch": patch,
            "summary": run.summary,
            "hypothesis": run.hypothesis,
            "changed": changed,
            "rounds": len(history),
            "attempts": [a.model_dump() for a in history],
            "report": last.model_dump() if last is not None else None,
            "steps": run.steps,
            "forced": run.forced,
            "cost_usd": round(run.usage.cost_usd, 6),
        }
        await self._machine.advance(
            task.id,
            TaskStatus.AWAITING_PATCH_APPROVAL,
            payload={
                "changed": changed,
                "rounds": len(history),
                "tests_ok": None if last is None else last.ok,
            },
        )
        await self._gate.request(
            task.id, ApprovalKind.PATCH, payload, subject=run.summary[:200] or "patch"
        )

    async def _account(self, task: Task, run: SolveRun) -> None:
        """Costs accumulate across stages and rounds; one task, one bill."""
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
