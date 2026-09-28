"""One round's record: what the solver tried, and what the tests said about it.

The solve/verify loop is deliberately not one long conversation. Each round
starts a fresh session, and what carries over is an ``Attempt`` -- the
hypothesis the solver acted on, the files it touched, and the test report that
came back -- rather than the previous round's transcript. Two reasons:

* the transcript is almost all observations (grep output, file contents) that
  were consumed the moment the model chose its next action. Dragging them into
  the next round costs tokens and buys nothing; and
* the *hypothesis* is the one thing the transcript does not state explicitly.
  A model that can see "round 1 assumed the body was None; the test still
  failed" does not need to rediscover that, and a human reading the console
  can follow the reasoning without replaying the run.

``TestReport`` is the shape a test run is delivered in. A pytest log for a
mid-sized suite is tens of thousands of lines, of which the model needs the
counts and, for each failure, where and why. The full log is written to a
file whose path the report carries, so the detail is one ``read_file`` away
instead of in every prompt. The report is parsed from pytest's own JUnit XML
(``--junitxml``) rather than from its terminal output, because the XML is a
format with a schema and the terminal output is prose with ellipses.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from repoaegis.agent.plan import Plan
from repoaegis.agent.tools import Workspace

MAX_FAILURES = 10
MAX_EXCERPT_LINES = 8

# The last "path:line: ExceptionType" line of a pytest traceback names the
# frame the failure surfaced in, which is where the model should look first.
_FRAME = re.compile(r"^(?P<file>[^\s:]+\.py):(?P<line>\d+): (?P<kind>\w+)", re.MULTILINE)


class Failure(BaseModel):
    test: str = Field(description="pytest node id, e.g. tests/test_x.py::test_y")
    file: str = ""
    line: int = 0
    kind: str = Field(default="", description="Exception class, e.g. AssertionError")
    message: str = Field(default="", max_length=500)
    excerpt: str = Field(default="", max_length=2000, description="The marked traceback lines")


class TestReport(BaseModel):
    __test__ = False  # the name is the domain term; it is not a pytest test class

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    failures: list[Failure] = Field(default_factory=list)
    omitted: int = Field(default=0, description="Failures beyond MAX_FAILURES, counted not listed")
    preexisting: int = Field(
        default=0, description="Tests that fail without the change too; not counted against it"
    )
    log_path: str = Field(default="", description="Where the full output was written")

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.errors == 0

    def render(self) -> str:
        """The report as the model reads it: counts first, then one failure per block."""
        head = (
            f"{self.passed} passed, {self.failed} failed, {self.errors} errors, "
            f"{self.skipped} skipped ({self.duration_seconds:.1f}s)"
        )
        lines = [head]
        for f in self.failures:
            where = f"{f.file}:{f.line}" if f.file else "?"
            lines.append(f"  FAIL {f.test}  ({where}, {f.kind or 'failure'})")
            if f.message:
                lines.append(f"       {f.message.splitlines()[0]}")
        if self.omitted:
            lines.append(f"  … and {self.omitted} more failures; see the full log")
        if self.preexisting:
            lines.append(
                f"  {self.preexisting} tests fail on the unchanged code as well and are not counted"
            )
        if self.log_path:
            lines.append(f"  full log: {self.log_path}")
        return "\n".join(lines)


class Attempt(BaseModel):
    """What one round handed to the next. Persisted as a ``round.finished`` event."""

    round: int = Field(ge=1)
    hypothesis: str = Field(default="", max_length=1000)
    changed_files: list[str] = Field(default_factory=list)
    report: TestReport | None = None

    def render(self) -> str:
        parts = [f"Round {self.round} — hypothesis: {self.hypothesis or '(none stated)'}"]
        parts.append(f"  changed: {', '.join(self.changed_files) or '(nothing)'}")
        if self.report is not None:
            body = self.report.render().replace("\n", "\n  ")
            parts.append(f"  tests: {body}")
        return "\n".join(parts)


def render_attempts(attempts: list[Attempt] | tuple[Attempt, ...]) -> str:
    return "\n\n".join(a.render() for a in attempts)


class Verifier(Protocol):
    """Runs the repository's tests against the workspace and reports back.

    ``changed`` is every file the rounds so far have touched; it is what test
    selection keys on. The implementation is wherever the sandbox is; this
    module only fixes the shape of what comes out.
    """

    async def run(self, ws: Workspace, plan: Plan, changed: Sequence[str]) -> TestReport: ...


def parse_junitxml(
    text: str, *, log_path: str | Path = "", known_failing: Iterable[str] = ()
) -> TestReport:
    """Turn pytest's ``--junitxml`` output into a report.

    ``known_failing`` are test ids that fail on the unchanged tree; a case in
    that set is counted as ``preexisting`` rather than as a failure, which is
    the harness-level baseline check the survey (3.5) describes: what was
    broken before the patch is not the patch's regression.

    Handles both a bare ``<testsuite>`` and the ``<testsuites>`` wrapper newer
    pytest writes; suites are summed. A test case counts as failed if it holds
    a ``<failure>``, as an error if it holds an ``<error>``, and as skipped if
    it holds a ``<skipped>``.
    """
    known = set(known_failing)
    root = ET.fromstring(text)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))

    report = TestReport(log_path=str(log_path))
    listed: list[Failure] = []
    omitted = 0
    for suite in suites:
        report.duration_seconds += float(suite.get("time") or 0.0)
        for case in suite.iter("testcase"):
            failure = case.find("failure")
            error = case.find("error")
            if case.find("skipped") is not None:
                report.skipped += 1
                continue
            node = failure if failure is not None else error
            if node is None:
                report.passed += 1
                continue
            if _test_id(case, node) in known:
                report.preexisting += 1
                continue
            if failure is not None:
                report.failed += 1
            else:
                report.errors += 1
            if len(listed) >= MAX_FAILURES:
                omitted += 1
                continue
            listed.append(_failure(case, node))

    report.failures = listed
    report.omitted = omitted
    return report


def failing_ids(text: str) -> set[str]:
    """Every failing or erroring test id in a JUnit file, uncapped."""
    root = ET.fromstring(text)
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    ids: set[str] = set()
    for suite in suites:
        for case in suite.iter("testcase"):
            node = case.find("failure")
            if node is None:
                node = case.find("error")
            if node is not None:
                ids.add(_test_id(case, node))
    return ids


def _test_id(case: ET.Element, node: ET.Element) -> str:
    """``classname::name`` -- stable across runs.

    The traceback's file is *not* part of the id: the same test can surface
    its failure in a different frame on the patched tree than on the base
    tree, and an id that moved would never match a baseline entry.
    """
    name = case.get("name") or "?"
    classname = case.get("classname") or ""
    return f"{classname}::{name}" if classname else name


def _failure(case: ET.Element, node: ET.Element) -> Failure:
    body = node.text or ""
    message = (node.get("message") or "").strip()

    frames = list(_FRAME.finditer(body))
    file, line, kind = "", 0, ""
    if frames:
        last = frames[-1]
        file, line, kind = last["file"], int(last["line"]), last["kind"]
    first = message.splitlines()[0] if message else ""
    if not kind and ":" in first:
        kind = first.split(":", 1)[0].strip()[:60]
    if not message:
        message = next((ln[1:].strip() for ln in body.splitlines() if ln.startswith("E ")), "")

    # pytest marks the failing statement with ">" and the explanation with "E".
    marked = [ln for ln in body.splitlines() if ln.startswith((">", "E "))]
    excerpt = "\n".join(marked[:MAX_EXCERPT_LINES])

    return Failure(
        test=_test_id(case, node),
        file=file,
        line=line,
        kind=kind,
        message=message[:500],
        excerpt=excerpt[:2000],
    )
