"""Tests for repo_maintenance_agent.inspect.dataset."""

from __future__ import annotations

import json

import pytest
from inspect_ai.dataset import MemoryDataset, Sample

from repo_maintenance_agent.inspect.dataset import load_repoaegis_holdout

_HOLDOUT_RECORDS = [
    {
        "instance_id": "owner__repo-1",
        "problem_statement": "Fix the flaky test.",
        "repo": "owner/repo",
        "base_commit": "abc123",
        "test_patch": "diff --git a/tests/test_x.py b/tests/test_x.py",
        "FAIL_TO_PASS": ["tests/test_x.py::test_flaky", "tests/test_x.py::test_other"],
        "PASS_TO_PASS": ["tests/test_y.py::test_stable"],
        "difficulty": "easy",
        "gold_patch": "diff --git a/src/x.py b/src/x.py",
    },
    {
        "instance_id": "owner__repo-2",
        "problem_statement": "Handle empty input gracefully.",
        "repo": "owner/repo",
        "base_commit": "def456",
        "test_patch": "diff --git a/tests/test_z.py b/tests/test_z.py",
        # FAIL_TO_PASS stored as a JSON string to exercise normalization.
        "FAIL_TO_PASS": '["tests/test_z.py::test_empty"]',
        "PASS_TO_PASS": [],
        "difficulty": "medium",
    },
    {
        "instance_id": "owner__repo-3",
        "problem_statement": "Document the public API.",
        "repo": "owner/repo",
        "base_commit": "789ghi",
        "test_patch": "",
        "FAIL_TO_PASS": None,
        "PASS_TO_PASS": None,
    },
]


def _write_holdout(path, records, extra_lines: list[str] | None = None) -> None:
    lines = [json.dumps(record) for record in records]
    if extra_lines:
        lines = extra_lines + lines
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_repoaegis_holdout_returns_memory_dataset(tmp_path) -> None:
    holdout = tmp_path / "holdout.jsonl"
    _write_holdout(holdout, _HOLDOUT_RECORDS)

    dataset = load_repoaegis_holdout(holdout)

    assert isinstance(dataset, MemoryDataset)
    assert len(dataset) == 3
    assert dataset.name == "holdout"
    assert dataset.location == str(holdout)


def test_sample_fields_and_metadata(tmp_path) -> None:
    holdout = tmp_path / "holdout.jsonl"
    _write_holdout(holdout, [_HOLDOUT_RECORDS[0]])

    dataset = load_repoaegis_holdout(holdout)
    sample: Sample = dataset[0]

    assert sample.id == "owner__repo-1"
    assert sample.input == "Fix the flaky test."
    assert sample.target == "diff --git a/tests/test_x.py b/tests/test_x.py"

    metadata = sample.metadata
    assert metadata["repo"] == "owner/repo"
    assert metadata["base_commit"] == "abc123"
    assert metadata["difficulty"] == "easy"
    assert metadata["gold_patch"] == "diff --git a/src/x.py b/src/x.py"
    assert metadata["FAIL_TO_PASS"] == [
        "tests/test_x.py::test_flaky",
        "tests/test_x.py::test_other",
    ]
    assert metadata["PASS_TO_PASS"] == ["tests/test_y.py::test_stable"]


def test_test_lists_normalized(tmp_path) -> None:
    holdout = tmp_path / "holdout.jsonl"
    _write_holdout(holdout, [_HOLDOUT_RECORDS[1], _HOLDOUT_RECORDS[2]])

    dataset = load_repoaegis_holdout(holdout)

    json_string_record = dataset[0]
    assert json_string_record.metadata["FAIL_TO_PASS"] == ["tests/test_z.py::test_empty"]
    assert json_string_record.metadata["PASS_TO_PASS"] == []

    none_record = dataset[1]
    assert none_record.metadata["FAIL_TO_PASS"] == []
    assert none_record.metadata["PASS_TO_PASS"] == []
    assert none_record.target == ""


def test_blank_lines_are_skipped(tmp_path) -> None:
    holdout = tmp_path / "holdout.jsonl"
    _write_holdout(holdout, [_HOLDOUT_RECORDS[0]], extra_lines=["", "   "])

    dataset = load_repoaegis_holdout(holdout)

    assert len(dataset) == 1


def test_missing_required_fields_raises(tmp_path) -> None:
    holdout = tmp_path / "holdout.jsonl"
    holdout.write_text(
        json.dumps({"repo": "owner/repo", "problem_statement": "no instance id"})
        + "\n"
        + json.dumps({"instance_id": "owner__repo-x"})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="instance_id"):
        load_repoaegis_holdout(holdout)


def test_invalid_json_line_raises(tmp_path) -> None:
    holdout = tmp_path / "holdout.jsonl"
    holdout.write_text(
        '{"instance_id": "x", "problem_statement": "y"}\nNOT-JSON\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not valid JSON"):
        load_repoaegis_holdout(holdout)


def test_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_repoaegis_holdout(tmp_path / "nope.jsonl")
