from __future__ import annotations

from repo_maintenance_agent.sandbox.failure_parser import parse_failures

PYTEST_OUTPUT = """\
============================= test session starts =============================
tests/test_config.py::test_load_returns_default FAILED
______________________________ test_load_returns_default ______________________
tests/test_config.py:12: in test_load_returns_default
    assert load() == {"env": "prod"}
E   AssertionError: assert {'env': 'demo'} == {'env': 'prod'}
=========================== short test summary info ===========================
FAILED tests/test_config.py::test_load_returns_default
"""
JUNIT_OUTPUT = """\
<testsuite>
  <testcase classname="test_config" name="test_load_returns_default">
    <failure message="AssertionError: {'env': 'demo'} != {'env': 'prod'}"/>
  </testcase>
</testsuite>
"""


def test_parse_pytest_headers_and_assertions() -> None:
    summary = parse_failures(PYTEST_OUTPUT)
    assert not summary.is_empty
    assert summary.failures[0].name == "test_load_returns_default"
    assert summary.failures[0].location == "tests/test_config.py:12"
    assert "AssertionError" in summary.failures[0].message
    assert "test_load_returns_default" in summary.to_prompt_block()


def test_parse_junit_xml() -> None:
    summary = parse_failures(JUNIT_OUTPUT)
    assert not summary.is_empty
    assert summary.failures[0].name == "test_config::test_load_returns_default"
    assert "AssertionError" in summary.failures[0].message


def test_parse_empty_output_returns_raw_tail() -> None:
    summary = parse_failures("")
    assert summary.is_empty
    assert summary.raw_tail == ""
