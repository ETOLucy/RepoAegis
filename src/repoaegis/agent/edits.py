"""The write tools, and the rule that decides which one the model may use.

Editing defaults to ``replace``: the model quotes the exact text it wants gone
and the exact text that replaces it. No line numbers, because line numbers are
free for a diff algorithm walking a grid and expensive for a model that has to
count. The controlled comparison behind that choice (Diff-XYZ, 2510.12487) puts
exact old/new at 0.96-0.97 applied correctly against 0.90 for unified diff.

Whole-file rewriting exists, but the model does not get to choose it. Output
tokens cost four times input, so rewriting a 500-line file costs roughly ten
times the entire localisation run that found the bug. It is offered only after
``replace`` has demonstrably failed twice on that file -- an observed fact,
not a judgement call the model could get wrong.

Creating a file is the one case where there is no old text to quote, so it has
its own tool and is whole-content by definition.

Line endings are normalised for matching and restored on write. A checkout on
Windows can hold CRLF while the model quotes LF, and an edit that fails for
that reason would be indistinguishable from a model that misremembered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from repoaegis.agent.tools import ToolError, Workspace

log = structlog.get_logger(__name__)

MAX_CONTENT = 200_000
FAILURES_BEFORE_REWRITE = 2


def _split_newline(raw: str) -> tuple[str, str]:
    """Return the text with LF endings, plus the ending the file actually used."""
    if "\r\n" in raw:
        return raw.replace("\r\n", "\n"), "\r\n"
    return raw, "\n"


def _read(ws: Workspace, path: str) -> tuple[str, str]:
    target = ws.resolve(path)
    if not target.is_file():
        raise ToolError(f"not a file: {path}")
    # newline="" disables universal-newline translation, which would otherwise
    # hand us LF on Windows and hide the fact that the file holds CRLF.
    with target.open(encoding="utf-8", errors="replace", newline="") as handle:
        return _split_newline(handle.read())


def _write(ws: Workspace, path: str, text: str, newline: str) -> None:
    target = ws.resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = text.replace("\n", newline) if newline != "\n" else text
    target.write_text(body, encoding="utf-8", newline="")


@dataclass
class EditLog:
    """What has been changed, and which files keep refusing to match.

    The failure count is the whole escalation policy: two misses on one file and
    the system stops asking the model to quote it correctly.
    """

    changed: set[str] = field(default_factory=set)
    failures: dict[str, int] = field(default_factory=dict)

    def succeeded(self, path: str) -> None:
        self.changed.add(path)
        self.failures.pop(path, None)

    def failed(self, path: str) -> int:
        self.failures[path] = self.failures.get(path, 0) + 1
        return self.failures[path]

    def may_rewrite(self, path: str) -> bool:
        return self.failures.get(path, 0) >= FAILURES_BEFORE_REWRITE


def replace(ws: Workspace, log_: EditLog, *, path: str, old: str, new: str) -> str:
    """Swap one exact, unique span of text. The default way to edit."""
    if not old:
        raise ToolError("old must not be empty; use create_file for a new file")
    text, newline = _read(ws, path)
    old_lf = old.replace("\r\n", "\n")
    occurrences = text.count(old_lf)

    if occurrences == 0:
        attempt = log_.failed(path)
        hint = (
            "call rewrite_file with the whole file instead"
            if log_.may_rewrite(path)
            else "re-read the file and quote it exactly, including indentation"
        )
        return f"error: no match in {path} (attempt {attempt}); {hint}"
    if occurrences > 1:
        attempt = log_.failed(path)
        return (
            f"error: {occurrences} matches in {path} (attempt {attempt}); "
            "include more surrounding lines so the span is unique"
        )

    _write(ws, path, text.replace(old_lf, new.replace("\r\n", "\n"), 1), newline)
    log_.succeeded(path)
    removed, added = old_lf.count("\n") + 1, new.count("\n") + 1
    return f"replaced 1 span in {path} ({removed} lines -> {added} lines)"


def rewrite_file(ws: Workspace, log_: EditLog, *, path: str, content: str) -> str:
    """Replace a file wholesale. Unlocked only once ``replace`` has failed twice."""
    if not log_.may_rewrite(path):
        return (
            f"error: rewrite_file is a fallback, not a first choice. Use replace on {path}; "
            f"it unlocks after {FAILURES_BEFORE_REWRITE} failed matches on the same file."
        )
    if len(content) > MAX_CONTENT:
        raise ToolError(f"content is {len(content)} characters; the limit is {MAX_CONTENT}")
    _, newline = _read(ws, path)
    _write(ws, path, content, newline)
    log_.succeeded(path)
    return f"rewrote {path} ({content.count(chr(10)) + 1} lines)"


def create_file(ws: Workspace, log_: EditLog, *, path: str, content: str) -> str:
    """A new file has no old text to quote, so it is whole-content by nature."""
    target = ws.resolve(path)
    if target.exists():
        return f"error: {path} already exists; use replace to change it"
    if len(content) > MAX_CONTENT:
        raise ToolError(f"content is {len(content)} characters; the limit is {MAX_CONTENT}")
    _write(ws, path, content, "\n")
    log_.succeeded(path)
    return f"created {path} ({content.count(chr(10)) + 1} lines)"


def delete_file(ws: Workspace, log_: EditLog, *, path: str) -> str:
    target = ws.resolve(path)
    if not target.is_file():
        return f"error: {path} is not a file"
    target.unlink()
    log_.succeeded(path)
    return f"deleted {path}"


EditTool = Callable[..., str]

EDIT_TOOLS: dict[str, EditTool] = {
    "replace": replace,
    "rewrite_file": rewrite_file,
    "create_file": create_file,
    "delete_file": delete_file,
}

EDIT_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "replace",
            "description": (
                "Replace one exact span of text in a file. Quote `old` verbatim, including "
                "indentation, and include enough surrounding lines that it appears exactly "
                "once. This is the normal way to edit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repo root."},
                    "old": {"type": "string", "description": "The exact text to remove."},
                    "new": {"type": "string", "description": "The exact text to put there."},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file with the given content. Fails if it already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rewrite_file",
            "description": (
                "Fallback only: send a file's entire new content. Refused until replace has "
                "failed twice on that same file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def run_edit(ws: Workspace, log_: EditLog, name: str, arguments: dict[str, Any]) -> str:
    """Dispatch an edit. Like the read tools, every failure returns as text."""
    tool = EDIT_TOOLS.get(name)
    if tool is None:
        return f"no such tool {name!r}; available: {', '.join(sorted(EDIT_TOOLS))}"
    try:
        return tool(ws, log_, **arguments)
    except ToolError as exc:
        return f"error: {exc}"
    except TypeError as exc:
        return f"error: bad arguments for {name}: {exc}"
    except Exception as exc:
        # A broken tool must degrade into a message, never end the run.
        log.warning("edit_failed", tool=name, error=str(exc))
        return f"error: {name} failed: {exc}"
