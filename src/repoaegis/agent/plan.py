"""What the model hands back, and how the server checks it.

The plan is structured rather than prose for three reasons: the approval panel
can render locations as a list a human clicks through, the next round can read
``locations`` directly instead of re-parsing paragraphs, and -- the reason that
matters most -- structure is checkable. A cited file that does not exist, or a
line past the end of the file, is a hallucination we can catch with the file
system instead of trusting the model's word for it.

That check is a cheap substitute for making the model quote the source lines
back: the disk already knows the truth, and it cannot mis-transcribe.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from repoaegis.agent.tools import ToolError, Workspace

Confidence = Literal["high", "medium", "low"]


class Location(BaseModel):
    file: str = Field(description="Path relative to the repository root.")
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    why: str = Field(max_length=500, description="What this span has to do with the issue.")


class Plan(BaseModel):
    diagnosis: str = Field(max_length=2000)
    locations: list[Location] = Field(default_factory=list)
    approach: str = Field(max_length=2000)
    verification: str = Field(max_length=2000, default="")
    confidence: Confidence = "medium"


SUBMIT_PLAN = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": (
            "Hand in the finished plan. Call this once you can name the code that has to "
            "change. Every location must be a real file and a real line range you have "
            "actually read."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "diagnosis": {
                    "type": "string",
                    "description": "The root cause, in one or two sentences.",
                },
                "locations": {
                    "type": "array",
                    "description": "The spans that need to change, most important first.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string"},
                            "line_start": {"type": "integer"},
                            "line_end": {"type": "integer"},
                            "why": {"type": "string"},
                        },
                        "required": ["file", "line_start", "line_end", "why"],
                    },
                },
                "approach": {"type": "string", "description": "How you intend to change it."},
                "verification": {
                    "type": "string",
                    "description": "Which tests or commands would show the fix works.",
                },
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["diagnosis", "locations", "approach"],
        },
    },
}


def check_locations(plan: Plan, ws: Workspace) -> list[str]:
    """Return the problems with a plan's citations. Empty means it checks out."""
    problems: list[str] = []
    for index, location in enumerate(plan.locations):
        label = f"locations[{index}] ({location.file})"
        try:
            target = ws.resolve(location.file)
        except ToolError as exc:
            problems.append(f"{label}: {exc}")
            continue
        if not target.is_file():
            problems.append(f"{label}: no such file in the repository")
            continue
        if location.line_end < location.line_start:
            problems.append(f"{label}: line_end {location.line_end} precedes line_start")
            continue
        total = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
        if location.line_start > total:
            problems.append(f"{label}: line_start {location.line_start} but the file has {total}")
        elif location.line_end > total:
            problems.append(f"{label}: line_end {location.line_end} but the file has {total}")
    return problems
