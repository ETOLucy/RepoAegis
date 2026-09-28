"""The round record and the test report: what one round hands to the next."""

from repoaegis.agent.rounds import (
    MAX_FAILURES,
    Attempt,
    Failure,
    TestReport,
    parse_junitxml,
    render_attempts,
)

# Verbatim from `pytest --junitxml` on a three-test file where two fail.
JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">\
<testsuite name="pytest" errors="0" failures="2" skipped="0" tests="3" time="0.085">\
<testcase classname="test_body" name="test_empty_body_is_bytes" time="0.001">\
<failure message="AssertionError: assert None == b''&#10; +  where None = prepare_body('')">\
def test_empty_body_is_bytes():
&gt;       assert prepare_body("") == b""
E       AssertionError: assert None == b''
E        +  where None = prepare_body('')

test_body.py:8: AssertionError</failure></testcase>\
<testcase classname="test_body" name="test_nonempty_body" time="0.000" />\
<testcase classname="test_body" name="test_length_header" time="0.000">\
<failure message="TypeError: object of type 'NoneType' has no len()">def test_length_header():
        body = prepare_body("")
&gt;       assert len(body) == 0
               ^^^^^^^^^
E       TypeError: object of type 'NoneType' has no len()

test_body.py:15: TypeError</failure></testcase></testsuite></testsuites>"""


def test_counts_and_failures_come_from_the_junit_xml() -> None:
    report = parse_junitxml(JUNIT, log_path=".repoaegis/runs/1/pytest.log")

    assert (report.passed, report.failed, report.errors, report.skipped) == (1, 2, 0, 0)
    assert not report.ok
    assert report.duration_seconds == 0.085
    assert report.log_path == ".repoaegis/runs/1/pytest.log"

    first, second = report.failures
    assert first.test == "test_body.py::test_empty_body_is_bytes"
    assert (first.file, first.line, first.kind) == ("test_body.py", 8, "AssertionError")
    assert first.message.startswith("AssertionError: assert None == b''")
    assert first.excerpt.splitlines()[0].startswith(">")  # the failing statement
    assert (second.file, second.line, second.kind) == ("test_body.py", 15, "TypeError")


def test_a_green_suite_is_ok() -> None:
    xml = '<testsuite tests="2" failures="0" errors="0" time="0.5"><testcase name="a"/>\
<testcase name="b"/></testsuite>'
    report = parse_junitxml(xml)
    assert report.ok and report.passed == 2 and report.failures == []


def test_errors_and_skips_are_told_apart_from_failures() -> None:
    xml = (
        '<testsuite tests="3">'
        '<testcase classname="m" name="broken"><error message="ImportError: no x"/></testcase>'
        '<testcase classname="m" name="later"><skipped message="needs network"/></testcase>'
        '<testcase classname="m" name="fine"/>'
        "</testsuite>"
    )
    report = parse_junitxml(xml)
    assert (report.passed, report.failed, report.errors, report.skipped) == (1, 0, 1, 1)
    assert not report.ok
    assert report.failures[0].test == "m::broken"
    assert report.failures[0].kind == "ImportError"


def test_failures_past_the_cap_are_counted_not_listed() -> None:
    cases = "".join(
        f'<testcase classname="m" name="t{i}"><failure message="AssertionError: {i}"/></testcase>'
        for i in range(MAX_FAILURES + 5)
    )
    report = parse_junitxml(f"<testsuite>{cases}</testsuite>")
    assert report.failed == MAX_FAILURES + 5
    assert len(report.failures) == MAX_FAILURES and report.omitted == 5
    assert f"{report.omitted} more failures" in report.render()


def test_the_rendered_report_leads_with_counts_and_names_each_failure() -> None:
    text = parse_junitxml(JUNIT, log_path="log.txt").render()
    lines = text.splitlines()
    assert lines[0] == "1 passed, 2 failed, 0 errors, 0 skipped (0.1s)"
    assert "FAIL test_body.py::test_empty_body_is_bytes  (test_body.py:8, AssertionError)" in text
    assert "full log: log.txt" in text


def test_an_attempt_renders_hypothesis_files_and_tests() -> None:
    attempt = Attempt(
        round=2,
        hypothesis="empty bodies came back as None",
        changed_files=["requests/models.py"],
        report=TestReport(passed=5, failed=1, failures=[Failure(test="t::x", kind="TypeError")]),
    )
    text = render_attempts([attempt])
    assert text.startswith("Round 2 — hypothesis: empty bodies came back as None")
    assert "changed: requests/models.py" in text
    assert "5 passed, 1 failed" in text and "FAIL t::x" in text


def test_an_attempt_survives_the_event_log_round_trip() -> None:
    before = Attempt(round=1, hypothesis="h", changed_files=["a.py"], report=TestReport(passed=1))
    after = Attempt.model_validate(before.model_dump())
    assert after == before
