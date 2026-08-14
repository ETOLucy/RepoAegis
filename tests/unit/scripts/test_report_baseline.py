"""Unit tests for scripts/report_baseline.py."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.report_baseline import (  # noqa: E402
    DEFAULT_BASELINES,
    extract_resolution_rate,
    load_baselines,
    load_result,
    render_baseline_table,
)


def _write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)
    return path


def test_load_baselines_two_columns(tmp_path):
    path = _write_csv(
        tmp_path / "baselines.csv",
        [
            ["name", "resolution_rate"],
            ["SWE-agent GPT-4o", "0.18"],
            ["Claude 3.5 Sonnet", "0.463"],
        ],
    )
    assert load_baselines(path) == {
        "SWE-agent GPT-4o": 0.18,
        "Claude 3.5 Sonnet": 0.463,
    }


def test_load_baselines_three_columns_ignores_note(tmp_path):
    path = _write_csv(
        tmp_path / "baselines.csv",
        [
            ["name", "resolution_rate", "note"],
            ["SWE-agent GPT-4o", "0.18", "official run 2025"],
            ["Claude 3.5 Sonnet", "0.463", "official run 2026"],
        ],
    )
    assert load_baselines(path) == {
        "SWE-agent GPT-4o": 0.18,
        "Claude 3.5 Sonnet": 0.463,
    }


def test_load_baselines_missing_columns_raises(tmp_path):
    path = _write_csv(tmp_path / "bad.csv", [["name", "score"], ["x", "0.5"]])
    with pytest.raises(ValueError, match="resolution_rate"):
        load_baselines(path)


def test_load_result_and_extract_resolution_rate(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps({"run": {"aggregate": {"resolution_rate": 0.375}}}),
        encoding="utf-8",
    )
    assert extract_resolution_rate(load_result(path)) == 0.375


def test_extract_resolution_rate_flat_fixture(tmp_path):
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps({"aggregate": {"resolution_rate": 0.375}}),
        encoding="utf-8",
    )
    assert extract_resolution_rate(load_result(path)) == 0.375


def _table_rows(table: str) -> list[list[str]]:
    return [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in table.splitlines()
        if line.startswith("|")
    ]


def test_render_baseline_table_headers_repoaegis_and_sorting():
    table = render_baseline_table(
        0.375,
        {
            "Claude 3.5 Sonnet": 0.463,
            "OpenHands / CodeAct": 0.26,
            "SWE-agent (GPT-4o)": 0.18,
        },
    )
    lines = table.splitlines()
    assert lines[0] == "| Method | Resolution | Delta vs RepoAegis |"
    assert lines[1] == "|---|---:|---:|"
    rows = _table_rows(table)[2:]
    rates = [float(row[1]) for row in rows]
    assert rates == sorted(rates, reverse=True)
    assert any(row[0] == "**RepoAegis (frozen subset)**" for row in rows)


def test_render_baseline_table_deltas():
    table = render_baseline_table(0.375, {"Claude 3.5 Sonnet": 0.463})
    rows = _table_rows(table)[2:]
    claude = next(row for row in rows if row[0] == "Claude 3.5 Sonnet")
    assert claude[1] == "0.463"
    assert claude[2] == "+0.088"


def test_render_baseline_table_self_delta_is_dash():
    table = render_baseline_table(0.375, {"OpenHands / CodeAct": 0.26})
    rows = _table_rows(table)[2:]
    own = next(row for row in rows if row[0] == "**RepoAegis (frozen subset)**")
    assert own[2] == "?"


def test_render_baseline_table_includes_disclaimer():
    table = render_baseline_table(0.375, {})
    assert "???????" in table
    assert "???????" in table


def test_default_baselines_include_public_references():
    assert DEFAULT_BASELINES["SWE-agent (GPT-4o)"] == pytest.approx(0.18)
    assert DEFAULT_BASELINES["Claude 3.5 Sonnet"] == pytest.approx(0.463)
    assert DEFAULT_BASELINES["OpenHands / CodeAct"] == pytest.approx(0.26)
