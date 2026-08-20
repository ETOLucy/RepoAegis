"""Unit tests for scripts/power_analysis.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.power_analysis import main, power_table  # noqa: E402

from repo_maintenance_agent.evaluation.significance import (  # noqa: E402
    required_n_for_power,
)

# Plan section 1.1 targets (~+-10% tolerance for the normal approximation).
PLAN_TARGET_N = [464, 178, 57, 30]


def _scenario_n_values(table: str) -> list[int]:
    return [int(line.split("|")[5]) for line in table.splitlines() if line.startswith("| 0.30 |")]


def test_power_table_contains_header_and_four_scenario_rows() -> None:
    table = power_table()

    assert "| p1 | p2 |" in table
    assert "Cohen's h" in table
    assert "?? n" in table
    assert len(_scenario_n_values(table)) == 4


def test_power_table_sample_sizes_match_plan_within_tolerance() -> None:
    sizes = _scenario_n_values(power_table())

    assert len(sizes) == len(PLAN_TARGET_N)
    for size, target in zip(sizes, PLAN_TARGET_N, strict=True):
        assert abs(size - target) <= 0.10 * target


def test_main_without_arguments_prints_power_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([]) == 0

    output = capsys.readouterr().out
    assert output.count("| 0.30 |") == 4


def test_main_prints_single_scenario_conclusion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected_n = required_n_for_power(0.30, 0.40)

    assert main(["--p1", "0.30", "--p2", "0.40"]) == 0

    output = capsys.readouterr().out
    assert "Cohen's h" in output
    assert str(expected_n) in output
