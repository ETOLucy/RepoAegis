"""The planning loop: one model, three read-only tools, four ways to stop.

The shape is the industry's single loop rather than a staged pipeline -- the
model searches, reads, revises its guess, and hands in a plan when it is ready.
Staged localisation (Agentless) cannot go back once it has picked the wrong
file; a loop can.

Four gates end a run, and every one of them still produces a plan:

* the model calls ``submit_plan`` -- the normal ending;
* the step budget runs out -- the tools are taken away and the model is told to
  hand in what it has, because a lost run is most useful when a human can see
  where it got lost;
* the money budget nears its limit -- the same forced ending, triggered early
  enough that one final call is still affordable;
* the plan fails validation twice -- the citations are wrong and the model has
  already had its correction.

Nothing here touches the database. Progress is reported through ``on_step`` so
the server can turn it into events without the loop knowing what an event is.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import ValidationError

from repoaegis.agent.llm import LLM, Budget, Completion, Message, ToolCall, Usage
from repoaegis.agent.plan import SUBMIT_PLAN, Plan, check_locations
from repoaegis.agent.tools import SCHEMAS, Workspace, run_tool

log = structlog.get_logger(__name__)

StepReporter = Callable[[str, dict[str, Any]], Awaitable[None]]

SYSTEM = """\
You are a maintenance engineer triaging one issue in a repository you can read \
but not modify.

Work like an engineer with a terminal: search for the symbols and messages the \
issue mentions, read the spans you find, and follow the call path until you can \
name the code that is wrong. Prefer one precise query over several broad ones; \
the tools truncate their output and will tell you when they do.

Call submit_plan as soon as you can point at the code. Do not pad the \
investigation: many issues name the file themselves, and confirming that in two \
or three steps is a good run, not a lazy one. Never cite a file or a line range \
you have not read."""

STEP_LIMIT_NOTICE = """\
You have used the whole step budget. Call submit_plan now with what you have, \
and set confidence to low if you are unsure. A partial plan that says where you \
got to is worth more than nothing."""


@dataclass(frozen=True, slots=True)
class PlanRun:
    """The outcome of one planning run, including how it ended."""

    plan: Plan | None
    steps: int
    usage: Usage
    stopped_by: str
    problems: tuple[str, ...] = ()
    transcript: tuple[Message, ...] = field(default=())
    forced: bool = False
    """The plan was handed in under duress (out of steps or money), not because
    the model decided it was done. Evaluation must keep these apart: a forced
    plan is a run that did not converge, however well it reads."""

    @property
    def ok(self) -> bool:
        return self.plan is not None


class Planner:
    def __init__(
        self,
        llm: LLM,
        ws: Workspace,
        *,
        max_steps: int = 20,
        max_repairs: int = 1,
        budget: Budget | None = None,
        reserve_fraction: float = 0.15,
        deadline_seconds: float = 600.0,
        on_step: StepReporter | None = None,
    ) -> None:
        self._llm = llm
        self._ws = ws
        self._max_steps = max_steps
        self._max_repairs = max_repairs
        self._budget = budget
        self._reserve = reserve_fraction
        self._deadline = deadline_seconds
        self._on_step = on_step

    async def run(self, *, title: str, body: str) -> PlanRun:
        messages: list[Message] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Issue: {title}\n\n{body}".strip()},
        ]
        usage = Usage()
        repairs = 0
        forced = False
        started = time.monotonic()

        for step in range(1, self._max_steps + 1):
            last_chance = forced or step == self._max_steps or self._out_of_room(started)
            if last_chance and not forced:
                forced = True
                messages.append({"role": "user", "content": STEP_LIMIT_NOTICE})

            tools = [SUBMIT_PLAN] if forced else [*SCHEMAS, SUBMIT_PLAN]
            completion = await self._llm.complete(messages, tools=tools)
            usage = usage + completion.usage
            messages.append(self._assistant_message(completion))

            if not completion.tool_calls:
                # The model answered in prose. Say so plainly and let it retry.
                await self._report("agent.thought", {"step": step, "text": completion.text[:500]})
                messages.append(
                    {
                        "role": "user",
                        "content": "Use a tool call. Investigate further, or call submit_plan.",
                    }
                )
                continue

            for call in completion.tool_calls:
                if call.name != "submit_plan":
                    result = run_tool(self._ws, call.name, call.arguments)
                    await self._report(
                        "agent.tool_call",
                        {
                            "step": step,
                            "tool": call.name,
                            "arguments": call.arguments,
                            "result_preview": result[:300],
                        },
                    )
                    messages.append(self._tool_message(call, result))
                    continue

                plan, problems = self._read_plan(call)
                if problems and repairs < self._max_repairs:
                    repairs += 1
                    await self._report(
                        "agent.plan_rejected", {"step": step, "problems": list(problems)}
                    )
                    messages.append(self._tool_message(call, _repair_prompt(problems)))
                    continue
                if problems:
                    return PlanRun(
                        None, step, usage, "invalid_plan", tuple(problems), tuple(messages), forced
                    )
                assert plan is not None
                await self._report(
                    "agent.plan_ready",
                    {"step": step, "locations": len(plan.locations), "confidence": plan.confidence},
                )
                return PlanRun(plan, step, usage, "model", (), tuple(messages), forced)

        return PlanRun(None, self._max_steps, usage, "step_limit", (), tuple(messages), forced)

    def _out_of_room(self, started: float) -> bool:
        """Money or wall clock nearly gone -- either way, wrap up while we can.

        The clock matters as much as the money: one slow provider call can hang
        for minutes, and a run with no deadline hangs with it.
        """
        if time.monotonic() - started >= self._deadline * (1 - self._reserve):
            return True
        budget = self._budget
        if budget is None or not budget.limit:
            return False
        return budget.remaining <= budget.limit * self._reserve

    def _read_plan(self, call: ToolCall) -> tuple[Plan | None, tuple[str, ...]]:
        try:
            plan = Plan.model_validate(call.arguments)
        except ValidationError as exc:
            return None, tuple(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
        problems = check_locations(plan, self._ws)
        return (plan, tuple(problems)) if problems else (plan, ())

    @staticmethod
    def _assistant_message(completion: Completion) -> Message:
        if completion.raw_message:
            return dict(completion.raw_message)
        return {"role": "assistant", "content": completion.text}

    @staticmethod
    def _tool_message(call: ToolCall, content: str) -> Message:
        return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}

    async def _report(self, kind: str, payload: dict[str, Any]) -> None:
        log.info(kind, **payload)
        if self._on_step is not None:
            await self._on_step(kind, payload)


def _repair_prompt(problems: Sequence[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "The plan was not accepted; its citations do not match the repository:\n"
        f"{listed}\n"
        "Read the files again and submit corrected locations."
    )
