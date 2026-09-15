"""The harness drives the real pipeline over one instance -- offline.

Everything here is local: a git repository built in a temp directory stands in
for the checkout, and a scripted model stands in for DeepSeek. What is being
proved is the wiring, not the model: that an instance becomes a task, passes
both gates under auto_approve, and comes back as a patch with its cost.
"""

from pathlib import Path
from typing import Any

import pytest

from repoaegis.agent.llm import Completion, ToolCall, Usage
from repoaegis.agent.workspace import RepoRef, Workspaces, git
from repoaegis.eval.dataset import Instance, changed_files
from repoaegis.eval.runner import InstanceRunner, issue_url_for
from repoaegis.server.config import Settings

SOURCE = "def content_length(body):\n    return len(body)\n"

INSTANCE = Instance(
    instance_id="psf__requests-1142",
    repo="psf/requests",
    base_commit="c" * 40,
    problem_statement="requests sets Content-Length on GET requests\n\nSteps to reproduce…",
    gold_patch=(
        "diff --git a/requests/models.py b/requests/models.py\n"
        "--- a/requests/models.py\n+++ b/requests/models.py\n"
        "@@ -1,2 +1,2 @@\n-    return len(body)\n+    return None\n"
    ),
    test_patch="",
    fail_to_pass=("test_requests.py::test_no_content_length",),
    pass_to_pass=("test_requests.py::test_other",),
    difficulty="<15 min fix",
)

PLAN = {
    "diagnosis": "Content-Length is set unconditionally",
    "locations": [
        {"file": "requests/models.py", "line_start": 1, "line_end": 2, "why": "computes it"}
    ],
    "approach": "return None for bodyless requests",
    "verification": "pytest test_requests.py",
    "confidence": "high",
}


class LocalWorkspaces(Workspaces):
    """A real git checkout, seeded locally instead of cloned."""

    def __init__(self, root: Path) -> None:
        super().__init__(root / "cache", root / "work")
        self.pinned: list[str] = []

    async def resolve_head(self, ref: RepoRef) -> str:  # pragma: no cover - must not be used
        raise AssertionError("the benchmark pins base_commit; HEAD must never be consulted")

    async def prepare(self, ref: RepoRef, sha: str, *, task_id: str) -> Path:
        self.pinned.append(sha)
        path = self.work_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        await git("init", "--quiet", str(path), timeout=30)
        (path / "requests").mkdir()
        (path / "requests" / "models.py").write_text(SOURCE, encoding="utf-8")
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
        return path


def scripted(*completions: Completion) -> Any:
    script = list(completions)

    class Scripted:
        async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
            return script.pop(0)

    return lambda budget: Scripted()


def tool(name: str, arguments: dict[str, Any], id: str = "c") -> Completion:
    return Completion(
        tool_calls=(ToolCall(id=id, name=name, arguments=arguments),),
        usage=Usage(cache_miss_tokens=100, completion_tokens=20, cost_usd=0.001),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        deepseek_api_key="unused",
        workspace_cache_dir=tmp_path / "cache",
        workspace_work_dir=tmp_path / "work",
        agent_max_steps=4,
        agent_max_edit_steps=4,
    )


def test_the_issue_url_names_the_instances_issue() -> None:
    assert issue_url_for(INSTANCE) == "https://github.com/psf/requests/issues/1142"


async def test_an_instance_runs_through_both_gates_and_yields_a_patch(
    settings: Settings, tmp_path: Path
) -> None:
    spaces = LocalWorkspaces(tmp_path / "spaces")
    runner = InstanceRunner(
        settings,
        tmp_path / "runs",
        llm_factory=scripted(
            tool("submit_plan", PLAN, id="plan"),
            tool(
                "replace",
                {
                    "path": "requests/models.py",
                    "old": "    return len(body)",
                    "new": "    return None",
                },
                id="edit",
            ),
            tool("finish", {"summary": "no Content-Length without a body"}, id="fin"),
        ),
        workspaces_factory=lambda root: spaces,
    )

    attempt = await runner.run(INSTANCE)

    # auto_approve answers both gates, so the task lands past the second one.
    assert attempt.status == "delivering"
    assert attempt.produced_patch
    assert attempt.planned_files == ("requests/models.py",)
    assert attempt.patched_files == ("requests/models.py",)
    assert "-    return len(body)" in attempt.patch
    assert "+    return None" in attempt.patch
    assert attempt.steps == 3  # one planning step, two solving steps
    assert attempt.cost_usd == pytest.approx(0.003)
    assert attempt.seconds > 0


async def test_the_pinned_commit_is_the_instances_not_the_branch_head(
    settings: Settings, tmp_path: Path
) -> None:
    """A fix evaluated against today's code is a fix to a different problem."""
    spaces = LocalWorkspaces(tmp_path / "spaces")
    runner = InstanceRunner(
        settings,
        tmp_path / "runs",
        llm_factory=scripted(
            tool("submit_plan", PLAN, id="plan"), tool("finish", {"summary": "x"})
        ),
        workspaces_factory=lambda root: spaces,
    )

    await runner.run(INSTANCE)

    assert spaces.pinned == [INSTANCE.base_commit]


async def test_the_model_is_told_the_dataset_text_not_a_fetched_one(
    settings: Settings, tmp_path: Path
) -> None:
    seen: list[Any] = []

    class Capturing:
        async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
            seen.extend(messages)
            return tool("submit_plan", PLAN, id="plan")

    runner = InstanceRunner(
        settings,
        tmp_path / "runs",
        llm_factory=lambda budget: Capturing(),
        workspaces_factory=lambda root: LocalWorkspaces(tmp_path / "spaces"),
    )
    await runner.run(INSTANCE)

    brief = str(seen[1]["content"])
    assert "Content-Length on GET requests" in brief
    assert "return None" not in brief  # the gold patch must never leak in


async def test_a_failed_instance_reports_why(settings: Settings, tmp_path: Path) -> None:
    class Exploding:
        async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
            raise RuntimeError("provider is down")

    runner = InstanceRunner(
        settings,
        tmp_path / "runs",
        llm_factory=lambda budget: Exploding(),
        workspaces_factory=lambda root: LocalWorkspaces(tmp_path / "spaces"),
    )

    attempt = await runner.run(INSTANCE)

    assert attempt.status == "failed"
    assert not attempt.produced_patch
    assert "RuntimeError" in attempt.failure and "provider is down" in attempt.failure


def test_gold_files_are_read_from_the_patch() -> None:
    assert INSTANCE.gold_files == ("requests/models.py",)
    assert changed_files("") == ()
    assert changed_files("--- /dev/null\n+++ b/new.py\n") == ("new.py",)
