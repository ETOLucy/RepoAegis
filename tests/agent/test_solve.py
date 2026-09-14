"""The editing loop. Its contract: finishing is not the model's word alone."""

from pathlib import Path
from typing import Any

import pytest

from repoaegis.agent.llm import Budget, Completion, Message, ToolCall, Usage
from repoaegis.agent.plan import Location, Plan
from repoaegis.agent.solve import Solver
from repoaegis.agent.tools import Workspace
from repoaegis.agent.verify import check_syntax

SOURCE = "def resolve(resp):\n    return resp\n"

PLAN = Plan(
    diagnosis="the fragment is dropped",
    locations=[Location(file="src/sessions.py", line_start=1, line_end=2, why="here")],
    approach="carry the fragment through",
    verification="pytest tests/test_redirects.py",
)


class ScriptedLLM:
    def __init__(self, *completions: Completion) -> None:
        self.script = list(completions)
        self.offered: list[list[str]] = []
        self.seen: list[list[Message]] = []

    async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
        self.seen.append(list(messages))
        self.offered.append([t["function"]["name"] for t in (tools or [])])
        return self.script.pop(0)


def call(name: str, arguments: dict[str, Any], id: str = "c1") -> Completion:
    return Completion(tool_calls=(ToolCall(id=id, name=name, arguments=arguments),))


def finish(summary: str = "done") -> Completion:
    return call("finish", {"summary": summary}, id="fin")


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sessions.py").write_text(SOURCE, encoding="utf-8")
    return Workspace(root=tmp_path)


async def test_a_normal_run_edits_then_finishes(ws: Workspace) -> None:
    llm = ScriptedLLM(
        call("replace", {"path": "src/sessions.py", "old": "return resp", "new": "return resp2"}),
        finish("carried the fragment"),
    )
    run = await Solver(llm, ws).run(title="t", body="b", plan=PLAN)

    assert run.ok and run.stopped_by == "model" and run.steps == 2
    assert run.changed == ("src/sessions.py",)
    assert run.summary == "carried the fragment"
    assert "return resp2" in (ws.root / "src" / "sessions.py").read_text(encoding="utf-8")


async def test_the_plan_is_put_in_front_of_the_model(ws: Workspace) -> None:
    llm = ScriptedLLM(finish())
    await Solver(llm, ws).run(title="redirect bug", body="report", plan=PLAN)

    briefing = str(llm.seen[0][1]["content"])
    assert "the fragment is dropped" in briefing
    assert "src/sessions.py:1-2" in briefing
    assert "carry the fragment through" in briefing


async def test_finish_is_refused_while_a_file_does_not_parse(ws: Workspace) -> None:
    broken = call(
        "replace",
        {"path": "src/sessions.py", "old": "    return resp", "new": "    return resp("},
    )
    repair = call(
        "replace",
        {"path": "src/sessions.py", "old": "    return resp(", "new": "    return resp"},
        id="c2",
    )
    llm = ScriptedLLM(broken, finish(), repair, finish("fixed"))
    seen: list[str] = []

    async def record(kind: str, payload: dict[str, Any]) -> None:
        seen.append(kind)

    run = await Solver(llm, ws, on_step=record).run(title="t", body="b", plan=PLAN)

    assert run.ok and run.steps == 4
    assert "agent.syntax_rejected" in seen
    rejection = [m for m in llm.seen[2] if m.get("role") == "tool"][-1]["content"]
    assert "no longer parse" in rejection and "src/sessions.py:2" in rejection


async def test_syntax_that_stays_broken_ends_the_run(ws: Workspace) -> None:
    broken = call(
        "replace", {"path": "src/sessions.py", "old": "    return resp", "new": "    return ("}
    )
    llm = ScriptedLLM(broken, finish(), finish(), finish())
    run = await Solver(llm, ws, max_repairs=2).run(title="t", body="b", plan=PLAN)

    assert not run.ok and run.stopped_by == "broken_syntax"
    assert run.problems and "src/sessions.py" in run.problems[0]


async def test_a_non_python_file_is_not_parsed(ws: Workspace) -> None:
    (ws.root / "README.md").write_text("# hi\n", encoding="utf-8")
    llm = ScriptedLLM(
        call("replace", {"path": "README.md", "old": "# hi", "new": "# hi )))("}), finish()
    )
    run = await Solver(llm, ws).run(title="t", body="b", plan=PLAN)
    assert run.ok and run.changed == ("README.md",)


async def test_read_tools_are_available_alongside_edits(ws: Workspace) -> None:
    llm = ScriptedLLM(call("grep", {"pattern": "def resolve"}), finish())
    run = await Solver(llm, ws).run(title="t", body="b", plan=PLAN)

    assert llm.offered[0].count("grep") == 1
    assert {"replace", "create_file", "rewrite_file", "finish"} <= set(llm.offered[0])
    result = [m for m in llm.seen[1] if m.get("role") == "tool"][-1]["content"]
    assert "src/sessions.py:1:" in result
    assert run.changed == ()  # grep changes nothing


async def test_the_step_limit_takes_the_tools_away(ws: Workspace) -> None:
    llm = ScriptedLLM(call("grep", {"pattern": "x"}), call("grep", {"pattern": "y"}), finish())
    run = await Solver(llm, ws, max_steps=3).run(title="t", body="b", plan=PLAN)

    assert run.forced is True and run.steps == 3
    assert llm.offered[-1] == ["finish"]


async def test_a_nearly_spent_budget_forces_the_ending(ws: Workspace) -> None:
    budget = Budget(limit_usd=1.0)
    budget.charge(Usage(cost_usd=0.92))
    llm = ScriptedLLM(finish())
    run = await Solver(llm, ws, budget=budget).run(title="t", body="b", plan=PLAN)

    assert run.forced is True and llm.offered[0] == ["finish"]


async def test_a_run_that_never_finishes_reports_the_step_limit(ws: Workspace) -> None:
    llm = ScriptedLLM(call("grep", {"pattern": "x"}), call("grep", {"pattern": "y"}))
    run = await Solver(llm, ws, max_steps=2).run(title="t", body="b", plan=PLAN)
    assert not run.ok and run.stopped_by == "step_limit"


async def test_edit_arguments_are_shortened_in_the_event_log(ws: Workspace) -> None:
    """A whole-file rewrite must not be replayed verbatim into every event."""
    long_body = "x = 1\n" * 500
    llm = ScriptedLLM(
        call("create_file", {"path": "big.py", "content": long_body}),
        finish(),
    )
    logged: list[dict[str, Any]] = []

    async def record(kind: str, payload: dict[str, Any]) -> None:
        if kind == "agent.tool_call":
            logged.append(payload)

    await Solver(llm, ws, on_step=record).run(title="t", body="b", plan=PLAN)
    assert len(logged[0]["arguments"]["content"]) < 250


def test_check_syntax_only_looks_at_files_that_exist(ws: Workspace) -> None:
    assert check_syntax(ws, {"src/sessions.py", "deleted.py", "notes.txt"}) == []
