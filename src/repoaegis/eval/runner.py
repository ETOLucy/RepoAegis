"""Running the real pipeline over one benchmark instance.

The whole product runs, not a shortcut through it: the same state machine, the
same two approval gates, the same planner and solver. Only two things are
swapped, and both are swapped because the benchmark knows better than the live
sources do:

*The issue text* comes from the dataset instead of GitHub. The API would burn
its rate limit over hundreds of instances, and an issue edited after the fix
landed would quietly hand the agent the answer.

*The commit* is the instance's ``base_commit`` instead of the default branch's
head. A fix evaluated against today's code is a fix to a different problem.

The gates run under the ``auto_approve`` policy -- the configuration that
exists precisely so a benchmark can run unattended, and the one half of the
with-gate/without-gate ablation.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from repoaegis.agent.github import Issue
from repoaegis.agent.llm import LLM, Budget, DeepSeek
from repoaegis.agent.workspace import RepoRef, Workspaces
from repoaegis.eval.dataset import Instance, changed_files
from repoaegis.server.config import Settings
from repoaegis.server.events import EventBus
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.models import ApprovalKind, TaskCreate, TaskStatus
from repoaegis.server.planning import PlanningService
from repoaegis.server.policy import get_policy
from repoaegis.server.solving import SolvingService
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, Database, TaskRepo

log = structlog.get_logger(__name__)


class DatasetIssues:
    """An IssueReader backed by the benchmark's own text."""

    def __init__(self, instance: Instance) -> None:
        self._instance = instance

    async def issue(self, ref: RepoRef) -> Issue:
        return Issue(
            number=ref.number or 0,
            title=self._instance.issue_title,
            body=self._instance.problem_statement,
            state="open",
            url=f"https://github.com/{self._instance.repo}/issues/{ref.number or 0}",
        )


@dataclass(frozen=True, slots=True)
class Attempt:
    """What one instance cost and produced, before any test has been run."""

    instance_id: str
    status: str
    patch: str = ""
    planned_files: tuple[str, ...] = ()
    patched_files: tuple[str, ...] = ()
    steps: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    failure: str = ""
    events: tuple[str, ...] = field(default=())

    @property
    def produced_patch(self) -> bool:
        return bool(self.patch.strip())


def issue_url_for(instance: Instance) -> str:
    """``psf__requests-1142`` names issue 1142 of psf/requests."""
    number = instance.instance_id.rsplit("-", 1)[-1]
    return f"https://github.com/{instance.repo}/issues/{number}"


class InstanceRunner:
    """Assembles the production services around one throwaway database."""

    def __init__(
        self,
        settings: Settings,
        workdir: Path,
        *,
        llm_factory: Any = None,
        policy: str = "auto_approve",
        workspaces_factory: Callable[[Path], Workspaces] | None = None,
    ) -> None:
        self._settings = settings
        self._workdir = workdir
        self._policy = policy
        self._llm_factory = llm_factory or self._deepseek
        # Injectable so the harness itself can be tested without a network.
        self._workspaces_factory = workspaces_factory or self._default_workspaces

    def _default_workspaces(self, root: Path) -> Workspaces:
        return Workspaces(
            self._settings.workspace_cache_dir,
            root / "work",
            timeout_seconds=self._settings.git_timeout_seconds,
        )

    def _deepseek(self, budget: Budget) -> LLM:
        return DeepSeek(
            api_key=self._settings.deepseek_api_key,
            model=self._settings.llm_model,
            base_url=self._settings.llm_base_url,
            timeout_seconds=self._settings.llm_timeout_seconds,
            max_retries=self._settings.llm_max_retries,
            budget=budget,
        )

    async def run(self, instance: Instance) -> Attempt:
        started = time.perf_counter()
        root = self._workdir / instance.instance_id
        root.mkdir(parents=True, exist_ok=True)
        database = Database(f"sqlite+aiosqlite:///{(root / 'run.db').as_posix()}")
        await database.create_all()

        try:
            return await self._run(instance, database, root, started)
        finally:
            await database.dispose()

    async def _run(
        self, instance: Instance, database: Database, root: Path, started: float
    ) -> Attempt:
        repo = TaskRepo(database.sessions)
        approvals = ApprovalRepo(database.sessions)
        machine = TaskMachine(repo, EventBus())
        workspaces = self._workspaces_factory(root)
        gate = ApprovalGate(
            approvals,
            machine,
            policy=get_policy(self._policy),
            ttl_seconds=self._settings.approval_ttl_seconds,
        )
        issues = DatasetIssues(instance)
        planning = PlanningService(
            machine,
            repo,
            gate,
            workspaces,
            issues,
            self._llm_factory,
            max_steps=self._settings.agent_max_steps,
            budget_usd=self._settings.llm_budget_usd,
        )
        solving = SolvingService(
            machine,
            repo,
            approvals,
            gate,
            workspaces,
            self._llm_factory,
            max_steps=self._settings.agent_max_edit_steps,
            budget_usd=self._settings.llm_budget_usd,
        )

        task = await machine.create(
            TaskCreate.model_validate({"issue_url": issue_url_for(instance)})
        )
        await machine.advance(task.id, TaskStatus.PLANNING)
        await planning.plan(task, sha=instance.base_commit)

        current = await repo.get(task.id)
        assert current is not None
        if current.status is TaskStatus.SOLVING:
            await solving.solve(current)
            current = await repo.get(task.id)
            assert current is not None

        return await self._collect(instance, repo, approvals, task.id, current, started)

    async def _collect(
        self,
        instance: Instance,
        repo: TaskRepo,
        approvals: ApprovalRepo,
        task_id: str,
        task: Any,
        started: float,
    ) -> Attempt:
        events = await repo.list_events(task_id=task_id)
        plan_gate = await approvals.latest_approved(task_id, ApprovalKind.PLAN)
        patch_gate = await approvals.latest_approved(task_id, ApprovalKind.PATCH)

        planned: tuple[str, ...] = ()
        if plan_gate is not None:
            locations = plan_gate.payload.get("plan", {}).get("locations", [])
            planned = tuple(dict.fromkeys(str(loc["file"]) for loc in locations))

        patch = str(patch_gate.payload.get("patch", "")) if patch_gate else ""
        failure = ""
        if task.status is TaskStatus.FAILED:
            last = [e for e in events if e.type == "task.status_changed"][-1]
            failure = f"{last.payload.get('reason')}: {str(last.payload.get('detail'))[:200]}"

        return Attempt(
            instance_id=instance.instance_id,
            status=str(task.status.value),
            patch=patch,
            planned_files=planned,
            patched_files=changed_files(patch),
            steps=int(task.steps),
            cost_usd=float(task.cost_usd),
            seconds=time.perf_counter() - started,
            failure=failure,
            events=tuple(e.type for e in events),
        )
