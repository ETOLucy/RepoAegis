"""The verdict rule. It has to be unforgiving in exactly the right places."""

import pytest

from repoaegis.eval.scoring import Status, judge, not_run, parse_pytest

OUTPUT = """\
============================= test session starts ==============================
collected 4 items

tests/test_redirects.py ..F.                                             [100%]

=========================== short test summary info ============================
PASSED tests/test_redirects.py::test_fragment_kept
PASSED tests/test_redirects.py::test_other
FAILED tests/test_redirects.py::test_broken - AssertionError: 1 != 2
SKIPPED [1] tests/test_redirects.py:40: needs network
========================= 1 failed, 2 passed in 0.12s ==========================
"""


def test_pytest_output_becomes_statuses() -> None:
    statuses = parse_pytest(OUTPUT)
    assert statuses["tests/test_redirects.py::test_fragment_kept"] is Status.PASSED
    assert statuses["tests/test_redirects.py::test_broken"] is Status.FAILED
    assert len(statuses) == 3  # the SKIPPED count line carries no test id


def test_errors_and_xfail_are_mapped() -> None:
    statuses = parse_pytest(
        "ERROR tests/a.py::test_collect\nXFAIL tests/a.py::test_known\nXPASS tests/a.py::test_lucky"
    )
    assert statuses["tests/a.py::test_collect"] is Status.ERROR
    assert statuses["tests/a.py::test_known"] is Status.SKIPPED
    assert statuses["tests/a.py::test_lucky"] is Status.PASSED


def test_the_last_mention_of_a_test_wins() -> None:
    statuses = parse_pytest("PASSED tests/a.py::test_x\nERROR tests/a.py::test_x")
    assert statuses["tests/a.py::test_x"] is Status.ERROR


def test_an_instance_resolves_only_when_both_lists_pass() -> None:
    outcome = judge(
        ["a::t1"],
        ["b::t2", "b::t3"],
        {
            "a::t1": Status.PASSED,
            "b::t2": Status.PASSED,
            "b::t3": Status.PASSED,
        },
    )
    assert outcome.resolved
    assert outcome.fail_to_pass_passed == 1 and outcome.pass_to_pass_passed == 2
    assert outcome.summary() == "F2P 1/1 · P2P 2/2"


def test_fixing_the_bug_by_breaking_something_else_does_not_resolve() -> None:
    """The second list is what stops 'delete the failing assertion' from working."""
    outcome = judge(["a::t1"], ["b::t2"], {"a::t1": Status.PASSED, "b::t2": Status.FAILED})
    assert not outcome.resolved
    assert outcome.fixed_the_bug and outcome.broke_something
    assert outcome.regressions == ("b::t2",)


def test_a_test_that_never_reported_counts_as_failed() -> None:
    """Silence usually means the module did not import; it must not read as a pass."""
    outcome = judge(["a::t1"], ["b::t2"], {"b::t2": Status.PASSED})
    assert not outcome.resolved
    assert outcome.missing == ("a::t1",)
    assert outcome.unfixed == ("a::t1",)


def test_a_skipped_target_test_is_not_a_pass() -> None:
    outcome = judge(["a::t1"], [], {"a::t1": Status.SKIPPED})
    assert not outcome.resolved and outcome.unfixed == ("a::t1",)


def test_an_instance_with_no_failing_tests_cannot_resolve() -> None:
    """Guards against an empty FAIL_TO_PASS scoring as a free win."""
    outcome = judge([], ["b::t2"], {"b::t2": Status.PASSED})
    assert not outcome.resolved


def test_a_run_that_never_happened_scores_zero() -> None:
    outcome = not_run(["a::t1"], ["b::t2"])
    assert not outcome.resolved
    assert outcome.fail_to_pass_passed == 0
    assert len(outcome.missing) == 2


def test_end_to_end_from_pytest_output() -> None:
    outcome = judge(
        ["tests/test_redirects.py::test_fragment_kept"],
        ["tests/test_redirects.py::test_other", "tests/test_redirects.py::test_broken"],
        parse_pytest(OUTPUT),
    )
    assert not outcome.resolved
    assert outcome.fixed_the_bug  # the target test passes
    assert outcome.regressions == ("tests/test_redirects.py::test_broken",)


@pytest.mark.parametrize("output", ["", "no summary here", "collected 0 items"])
def test_unparseable_output_yields_nothing_rather_than_guesses(output: str) -> None:
    assert parse_pytest(output) == {}
