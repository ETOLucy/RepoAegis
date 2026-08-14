"""Tests for repo_maintenance_agent.inspect.parser.

Uses a self-constructed JSONL fixture (two ``sample`` events, one skipped
non-sample event, one malformed line) to exercise the tolerant parsing logic.
"""

from __future__ import annotations

import json

import pytest

from repo_maintenance_agent.inspect.parser import EvalResult, parse_inspect_log

_FULL_SAMPLE_EVENT = {
    "type": "sample",
    "run_id": "run-abc123",
    "eval": {"model": "openai/gpt-5.5"},
    "sample": {
        "id": "owner__repo-1",
        "scores": {
            "swebench": {"value": "C"},
            "progress": {"value": 1.0},
        },
        "usage": {
            "input_tokens": 100,
            "output_tokens": 40,
            "total_tokens": 140,
        },
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "checking", "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "tool_call_id": "t1", "content": "ok"},
        ],
    },
}

_MINIMAL_SAMPLE_EVENT = {
    "type": "sample",
    # Intentionally missing run_id / eval / scores / usage / messages / id.
    "sample": {},
}

_NON_SAMPLE_EVENT = {"type": "model", "model": "openai/gpt-4o", "messages": []}

_BAD_JSON_LINE = '{"type": "sample", "sample": {"id": '


def _write_jsonl(path, events) -> None:
    lines = [event if isinstance(event, str) else json.dumps(event) for event in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_parse_full_sample_event(tmp_path) -> None:
    log_path = tmp_path / "run.eval"
    _write_jsonl(log_path, [_FULL_SAMPLE_EVENT, _NON_SAMPLE_EVENT])

    results = parse_inspect_log(log_path)

    assert len(results) == 1
    result = results[0]
    assert isinstance(result, EvalResult)
    assert result.run_id == "run-abc123"
    assert result.model == "openai/gpt-5.5"
    assert result.sample_id == "owner__repo-1"
    assert result.score == 1.0  # prefers the "progress" score
    assert result.extra_scores == {"swebench": 1.0}  # letter grade "C" -> 1.0
    assert result.tokens == {
        "input_tokens": 100,
        "output_tokens": 40,
        "total_tokens": 140,
    }
    assert result.tool_calls == 1  # one assistant tool_calls entry
    assert result.status == "completed"
    assert result.source == "inspect"


def test_parse_skips_non_sample_and_malformed_lines(tmp_path) -> None:
    log_path = tmp_path / "mixed.eval"
    _write_jsonl(log_path, [_NON_SAMPLE_EVENT, _BAD_JSON_LINE, _FULL_SAMPLE_EVENT])

    results = parse_inspect_log(log_path)

    assert len(results) == 1
    assert results[0].sample_id == "owner__repo-1"


def test_parse_missing_fields_tolerated(tmp_path) -> None:
    log_path = tmp_path / "minimal.eval"
    _write_jsonl(log_path, [_MINIMAL_SAMPLE_EVENT])

    results = parse_inspect_log(log_path)

    assert len(results) == 1
    result = results[0]
    assert result.run_id == ""
    assert result.model == ""
    assert result.sample_id is None
    assert result.score is None
    assert result.extra_scores == {}
    assert result.tokens == {}
    assert result.tool_calls == 0
    assert result.status == "unknown"


def test_parse_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_inspect_log(tmp_path / "does-not-exist.eval")


def test_parse_zip_magic_unreadable_raises_value_error(tmp_path) -> None:
    log_path = tmp_path / "broken.eval"
    log_path.write_bytes(b"PK\x03\x04this-is-not-a-real-zip")

    with pytest.raises(ValueError):
        parse_inspect_log(log_path)


def test_parse_blank_and_empty_file_returns_empty(tmp_path) -> None:
    log_path = tmp_path / "empty.eval"
    log_path.write_text("\n\n", encoding="utf-8")

    assert parse_inspect_log(log_path) == []
