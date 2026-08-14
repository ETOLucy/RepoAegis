"""Unit tests for scripts/gen_layers.py (difficulty-stratified daily 100 ids)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gen_layers import gen_layer_ids, main  # noqa: E402

REPOS = ("django__django", "astropy__astropy", "sympy__sympy")


def _record(repo: str, difficulty: str, index: int) -> dict:
    return {
        "instance_id": f"{repo}-{index:04d}",
        "repo": repo,
        "difficulty": difficulty,
        "problem_statement": f"Fix issue {index} in {repo}",
        "base_commit": "abc123",
    }


def _fixture_records(count: int = 30) -> list[dict]:
    """count rows with difficulty easy/medium/hard cycling (10 each for 30)."""
    return [
        _record(REPOS[index % 3], ("easy", "medium", "hard")[index % 3], index)
        for index in range(count)
    ]


def _write_input(tmp_path: Path, records: list[dict]) -> Path:
    path = tmp_path / "input.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return path


def _ids(payload: dict[str, list[str]]) -> set[str]:
    return set().union(*payload.values())


def test_layer_counts_match_targets_when_layers_are_sufficient(tmp_path) -> None:
    input_path = _write_input(tmp_path, _fixture_records(30))

    layers = gen_layer_ids(
        input_path,
        target_easy=5,
        target_medium=5,
        target_hard=5,
        seed=42,
    )

    assert {layer: len(ids) for layer, ids in layers.items()} == {
        "easy": 5,
        "medium": 5,
        "hard": 5,
    }


def test_same_seed_is_reproducible(tmp_path) -> None:
    input_path = _write_input(tmp_path, _fixture_records(30))

    first = gen_layer_ids(input_path, seed=7)
    second = gen_layer_ids(input_path, seed=7)

    assert first == second


def test_difficulty_values_are_case_insensitive(tmp_path) -> None:
    records = [
        _record("alpha__repo", "Easy", 0),
        _record("beta__repo", "MEDIUM", 1),
        _record("gamma__repo", "hard", 2),
    ]
    input_path = _write_input(tmp_path, records)

    layers = gen_layer_ids(input_path, target_easy=1, target_medium=1, target_hard=1)

    assert len(layers["easy"]) == 1
    assert len(layers["medium"]) == 1
    assert len(layers["hard"]) == 1


def test_ratio_based_stratification_when_difficulty_absent(tmp_path) -> None:
    records = [
        {"instance_id": "a-1", "repo": "a__repo", "resolved_by_baseline": 0.9},
        {"instance_id": "b-1", "repo": "b__repo", "resolved_by_baseline": 0.5},
        {"instance_id": "c-1", "repo": "c__repo", "resolved_by_baseline": 0.2},
    ]
    input_path = _write_input(tmp_path, records)

    layers = gen_layer_ids(input_path, target_easy=1, target_medium=1, target_hard=1)

    assert layers["easy"] == ["a-1"]
    assert layers["medium"] == ["b-1"]
    assert layers["hard"] == ["c-1"]


def test_repo_proxy_warns_when_difficulty_and_ratio_absent(tmp_path, capsys) -> None:
    records = [
        _record(REPOS[index % 3], "unknown", index)
        for index in range(9)
    ]
    for record in records:
        record.pop("difficulty")
    input_path = _write_input(tmp_path, records)

    layers = gen_layer_ids(input_path, seed=42)

    stderr = capsys.readouterr().err
    assert "difficulty absent, using repo proxy" in stderr
    assert len(_ids(layers)) == 9  # every row lands in exactly one layer
    for ids in layers.values():
        assert ids == sorted(ids)


def test_insufficient_layer_keeps_all_and_warns(tmp_path, capsys) -> None:
    input_path = _write_input(tmp_path, _fixture_records(30))

    layers = gen_layer_ids(
        input_path,
        target_easy=20,
        target_medium=40,
        target_hard=40,
        seed=42,
    )

    stderr = capsys.readouterr().err
    assert "below target_easy=20" in stderr
    assert "below target_medium=40" in stderr
    assert "below target_hard=40" in stderr
    assert {layer: len(ids) for layer, ids in layers.items()} == {
        "easy": 10,
        "medium": 10,
        "hard": 10,
    }


def test_output_lists_are_sorted(tmp_path) -> None:
    input_path = _write_input(tmp_path, _fixture_records(30))

    layers = gen_layer_ids(input_path, target_easy=8, target_medium=8, target_hard=8)

    for ids in layers.values():
        assert ids == sorted(ids)


def test_empty_input_raises(tmp_path) -> None:
    input_path = tmp_path / "empty.jsonl"
    input_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="contains no rows"):
        gen_layer_ids(input_path)


def test_main_writes_daily_100_ids_and_layers_json(tmp_path) -> None:
    records = (
        [_record("django__django", "easy", i) for i in range(20)]
        + [_record("astropy__astropy", "medium", i) for i in range(40)]
        + [_record("sympy__sympy", "hard", i) for i in range(40)]
    )
    input_path = _write_input(tmp_path, records)
    output_dir = tmp_path / "out"

    exit_code = main(
        [
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--seed",
            "42",
        ]
    )

    assert exit_code == 0
    ids_file = output_dir / "daily_100_ids.txt"
    assert ids_file.exists()
    lines = [line for line in ids_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 100
    assert len(set(lines)) == 100
    layers_file = output_dir / "layers.json"
    payload = json.loads(layers_file.read_text(encoding="utf-8"))
    assert payload["seed"] == 42
    assert payload["targets"] == {"easy": 20, "medium": 40, "hard": 40}
    assert [len(ids) for ids in payload["layers"].values()] == [20, 40, 40]
