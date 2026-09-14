"""Cheap checks that do not run the repository's code.

Parsing a file is not executing it: ``compile`` builds a syntax tree and stops,
so a malicious ``conftest.py`` in a cloned repository is just text here. That
distinction is what lets this round stay sandbox-free while still catching the
most common way an edit goes wrong -- a broken indent, an unbalanced bracket,
half a function left behind.

It catches nothing about whether the fix is correct. Only running the tests can
say that, and running tests means executing untrusted code, which is the next
round's problem.
"""

from __future__ import annotations

from dataclasses import dataclass

from repoaegis.agent.tools import Workspace


@dataclass(frozen=True, slots=True)
class SyntaxProblem:
    file: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.message}"


def check_syntax(ws: Workspace, paths: set[str]) -> list[SyntaxProblem]:
    """Parse every edited Python file. Returns what failed, in path order."""
    problems: list[SyntaxProblem] = []
    for path in sorted(paths):
        if not path.endswith(".py"):
            continue
        target = ws.root / path
        if not target.is_file():
            continue  # deleted on purpose
        source = target.read_text(encoding="utf-8", errors="replace")
        try:
            compile(source, path, "exec")
        except SyntaxError as exc:
            problems.append(SyntaxProblem(path, exc.lineno or 0, exc.msg))
        except ValueError as exc:
            # e.g. a source containing null bytes; still a broken edit.
            problems.append(SyntaxProblem(path, 0, str(exc)))
    return problems
