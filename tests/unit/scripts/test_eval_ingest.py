"""Unit tests for scripts/eval_ingest.py (Inspect log -> EvalResult + bootstrap)."""

from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scripts.eval_ingest import (  # noqa: E402
    EvalResult,
    _default_minimum_effect,
    bootstrap_summary,
    ingest_inspect_log,
    main,
)

_SUMMARY_RE = re.compile(
    r"-?\d+\.\d{4} \[-?\d+\.\d{4}, -?\d+\.\d{4}\] "
    r"\((improvement|regression|inconclusive)\)"
)


def _sample(
    sample_id: str,
    *,
    scores: dict | None = None,
    usage: dict | None = None,
    elapsed: float | None = None,
    metadata: dict | None = None,
) -> dict:
    sample: dict = {"id": sample_id}
    if scores is not None:
        sample["scores"] = scores
    if usage is not None:
        sample["model_usage"] = usage
    if elapsed is not None:
        sample["elapsed"] = elapsed
    if metadata is not None:
        sample["metadata"] = metadata
    return sample


def _log_payload(samples: list, model: str = "openai/gpt-5.5", seed: int = 7) -> dict:
    return {"eval": {"run_id": "run-abc", "model": model, "seed": seed}, "samples": samples}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_ingest_directory_log_and_score_json(tmp_path) -> None:
    samples = [
        _sample(
            "case-a",
            scores={"swebench": {"value": "C"}},
            usage={"input_tokens": 100, "output_tokens": 50, "estimated_cost_cny": 0.012},
            elapsed=1.5,
            metadata={"safety_violations": 2},
        ),
        _sample("case-b", scores={}),
    ]
    _write_json(tmp_path / "log.json", _log_payload(samples))
    _write_json(
        tmp_path / "score.json",
        {"scores": [{"sample_id": "case-b", "score": {"value": "I"}}]},
    )

    results = ingest_inspect_log(tmp_path)

    assert len(results) == 2
    by_case = {result.case_id: result for result in results}
    assert by_case["case-a"].score == 1.0  # letter grade "C" -> 1
    assert by_case["case-a"].safety_violations == 2
    assert by_case["case-a"].cost_cny == 0.012
    assert by_case["case-a"].latency_ms == 1500
    assert by_case["case-a"].model == "openai/gpt-5.5"
    assert by_case["case-a"].seed == 7
    assert by_case["case-a"].source == "inspect"
    assert by_case["case-b"].score == 0.0  # filled from score.json ("I" -> 0)


def test_ingest_zip_archive(tmp_path) -> None:
    log_payload = _log_payload(
        [_sample("case-a", scores={})],
        model="openai/gpt-4o",
        seed=3,
    )
    score_payload = {"scores": [{"sample_id": "case-a", "score": {"value": "C"}}]}
    archive = tmp_path / "run.eval"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("log.json", json.dumps(log_payload))
        zf.writestr("score.json", json.dumps(score_payload))

    results = ingest_inspect_log(archive)

    assert len(results) == 1
    assert results[0].case_id == "case-a"
    assert results[0].score == 1.0
    assert results[0].model == "openai/gpt-4o"
    assert results[0].seed == 3


def test_ingest_bare_log_json_file(tmp_path) -> None:
    log_file = tmp_path / "log.json"
    _write_json(
        log_file,
        _log_payload([_sample("solo", scores={"accuracy": {"value": 0.5}})]),
    )

    results = ingest_inspect_log(log_file)

    assert len(results) == 1
    assert results[0].case_id == "solo"
    assert results[0].score == 0.5


def test_ingest_letter_grades_map_to_numbers(tmp_path) -> None:
    _write_json(
        tmp_path / "log.json",
        _log_payload(
            [
                _sample("pass", scores={"swebench": {"value": "C"}}),
                _sample("fail", scores={"swebench": {"value": "I"}}),
            ]
        ),
    )

    results = ingest_inspect_log(tmp_path)

    assert {result.case_id: result.score for result in results} == {
        "pass": 1.0,
        "fail": 0.0,
    }


def test_ingest_estimates_cost_when_usage_has_no_cost(tmp_path) -> None:
    _write_json(
        tmp_path / "log.json",
        _log_payload(
            [
                _sample(
                    "costed",
                    scores={"progress": {"value": 1.0}},
                    usage={"input_tokens": 1000, "output_tokens": 500},
                )
            ]
        ),
    )

    results = ingest_inspect_log(tmp_path)

    assert results[0].cost_cny == pytest.approx(1000 * 20e-6 + 500 * 60e-6)


def test_ingest_tolerates_missing_fields_and_skips_non_dict_samples(tmp_path) -> None:
    _write_json(
        tmp_path / "log.json",
        _log_payload(
            [
                {"id": "bare"},
                42,
                "not-a-sample",
                {"id": "scored", "scores": {"progress": {"value": 0.25}}},
            ]
        ),
    )

    results = ingest_inspect_log(tmp_path)

    assert len(results) == 2
    assert results[0].case_id == "bare"
    assert results[0].score is None
    assert results[0].safety_violations == 0
    assert results[0].cost_cny == 0.0
    assert results[0].latency_ms == 0
    assert results[0].model == "openai/gpt-5.5"  # header model applies to all samples
    assert results[0].seed == 7
    assert results[1].score == 0.25


def test_ingest_missing_file_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        ingest_inspect_log(tmp_path / "does-not-exist")


def test_ingest_directory_without_log_json_raises(tmp_path) -> None:
    with pytest.raises(ValueError, match=r"contains no log\.json"):
        ingest_inspect_log(tmp_path)


def _results(
    scores_a: list[float],
    scores_b: list[float],
) -> tuple[list[EvalResult], list[EvalResult]]:
    def make(scores: list[float]) -> list[EvalResult]:
        return [EvalResult(case_id=f"c{index}", score=score) for index, score in enumerate(scores)]

    return make(scores_a), make(scores_b)


def test_bootstrap_summary_reports_improvement(tmp_path) -> None:
    results_a, results_b = _results([0.0] * 10, [1.0] * 10)

    summary = bootstrap_summary(results_a, results_b, seed=42)

    assert _SUMMARY_RE.match(summary)
    assert "(improvement)" in summary


def test_bootstrap_summary_reports_regression(tmp_path) -> None:
    results_a, results_b = _results([1.0] * 10, [0.0] * 10)

    summary = bootstrap_summary(results_a, results_b, seed=42)

    assert _SUMMARY_RE.match(summary)
    assert "(regression)" in summary


def test_bootstrap_summary_minimum_effect_downgrades_small_wins(tmp_path) -> None:
    results_a, results_b = _results([0.0] * 20, [0.01] * 20)

    defaulted = bootstrap_summary(results_a, results_b, seed=42)
    relaxed = bootstrap_summary(results_a, results_b, seed=42, minimum_effect=0.0)

    assert "(inconclusive)" in defaulted  # 0.01 < default tiered minimum effect
    assert "(improvement)" in relaxed


def test_bootstrap_summary_requires_equal_lengths(tmp_path) -> None:
    results_a, results_b = _results([0.0, 1.0], [0.0])

    with pytest.raises(ValueError, match="equal length"):
        bootstrap_summary(results_a, results_b, seed=42)


def test_default_minimum_effect_tier() -> None:
    # Mirrors significance.minimum_effect_tier (Cohen's h tiers).
    assert _default_minimum_effect(5) == 0.10
    assert _default_minimum_effect(50) == 0.10
    assert _default_minimum_effect(100) == 0.05
    assert _default_minimum_effect(300) == 0.03


def test_main_outputs_jsonl_rows_and_bootstrap_summary(tmp_path, capsys) -> None:
    champion = tmp_path / "champion"
    challenger = tmp_path / "challenger"
    champion.mkdir()
    challenger.mkdir()
    _write_json(
        champion / "log.json",
        _log_payload([_sample("c-1", scores={"swebench": {"value": "C"}})]),
    )
    _write_json(
        challenger / "log.json",
        _log_payload([_sample("c-1", scores={"swebench": {"value": "I"}})]),
    )

    exit_code = main(["--dir", str(tmp_path), "--seed", "42"])

    assert exit_code == 0
    captured = capsys.readouterr().out
    lines = [line for line in captured.splitlines() if line]
    assert len(lines) == 3  # 1 JSONL per arm + bootstrap line
    rows = [json.loads(line) for line in lines if line.startswith("{")]
    assert [row["arm"] for row in rows] == ["champion", "challenger"]
    assert rows[0]["case_id"] == "c-1"
    assert rows[0]["score"] == 1.0
    assert rows[1]["score"] == 0.0
    assert any(line.startswith("bootstrap: ") for line in lines)
    summary = next(line for line in lines if line.startswith("bootstrap: "))
    assert _SUMMARY_RE.search(summary)
    assert "(regression)" in summary
