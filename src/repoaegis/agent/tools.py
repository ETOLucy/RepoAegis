"""Read-only tools the model may call, and their schemas.

A tool is a plain function ``(workspace, **params) -> str``; the schema beside
it is what the model sees. No class hierarchy, no executor -- the router in the
loop looks the name up in ``TOOLS`` and calls it.

Two invariants hold for every tool, and both exist because the caller is a
language model rather than a program:

*Everything is clamped.* A model that greps the whole tree would otherwise pull
tens of thousands of lines into the context window, and the run dies of its own
output. Each tool caps what it returns and says so in the text, which also
teaches the model to narrow its next query.

*Nothing escapes the workspace.* Paths are resolved and checked against the
root, so a traversal out of the repository is refused rather than read. These
tools cannot execute anything, so this is the whole attack surface -- and it is
why this round needs no sandbox.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

MAX_MATCHES = 60
MAX_LINES = 400
MAX_CHARS = 20_000
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"})
BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".whl", ".so", ".pyc"}
)


class ToolError(Exception):
    """A tool refused. The message goes back to the model as the result."""


@dataclass(frozen=True, slots=True)
class Workspace:
    """A directory the tools may read, and nothing above it."""

    root: Path

    def resolve(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        root = self.root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ToolError(f"path escapes the workspace: {relative}")
        return candidate

    def walk(self) -> Iterator[Path]:
        # Prune while descending. Filtering afterwards would still walk into
        # .venv and node_modules, which on a real checkout is tens of thousands
        # of files and turns every grep into a multi-second call.
        for folder, subdirs, files in os.walk(self.root):
            subdirs[:] = [d for d in subdirs if d not in SKIP_DIRS]
            base = Path(folder)
            for name in files:
                if Path(name).suffix.lower() in BINARY_SUFFIXES:
                    continue
                yield base / name

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def _clamp(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n… [truncated]"


def _matches(name: str, glob: str) -> bool:
    return fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(Path(name).name, glob)


def list_files(ws: Workspace, *, glob: str = "*", limit: int = 200) -> str:
    """Paths matching a glob. The cheapest way to see a repository's shape."""
    limit = max(1, min(int(limit), 1000))
    names = sorted(ws.rel(p) for p in ws.walk())
    hits = [n for n in names if _matches(n, glob)]
    if not hits:
        return f"no files match {glob!r} (of {len(names)} files in the workspace)"
    head = hits[:limit]
    tail = "" if len(hits) <= limit else f"\n… {len(hits) - limit} more; narrow the glob"
    return _clamp("\n".join(head) + tail)


def grep(ws: Workspace, *, pattern: str, glob: str = "*.py", limit: int = MAX_MATCHES) -> str:
    """Regex search. Returns path:line:text, the format every engineer reads."""
    limit = max(1, min(int(limit), MAX_MATCHES))
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"bad regular expression {pattern!r}: {exc}") from None

    out: list[str] = []
    scanned = 0
    for path in ws.walk():
        name = ws.rel(path)
        if not _matches(name, glob):
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                out.append(f"{name}:{number}:{line.rstrip()[:300]}")
                if len(out) >= limit:
                    note = f"\n… stopped at {limit} matches; narrow the pattern"
                    return _clamp("\n".join(out) + note)
    if not out:
        return f"no match for {pattern!r} in {scanned} files matching {glob!r}"
    return _clamp("\n".join(out))


def read_file(ws: Workspace, *, path: str, start: int = 1, lines: int = MAX_LINES) -> str:
    """A numbered slice of one file, so the model can cite line numbers back."""
    target = ws.resolve(path)
    if not target.is_file():
        raise ToolError(f"not a file: {path}")
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ToolError(f"cannot read {path}: {exc}") from None

    all_lines = text.splitlines()
    start = max(1, int(start))
    count = max(1, min(int(lines), MAX_LINES))
    chunk = all_lines[start - 1 : start - 1 + count]
    if not chunk:
        return f"{path} has {len(all_lines)} lines; {start} is past the end"
    body = "\n".join(f"{start + i:>6}  {line}" for i, line in enumerate(chunk))
    end = start + len(chunk) - 1
    return _clamp(f"{path} lines {start}-{end} of {len(all_lines)}\n{body}")


Tool = Callable[..., str]

TOOLS: dict[str, Tool] = {"list_files": list_files, "grep": grep, "read_file": read_file}

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List file paths in the repository, optionally filtered by a glob.",
            "parameters": {
                "type": "object",
                "properties": {
                    "glob": {
                        "type": "string",
                        "description": "Glob such as *.py or src/**/adapters.py. Default *.",
                    },
                    "limit": {"type": "integer", "description": "Max paths, default 200."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search the repository with a Python regular expression. "
                "Returns path:line:text. Prefer a specific symbol name over a broad word."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regular expression."},
                    "glob": {"type": "string", "description": "File filter, default *.py."},
                    "limit": {
                        "type": "integer",
                        "description": f"Max matches, at most {MAX_MATCHES}.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a numbered slice of one file so you can cite exact line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repo root."},
                    "start": {"type": "integer", "description": "First line, 1-based."},
                    "lines": {
                        "type": "integer",
                        "description": f"How many lines, at most {MAX_LINES}.",
                    },
                },
                "required": ["path"],
            },
        },
    },
]


def run_tool(ws: Workspace, name: str, arguments: dict[str, Any]) -> str:
    """Dispatch by name. Every failure becomes text the model can react to."""
    tool = TOOLS.get(name)
    if tool is None:
        return f"no such tool {name!r}; available: {', '.join(sorted(TOOLS))}"
    try:
        return tool(ws, **arguments)
    except ToolError as exc:
        return f"error: {exc}"
    except TypeError as exc:
        return f"error: bad arguments for {name}: {exc}"
    except Exception as exc:
        # A broken tool must degrade into a message, never end the run.
        log.warning("tool_failed", tool=name, error=str(exc))
        return f"error: {name} failed: {exc}"
