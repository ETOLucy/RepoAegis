"""Aggregation. The headline is resolve rate; the rest exists to explain it."""

import json
from pathlib import Path

import pytest

from repoaegis.eval.dataset import Instance
from repoaegis.eval.report import Result, render, result_for, summarise, write
from repoaegis.eval.runner import Attempt
from repoaegis.eval.scoring import Status, judge, not_run

PASS = {"a::t1": Status.PASSED, "b::t2": Status.PASSED}
BROKE = {"a::t1": Status.PASSED, "b::t2": Status.FAILED}


def instance(name: str, repo: str = "psf/requests", gold: str = "requests/models.py") -> Instance:
    return Instance(
        instance_id=name,
        repo=repo,
        base_commit="c" * 40,
        problem_statement="something is wrong",
        gold_patch=f"--- a/{gold}\n+++ b/{gold}\n",
        test_patch="",
        fail_to_pass=("a::t1",),
        pass_to_pass=("b::t2",),
        difficulty="<15 min fix",
    )


def attempt(
    name: str,
    *,
    status: str = "delivering",
    files: tuple[str, ...] = ("requests/models.py",),
    cost: float = 0.01,
    steps: int = 5,
    seconds: float = 30.0,
    failure: str = "",
) -> Attempt:
    patch = "".join(f"--- a/{f}\n+++ b/{f}\n" for f in files)
    return Attempt(
        instance_id=name,
        status=status,
        patch=patch,
        planned_files=files,
        patched_files=files,
        steps=steps,
        cost_usd=cost,
        seconds=seconds,
        failure=failure,
    )


def resolved_result(name: str = "i1") -> Result:
    return result_for(instance(name), attempt(name), judge(["a::t1"], ["b::t2"], PASS))


def test_an_empty_run_summarises_without_dividing_by_zero() -> None:
    summary = summarise([])
    assert summary.instances == 0 and summary.resolve_rate == 0.0
    assert "没有任何实例结果" in render(summary)


def test_resolve_rate_is_the_headline() -> None:
    results = [
        resolved_result("i1"),
        result_for(instance("i2"), attempt("i2"), judge(["a::t1"], ["b::t2"], BROKE)),
    ]
    summary = summarise(results)

    assert summary.instances == 2 and summary.resolved == 1
    assert summary.resolve_rate == 0.5
    assert "修复率" in render(summary) and "50.0%" in render(summary)


def test_costs_and_steps_are_averaged_and_totalled() -> None:
    results = [
        result_for(instance("i1"), attempt("i1", cost=0.01, steps=4, seconds=10), not_run([], [])),
        result_for(instance("i2"), attempt("i2", cost=0.03, steps=8, seconds=30), not_run([], [])),
    ]
    summary = summarise(results)

    assert summary.total_cost_usd == pytest.approx(0.04)
    assert summary.mean_cost_usd == pytest.approx(0.02)
    assert summary.mean_steps == 6.0
    assert summary.median_seconds == 20.0


def test_localisation_is_measured_against_the_reference_files() -> None:
    """One instance hits, one misses, one over-reaches: precision 2/4, recall 2/3."""
    results = [
        result_for(instance("i1", gold="a.py"), attempt("i1", files=("a.py",)), not_run([], [])),
        result_for(instance("i2", gold="b.py"), attempt("i2", files=("z.py",)), not_run([], [])),
        result_for(
            instance("i3", gold="c.py"), attempt("i3", files=("c.py", "extra.py")), not_run([], [])
        ),
    ]
    summary = summarise(results)

    assert summary.localisation_precision == 0.5
    assert summary.localisation_recall == 0.667  # rounded for a readable report


def test_every_unresolved_instance_lands_in_exactly_one_bucket() -> None:
    results = [
        resolved_result("ok"),
        result_for(
            instance("crashed"),
            attempt("crashed", status="failed", files=(), failure="GitHubError: no such issue"),
            not_run(["a::t1"], ["b::t2"]),
        ),
        result_for(instance("regressed"), attempt("regressed"), judge(["a::t1"], ["b::t2"], BROKE)),
        result_for(instance("silent"), attempt("silent"), judge(["a::t1"], ["b::t2"], {})),
    ]
    buckets = [r.failure_bucket for r in results]

    assert buckets == [
        "resolved",
        "no_patch:GitHubError",
        "broke_other_tests",
        "tests_did_not_run",
    ]
    assert sum(summarise(results).failures.values()) == len(results)


def test_a_run_is_split_by_repository_when_there_is_more_than_one() -> None:
    results = [
        resolved_result("i1"),
        result_for(
            instance("i2", repo="pallets/flask"),
            attempt("i2"),
            judge(["a::t1"], ["b::t2"], BROKE),
        ),
    ]
    summary = summarise(results)

    assert summary.by_repo["psf/requests"] == {"instances": 1, "resolved": 1}
    assert summary.by_repo["pallets/flask"] == {"instances": 1, "resolved": 0}
    assert "分仓库" in render(summary)


def test_writing_a_run_keeps_the_numbers_and_the_patches(tmp_path: Path) -> None:
    results = [resolved_result("psf__requests-1142")]
    summary = summarise(results, model="deepseek-flash")

    directory = write(tmp_path / "run-1", results, summary)

    saved = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    assert saved["resolved"] == 1 and saved["model"] == "deepseek-flash"

    rows = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    assert rows[0]["instance_id"] == "psf__requests-1142"
    assert rows[0]["resolved"] is True
    # The diff lives in its own file; the JSON keeps only its size.
    assert "patch" not in rows[0]["attempt"] and rows[0]["attempt"]["patch_lines"] > 0
    assert (directory / "patches" / "psf__requests-1142.diff").exists()
    assert "修复率" in (directory / "report.txt").read_text(encoding="utf-8")


def test_a_run_with_no_patch_writes_no_diff_file(tmp_path: Path) -> None:
    results = [
        result_for(
            instance("i1"),
            attempt("i1", status="failed", files=(), failure="RuntimeError: boom"),
            not_run(["a::t1"], ["b::t2"]),
        )
    ]
    directory = write(tmp_path / "run-2", results, summarise(results))
    assert list((directory / "patches").iterdir()) == []
