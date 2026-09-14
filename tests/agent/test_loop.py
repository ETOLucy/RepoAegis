"""The loop's contract is about endings: every run must produce either a
checked plan or a stated reason, and it must never cite code that isn't there."""

from pathlib import Path
from typing import Any

import pytest

from repoaegis.agent.llm import Budget, Completion, Message, ToolCall, Usage
from repoaegis.agent.loop import Planner
from repoaegis.agent.tools import Workspace

GOOD_PLAN = {
    "diagnosis": "resolve_redirects drops the fragment",
    "locations": [{"file": "src/sessions.py", "line_start": 1, "line_end": 2, "why": "the bug"}],
    "approach": "carry the fragment through",
    "verification": "pytest tests/test_redirects.py",
    "confidence": "high",
}


class ScriptedLLM:
    """Replays a fixed list of completions and records what it was offered."""

    def __init__(self, *completions: Completion) -> None:
        self.script = list(completions)
        self.offered: list[list[str]] = []
        self.seen: list[list[Message]] = []

    async def complete(self, messages: Any, *, tools: Any = None) -> Completion:
        self.seen.append(list(messages))
        self.offered.append([t["function"]["name"] for t in (tools or [])])
        return self.script.pop(0) if self.script else _submit(GOOD_PLAN)


def _call(name: str, arguments: dict[str, Any], id: str = "c1") -> Completion:
    return Completion(tool_calls=(ToolCall(id=id, name=name, arguments=arguments),))


def _submit(plan: dict[str, Any]) -> Completion:
    return _call("submit_plan", plan, id="submit")


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "sessions.py").write_text(
        "def resolve_redirects(resp):\n    return resp\n", encoding="utf-8"
    )
    return Workspace(root=tmp_path)


async def test_a_normal_run_greps_then_submits(ws: Workspace) -> None:
    llm = ScriptedLLM(_call("grep", {"pattern": "resolve_redirects"}), _submit(GOOD_PLAN))
    run = await Planner(llm, ws).run(title="redirect bug", body="fragment is lost")

    assert run.ok and run.stopped_by == "model" and run.steps == 2
    assert run.forced is False  # it decided it was done
    assert run.plan is not None and run.plan.locations[0].file == "src/sessions.py"
    # The tool result went back as a tool message the model can read.
    first_tool_message = next(m for m in llm.seen[-1] if m.get("role") == "tool")
    assert "src/sessions.py:1:" in first_tool_message["content"]


async def test_prose_instead_of_a_tool_call_is_nudged(ws: Workspace) -> None:
    llm = ScriptedLLM(Completion(text="I think it is in sessions.py"), _submit(GOOD_PLAN))
    run = await Planner(llm, ws).run(title="t", body="b")

    assert run.ok and run.steps == 2
    nudges = [m for m in llm.seen[-1] if m.get("role") == "user" and "Use a tool call" in str(m)]
    assert len(nudges) == 1


async def test_a_bad_citation_is_sent_back_once_and_can_be_fixed(ws: Workspace) -> None:
    bad = {
        **GOOD_PLAN,
        "locations": [{**GOOD_PLAN["locations"][0], "line_start": 999, "line_end": 1000}],  # type: ignore[index]
    }
    llm = ScriptedLLM(_submit(bad), _submit(GOOD_PLAN))
    seen: list[tuple[str, dict[str, Any]]] = []

    async def record(kind: str, payload: dict[str, Any]) -> None:
        seen.append((kind, payload))

    run = await Planner(llm, ws, on_step=record).run(title="t", body="b")

    assert run.ok and run.steps == 2
    assert [k for k, _ in seen] == ["agent.plan_rejected", "agent.plan_ready"]
    repair = next(m for m in llm.seen[-1] if m.get("role") == "tool")["content"]
    assert "line_start 999 but the file has 2" in repair


async def test_a_file_that_does_not_exist_is_caught(ws: Workspace) -> None:
    ghost = {"file": "src/ghost.py", "line_start": 1, "line_end": 2, "why": "invented"}
    invented = {**GOOD_PLAN, "locations": [ghost]}
    llm = ScriptedLLM(_submit(invented), _submit(invented))
    run = await Planner(llm, ws).run(title="t", body="b")

    assert not run.ok and run.stopped_by == "invalid_plan"
    assert "no such file" in run.problems[0]


async def test_a_plan_missing_required_fields_is_rejected(ws: Workspace) -> None:
    llm = ScriptedLLM(_submit({"diagnosis": "x"}), _submit(GOOD_PLAN))
    run = await Planner(llm, ws).run(title="t", body="b")
    assert run.ok and run.steps == 2  # the schema error was repaired like any other


async def test_the_step_limit_takes_the_tools_away_and_demands_a_plan(ws: Workspace) -> None:
    wander = [_call("list_files", {}, id=f"c{i}") for i in range(2)]
    llm = ScriptedLLM(*wander, _submit(GOOD_PLAN))
    run = await Planner(llm, ws, max_steps=3).run(title="t", body="b")

    assert run.ok and run.steps == 3
    assert run.forced is True  # handed in under duress, not converged
    assert llm.offered[-1] == ["submit_plan"]  # only one way out remained
    assert any("step budget" in str(m.get("content")) for m in llm.seen[-1])


async def test_a_model_that_never_submits_ends_as_step_limit(ws: Workspace) -> None:
    llm = ScriptedLLM(_call("list_files", {}, id="a"), _call("list_files", {}, id="b"))
    run = await Planner(llm, ws, max_steps=2).run(title="t", body="b")

    assert not run.ok and run.stopped_by == "step_limit" and run.steps == 2


async def test_a_nearly_spent_budget_forces_the_ending_early(ws: Workspace) -> None:
    budget = Budget(limit_usd=1.0)
    budget.charge(Usage(cost_usd=0.90))  # 10% left, below the 15% reserve
    llm = ScriptedLLM(_submit(GOOD_PLAN))
    run = await Planner(llm, ws, max_steps=20, budget=budget).run(title="t", body="b")

    assert run.ok and run.steps == 1 and run.forced is True
    assert llm.offered[0] == ["submit_plan"]


async def test_usage_is_summed_across_steps(ws: Workspace) -> None:
    priced = Completion(
        tool_calls=(ToolCall(id="c", name="list_files", arguments={}),),
        usage=Usage(cache_miss_tokens=100, completion_tokens=10, cost_usd=0.001),
    )
    submit = Completion(
        tool_calls=(ToolCall(id="s", name="submit_plan", arguments=GOOD_PLAN),),
        usage=Usage(cache_miss_tokens=200, completion_tokens=20, cost_usd=0.002),
    )
    run = await Planner(ScriptedLLM(priced, submit), ws).run(title="t", body="b")

    assert run.usage.cost_usd == pytest.approx(0.003)
    assert run.usage.total_tokens == 330
