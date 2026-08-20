"""Parse verification output into structured failure attribution.
Extracts failing test names, assertion messages, and file:line locations from
pytest-style and JUnit-style output so the coding loop can iterate on the
actual failure instead of a wall of text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FAILURE_HEADER = re.compile(r"^_{5,}\s*(?P<name>[\w./\[\]()-]+)\s*_{5,}\s*$", re.MULTILINE)
_ASSERTION = re.compile(
    r"^(?P<location>.+\.py:\d+):\s*(?:in\s+\S+\s*)?(?P<message>.*(?:Error|error|assert).*)$",
    re.MULTILINE,
)
# pytest prints the actual exception on a line prefixed with "E" — prefer it
# over the source "assert ..." line because it carries the real error type
# (e.g. "AssertionError: assert {'env': 'demo'} == {'env': 'prod'}").
_PYTEST_ERROR = re.compile(r"^E\s+(?P<message>.*)$", re.MULTILINE)
_FAILED_SUMMARY = re.compile(r"^(?P<name>[\w./\[\]()-]+) (?:FAILED|ERROR)", re.MULTILINE)
_JUNIT_TESTCASE = re.compile(
    r'<testcase\s+classname="(?P<classname>[^"]+)"\s+name="(?P<name>[^"]+)"',
    re.MULTILINE,
)
_JUNIT_FAILURE = re.compile(r'<failure\s+message="(?P<message>[^"]*)"', re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Failure:
    name: str
    message: str = ""
    location: str = ""


@dataclass(frozen=True, slots=True)
class FailureSummary:
    failures: tuple[Failure, ...] = ()
    raw_tail: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.failures

    def to_prompt_block(self, *, limit: int = 3_000) -> str:
        if self.is_empty:
            return self.raw_tail[-limit:]
        lines = ["Structured failure attribution:"]
        for failure in self.failures[:20]:
            lines.append(f"- {failure.name} @ {failure.location or 'unknown'}: {failure.message}")
        joined = "\n".join(lines)
        return joined[-limit:]


def _first_pytest_error(window: str) -> str:
    match = _PYTEST_ERROR.search(window)
    return match.group("message")[:500] if match else ""


def parse_failures(output: str, *, tail_chars: int = 2_000) -> FailureSummary:
    """Best-effort parser for pytest and JUnit XML output.
    Returns structured failures when recognizable, otherwise the raw tail so
    the caller can still give the model *something*.
    """
    if not output:
        return FailureSummary(raw_tail="")
    failures: list[Failure] = []
    # 1. pytest "_____ test_name _____" headers with an assertion line under them
    header_positions = list(_FAILURE_HEADER.finditer(output))
    for match in header_positions:
        name = match.group("name")
        window = output[match.end() : match.end() + 2_000]
        assertion = _ASSERTION.search(window)
        if assertion is None:
            failures.append(Failure(name=name))
            continue
        failures.append(
            Failure(
                name=name,
                location=assertion.group("location"),
                message=(_first_pytest_error(window) or assertion.group("message")[:500]),
            )
        )
    # 2. pytest short summary "FAILED tests/x.py::test_y"
    if not failures:
        for match in _FAILED_SUMMARY.finditer(output):
            failures.append(Failure(name=match.group("name")))
    # 3. JUnit XML <testcase> + <failure>
    if not failures and "<testcase" in output:
        cases = list(_JUNIT_TESTCASE.finditer(output))
        failure_messages = [m.group("message") for m in _JUNIT_FAILURE.finditer(output)]
        for index, case in enumerate(cases):
            failures.append(
                Failure(
                    name=f"{case.group('classname')}::{case.group('name')}",
                    message=(
                        failure_messages[index][:500] if index < len(failure_messages) else ""
                    ),
                )
            )
    return FailureSummary(
        failures=tuple(failures),
        raw_tail=output[-tail_chars:],
    )
