"""The editing loop: an approved plan in, a set of changed files out.

It is deliberately a separate loop from ``Planner`` rather than a
parameterisation of it. The two share a skeleton -- call the model, dispatch
tools, respect the step and money budgets -- but they answer different
questions and fail in different ways: one can cite a file that does not exist,
the other can leave a file that does not parse. An abstraction over both would
have to be parameterised by almost everything that differs between them, which
hides more than it saves. If a third loop appears, extract then.

Finishing is not the model's word alone. ``finish`` runs a syntax check over
every edited Python file, and a file that no longer parses sends the model back
to fix it. That check costs milliseconds and executes nothing, so it does not
breach this round's rule that no repository code is run.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from repoaegis.agent.edits import EDIT_SCHEMAS, EditLog, run_edit
from repoaegis.agent.llm import LLM, Budget, Completion, Message, ToolCall, Usage
from repoaegis.agent.plan import Plan
from repoaegis.agent.tools import SCHEMAS, Workspace, run_tool
from repoaegis.agent.verify import SyntaxProblem, check_syntax

log = structlog.get_logger(__name__)

StepReporter = Callable[[str, dict[str, Any]], Awaitable[None]]

SYSTEM = """\
You are implementing a fix that a reviewer has already approved. The plan below \
is the agreement; follow it rather than re-deciding the diagnosis.

Read before you write: quote `old` exactly as the file holds it, indentation \
included, and include enough surrounding lines that the span appears only once. \
Make the smallest change that implements the plan. Do not reformat untouched \
code, do not rename things the plan did not mention, and do not leave debugging \
output behind.

Call finish when the edits are complete. It checks that every file you touched \
still parses, so a syntax error comes back to you rather than reaching the \
reviewer."""

FINISH = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": (
            "Declare the implementation complete. Every edited Python file is parsed; "
            "if one no longer parses you will be asked to fix it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "One or two sentences on what you changed and why.",
                }
            },
            "required": ["summary"],
        },
    },
}

STEP_LIMIT_NOTICE = """\
You have used the whole step budget. Call finish now with what you have done so \
far, and say plainly in the summary what is still missing."""


@dataclass(frozen=True, slots=True)
class SolveRun:
    summary: str
    changed: tuple[str, ...]
    steps: int
    usage: Usage
    stopped_by: str
    problems: tuple[str, ...] = ()
    forced: bool = False
    transcript: tuple[Message, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.stopped_by == "model" and bool(self.changed)


class Solver:
    def __init__(
        self,
        llm: LLM,
        ws: Workspace,
        *,
        max_steps: int = 30,
        max_repairs: int = 2,
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

    async def run(self, *, title: str, body: str, plan: Plan) -> SolveRun:
        edits = EditLog()
        messages: list[Message] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _briefing(title, body, plan)},
        ]
        usage = Usage()
        repairs = 0
        forced = False
        started = time.monotonic()

        for step in range(1, self._max_steps + 1):
            if not forced and (step == self._max_steps or self._out_of_room(started)):
                forced = True
                messages.append({"role": "user", "content": STEP_LIMIT_NOTICE})

            tools = [FINISH] if forced else [*SCHEMAS, *EDIT_SCHEMAS, FINISH]
            completion = await self._llm.complete(messages, tools=tools)
            usage = usage + completion.usage
            messages.append(_assistant(completion))

            if not completion.tool_calls:
                await self._report("agent.thought", {"step": step, "text": completion.text[:500]})
                messages.append(
                    {"role": "user", "content": "Use a tool call: edit the code, or call finish."}
                )
                continue

            for call in completion.tool_calls:
                if call.name != "finish":
                    result = self._dispatch(edits, call)
                    await self._report(
                        "agent.tool_call",
                        {
                            "step": step,
                            "tool": call.name,
                            "arguments": _short(call.arguments),
                            "result_preview": result[:300],
                        },
                    )
                    messages.append(_tool_message(call, result))
                    continue

                problems = check_syntax(self._ws, edits.changed)
                if problems and repairs < self._max_repairs:
                    repairs += 1
                    await self._report(
                        "agent.syntax_rejected",
                        {"step": step, "problems": [str(p) for p in problems]},
                    )
                    messages.append(_tool_message(call, _repair_prompt(problems)))
                    continue

                summary = str(call.arguments.get("summary", ""))
                changed = tuple(sorted(edits.changed))
                if problems:
                    return SolveRun(
                        summary,
                        changed,
                        step,
                        usage,
                        "broken_syntax",
                        tuple(str(p) for p in problems),
                        forced,
                        tuple(messages),
                    )
                await self._report("agent.edits_ready", {"step": step, "changed": list(changed)})
                return SolveRun(summary, changed, step, usage, "model", (), forced, tuple(messages))

        return SolveRun(
            "",
            tuple(sorted(edits.changed)),
            self._max_steps,
            usage,
            "step_limit",
            (),
            forced,
            tuple(messages),
        )

    def _dispatch(self, edits: EditLog, call: ToolCall) -> str:
        if call.name in {"list_files", "grep", "read_file"}:
            return run_tool(self._ws, call.name, call.arguments)
        return run_edit(self._ws, edits, call.name, call.arguments)

    def _out_of_room(self, started: float) -> bool:
        """Money or wall clock nearly gone -- either way, wrap up while we can."""
        if time.monotonic() - started >= self._deadline * (1 - self._reserve):
            return True
        budget = self._budget
        if budget is None or not budget.limit:
            return False
        return budget.remaining <= budget.limit * self._reserve

    async def _report(self, kind: str, payload: dict[str, Any]) -> None:
        log.info(kind, **payload)
        if self._on_step is not None:
            await self._on_step(kind, payload)


def _briefing(title: str, body: str, plan: Plan) -> str:
    cited = "\n".join(
        f"- {loc.file}:{loc.line_start}-{loc.line_end} — {loc.why}" for loc in plan.locations
    )
    return (
        f"Issue: {title}\n\n{body}\n\n"
        f"--- approved plan ---\n"
        f"Diagnosis: {plan.diagnosis}\n"
        f"Locations:\n{cited or '- (none given)'}\n"
        f"Approach: {plan.approach}\n"
        f"Verification: {plan.verification}"
    ).strip()


def _assistant(completion: Completion) -> Message:
    if completion.raw_message:
        return dict(completion.raw_message)
    return {"role": "assistant", "content": completion.text}


def _tool_message(call: ToolCall, content: str) -> Message:
    return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}


def _short(arguments: dict[str, Any]) -> dict[str, Any]:
    """Edit arguments carry whole file bodies; the event log wants a gist."""
    return {
        key: (value[:200] + "…" if isinstance(value, str) and len(value) > 200 else value)
        for key, value in arguments.items()
    }


def _repair_prompt(problems: list[SyntaxProblem]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (
        "finish was refused: these files no longer parse.\n"
        f"{listed}\n"
        "Read each one around the reported line and repair it, then call finish again."
    )
