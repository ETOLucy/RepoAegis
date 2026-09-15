"""Deciding whether a patch actually fixed the bug.

The rule is SWE-bench's and it is deliberately unforgiving: an instance counts
as resolved only when every test in ``FAIL_TO_PASS`` now passes *and* every
test in ``PASS_TO_PASS`` still passes. The first half says the bug is fixed;
the second says nothing else was broken to achieve it. A patch that deletes the
failing assertion satisfies neither.

A test the run never reported is counted as failed, never as passed. Silence
usually means the file did not import -- a patch that breaks collection would
otherwise score as a clean sweep, which is precisely backwards.

Parsing is per-runner because output formats are. pytest is implemented here;
Django's own runner and others get their own parsers when their repositories
are added, which is how upstream structures it too.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum


class Status(StrEnum):
    """Named Status, not TestStatus: pytest tries to collect anything called Test*."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    MISSING = "missing"


# pytest -rA prints one line per test in the short summary, e.g.
#   PASSED tests/test_x.py::test_a
#   FAILED tests/test_x.py::test_b - AssertionError: ...
_SUMMARY = re.compile(
    r"^(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\s+(?P<test>\S+)", re.MULTILINE
)

_STATUS = {
    "PASSED": Status.PASSED,
    "XPASS": Status.PASSED,
    "FAILED": Status.FAILED,
    "ERROR": Status.ERROR,
    "XFAIL": Status.SKIPPED,
    "SKIPPED": Status.SKIPPED,
}


def parse_pytest(output: str) -> dict[str, Status]:
    """Map every test pytest reported to its status.

    Only ``path::test`` identifiers are kept. A skip line reads
    ``SKIPPED [1] tests/x.py:40: needs network``, whose second token is a count,
    not a test -- and the benchmark's lists are always ``::`` qualified anyway.

    The last mention wins: a test that passes and is then reported again in an
    error teardown must not count as passed.
    """
    statuses: dict[str, Status] = {}
    for match in _SUMMARY.finditer(output):
        name = match.group("test").rstrip(":")
        if "::" not in name:
            continue
        statuses[name] = _STATUS[match.group("status")]
    return statuses


@dataclass(frozen=True, slots=True)
class Outcome:
    """The verdict on one instance, with the evidence behind it."""

    resolved: bool
    fail_to_pass_passed: int
    fail_to_pass_total: int
    pass_to_pass_passed: int
    pass_to_pass_total: int
    missing: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    unfixed: tuple[str, ...] = ()

    @property
    def fixed_the_bug(self) -> bool:
        """The failing tests pass now, whatever happened to the rest."""
        return self.fail_to_pass_total > 0 and not self.unfixed

    @property
    def broke_something(self) -> bool:
        """A test that ran and failed. Tests that never ran are ``missing``."""
        return bool(self.regressions)

    def summary(self) -> str:
        return (
            f"F2P {self.fail_to_pass_passed}/{self.fail_to_pass_total} · "
            f"P2P {self.pass_to_pass_passed}/{self.pass_to_pass_total}"
            + (f" · 回归 {len(self.regressions)}" if self.regressions else "")
            + (f" · 缺失 {len(self.missing)}" if self.missing else "")
        )


def judge(
    fail_to_pass: Sequence[str],
    pass_to_pass: Sequence[str],
    statuses: Mapping[str, Status],
) -> Outcome:
    """Apply the benchmark's rule to one run's test statuses."""
    missing: list[str] = []
    unfixed: list[str] = []
    regressions: list[str] = []

    f2p_passed = 0
    for test in fail_to_pass:
        status = statuses.get(test, Status.MISSING)
        if status is Status.MISSING:
            missing.append(test)
        if status is Status.PASSED:
            f2p_passed += 1
        else:
            unfixed.append(test)

    p2p_passed = 0
    for test in pass_to_pass:
        status = statuses.get(test, Status.MISSING)
        if status is Status.MISSING:
            # Never ran, so it did not regress -- a different failure entirely,
            # and conflating the two hides the fact that collection broke.
            missing.append(test)
        elif status is Status.PASSED:
            p2p_passed += 1
        else:
            regressions.append(test)

    resolved = (
        len(fail_to_pass) > 0
        and f2p_passed == len(fail_to_pass)
        and p2p_passed == len(pass_to_pass)
    )
    return Outcome(
        resolved=resolved,
        fail_to_pass_passed=f2p_passed,
        fail_to_pass_total=len(fail_to_pass),
        pass_to_pass_passed=p2p_passed,
        pass_to_pass_total=len(pass_to_pass),
        missing=tuple(missing),
        regressions=tuple(regressions),
        unfixed=tuple(unfixed),
    )


def not_run(fail_to_pass: Sequence[str], pass_to_pass: Sequence[str]) -> Outcome:
    """The verdict when no tests ran at all -- no patch, or the run crashed."""
    return judge(fail_to_pass, pass_to_pass, {})
