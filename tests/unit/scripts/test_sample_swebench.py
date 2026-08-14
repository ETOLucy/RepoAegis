"""Unit tests for scripts/sample_swebench.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.sample_swebench import sample_swebench  # noqa: E402

REPOS = ("django__django", "astropy__astropy", "sympy__sympy")
DIFFICULTIES = ("easy", "medium", "hard")


def _make_record(repo: str, difficulty: str, index: int) -> dict:
    return {
        "instance_id": f"{repo}-{index:04d}",
        "repo": repo,
        "difficulty": difficulty,
        "problem_statement": f"Fix issue {index} in {repo}",
        "base_commit": "abc123",
        "test_patch": "--- a/x.py\n+++ b/x.py\n",
        "FAIL_TO_PASS": [f"tests/test_{index}::test_fix"],
        "PASS_TO_PASS": [f"tests/test_{index}::test_keep"],
    }


def _fixture_records(count: int = 30) -> list[dict]:
    return [
        _make_record(REPOS[index % 3], DIFFICULTIES[index % 3], index)
        for index in range(count)
    ]


def _write_input(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "input.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return path


def _read_output(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_samples_requested_size_with_metadata(tmp_path):
    input_path = _write_input(tmp_path, _fixture_records())
    output_path = tmp_path / "out.jsonl"

    selected = sample_swebench(input_path, output_path, target_size=10, seed=42)

    rows = _read_output(output_path)
    assert len(selected) == 10
    assert selected == rows
    for row in rows:
        assert row["sample_seed"] == 42
        assert row["sampled"] is True
        assert row["repo"] in REPOS


def test_same_seed_is_reproducible(tmp_path):
    input_path = _write_input(tmp_path, _fixture_records())
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    sample_swebench(input_path, first, target_size=10, seed=7)
    sample_swebench(input_path, second, target_size=10, seed=7)
    assert _read_output(first) == _read_output(second)


def test_different_seeds_produce_valid_equal_size_samples(tmp_path):
    input_path = _write_input(tmp_path, _fixture_records())
    out_a = tmp_path / "a.jsonl"
    out_b = tmp_path / "b.jsonl"
    sample_swebench(input_path, out_a, target_size=10, seed=1)
    sample_swebench(input_path, out_b, target_size=10, seed=2)
    rows_a = _read_output(out_a)
    rows_b = _read_output(out_b)
    assert len(rows_a) == len(rows_b) == 10
    assert {row["sample_seed"] for row in rows_a} == {1}
    assert {row["sample_seed"] for row in rows_b} == {2}


def test_different_seeds_can_produce_different_subsets(tmp_path):
    records = [_make_record("alpha__repo", "easy", i) for i in range(3)] + [
        _make_record("beta__repo", "hard", i) for i in range(3)
    ]
    input_path = _write_input(tmp_path, records)
    subsets: set[frozenset[str]] = set()
    for seed in range(16):
        output_path = tmp_path / f"out-{seed}.jsonl"
        sample_swebench(input_path, output_path, target_size=3, seed=seed)
        ids = {row["instance_id"] for row in _read_output(output_path)}
        subsets.add(frozenset(ids))
    assert len(subsets) > 1


def test_repo_coverage_when_repos_below_target(tmp_path):
    input_path = _write_input(tmp_path, _fixture_records())
    output_path = tmp_path / "out.jsonl"
    sample_swebench(input_path, output_path, target_size=10, seed=42)
    repos = {row["repo"] for row in _read_output(output_path)}
    assert repos == set(REPOS)


def test_target_size_beyond_input_warns_and_keeps_all(tmp_path, capsys):
    records = _fixture_records(count=30)
    input_path = _write_input(tmp_path, records)
    output_path = tmp_path / "out.jsonl"
    sample_swebench(input_path, output_path, target_size=40, seed=42)
    stderr = capsys.readouterr().err
    assert "target_size=40" in stderr
    rows = _read_output(output_path)
    assert len(rows) == 30
    assert {row["instance_id"] for row in rows} == {
        record["instance_id"] for record in records
    }


def test_output_sorted_by_instance_id(tmp_path):
    input_path = _write_input(tmp_path, _fixture_records())
    output_path = tmp_path / "out.jsonl"
    sample_swebench(input_path, output_path, target_size=10, seed=42)
    ids = [row["instance_id"] for row in _read_output(output_path)]
    assert ids == sorted(ids)


def test_empty_input_raises(tmp_path):
    input_path = tmp_path / "empty.jsonl"
    input_path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no rows"):
        sample_swebench(input_path, tmp_path / "out.jsonl", target_size=10, seed=42)


def test_missing_optional_fields_are_tolerated(tmp_path):
    records = [{"instance_id": "x-1", "repo": "only__repo", "difficulty": "easy"}]
    input_path = _write_input(tmp_path, records)
    output_path = tmp_path / "out.jsonl"
    sample_swebench(input_path, output_path, target_size=5, seed=42)
    rows = _read_output(output_path)
    assert len(rows) == 1
    assert rows[0]["repo"] == "only__repo"
    assert rows[0]["sampled"] is True
