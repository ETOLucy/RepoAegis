"""The seam between our pipeline and SWE-bench's harness.

Nothing here runs Docker. What is worth testing is the seam itself: what we
hand the harness, what we make of what it hands back, and -- the part that
actually matters -- that a harness which fails to judge an instance is never
mistaken for a harness that judged it unresolved. Those two look identical in a
resolve rate and mean completely different things.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from repoaegis.eval.dataset import Instance
from repoaegis.eval.official import (
    MODEL_NAME,
    Harness,
    HarnessError,
    Outcome,
    harness_path,
    not_run,
    predictions_for,
)

# One real report.json entry, copied from a run of the official harness.
REPORT = {
    "psf__requests-1142": {
        "patch_is_None": False,
        "patch_exists": True,
        "patch_successfully_applied": True,
        "resolved": True,
        "infra_failure": False,
        "tests_status": {
            "FAIL_TO_PASS": {
                "success": ["test_requests.py::RequestsTestCase::test_no_content_length"],
                "failure": [],
            },
            "PASS_TO_PASS": {
                "success": [
                    "test_requests.py::RequestsTestCase::test_basic_building",
                    "test_requests.py::RequestsTestCase::test_entry_points",
                ],
                "failure": [],
            },
            "FAIL_TO_FAIL": {"success": [], "failure": []},
            "PASS_TO_FAIL": {"success": [], "failure": []},
        },
    }
}


def instance(name: str = "psf__requests-1142") -> Instance:
    return Instance(
        instance_id=name,
        repo="psf/requests",
        base_commit="c" * 40,
        problem_statement="something is wrong",
        gold_patch="--- a/requests/models.py\n+++ b/requests/models.py\n",
        test_patch="",
        fail_to_pass=("a::t1",),
        pass_to_pass=("b::t2", "b::t3"),
    )


class ScriptedHarness(Harness):
    """A harness whose subprocess is replaced by a function."""

    def __init__(self, script: Any, **kwargs: Any) -> None:
        super().__init__(python="python", **kwargs)
        object.__setattr__(self, "script", script)
        object.__setattr__(self, "calls", [])

    async def _run(self, *args: str, cwd: Path | None = None) -> tuple[int, str]:
        self.calls.append(list(args))  # type: ignore[attr-defined]
        return self.script(list(args), cwd)  # type: ignore[attr-defined]


def writes_report(
    payload: dict[str, Any] | None = None, *, model: str = MODEL_NAME, code: int = 0
) -> Any:
    """A script that behaves like a successful harness run."""

    def script(args: list[str], cwd: Path | None) -> tuple[int, str]:
        if payload is not None and cwd is not None:
            run_id = args[args.index("--run_id") + 1]
            for iid, entry in payload.items():
                out = cwd / "logs" / "run_evaluation" / run_id / model / iid
                out.mkdir(parents=True, exist_ok=True)
                (out / "report.json").write_text(json.dumps({iid: entry}), encoding="utf-8")
        return code, "done"

    return script


# MARK: pure helpers


def test_a_windows_path_is_rewritten_for_the_other_side_of_the_boundary() -> None:
    """The harness lives in WSL, where every drive hangs under /mnt."""
    assert harness_path(Path("D:/Repos/RepoAegis/data"), translate=True) == (
        "/mnt/d/Repos/RepoAegis/data"
    )


def test_without_a_prefix_the_path_is_left_alone() -> None:
    here = Path("data/eval").resolve()
    assert harness_path(here, translate=False) == str(here)


def test_predictions_carry_the_patch_and_nothing_else() -> None:
    rows = predictions_for({"psf__requests-1142": "diff --git a/x b/x\n"})
    assert rows == [
        {
            "instance_id": "psf__requests-1142",
            "model_name_or_path": MODEL_NAME,
            "model_patch": "diff --git a/x b/x\n",
        }
    ]


def test_an_unscored_instance_keeps_its_totals_so_the_report_still_adds_up() -> None:
    outcome = not_run(instance(), "docker 没起来")
    assert not outcome.resolved
    assert outcome.fail_to_pass_total == 1 and outcome.pass_to_pass_total == 2
    assert outcome.error == "docker 没起来"
    assert "未判分" in outcome.summary()


# MARK: reading the harness's report


async def test_a_resolved_report_is_read_back_field_for_field(tmp_path: Path) -> None:
    harness = ScriptedHarness(writes_report(REPORT))
    outcomes = await harness.evaluate(
        [instance()], {"psf__requests-1142": "diff"}, directory=tmp_path, run_id="r1"
    )

    outcome = outcomes["psf__requests-1142"]
    assert outcome.resolved
    assert outcome.fail_to_pass_passed == 1 and outcome.fail_to_pass_total == 1
    assert outcome.pass_to_pass_passed == 2 and outcome.pass_to_pass_total == 2
    assert outcome.patch_applied and not outcome.infra_failure
    assert outcome.summary() == "F2P 1/1 · P2P 2/2"


async def test_a_regression_is_named_not_just_counted(tmp_path: Path) -> None:
    entry = json.loads(json.dumps(REPORT))
    entry["psf__requests-1142"]["resolved"] = False
    entry["psf__requests-1142"]["tests_status"]["PASS_TO_PASS"] = {
        "success": ["test_requests.py::RequestsTestCase::test_basic_building"],
        "failure": ["test_requests.py::RequestsTestCase::test_entry_points"],
    }
    harness = ScriptedHarness(writes_report(entry))

    outcomes = await harness.evaluate(
        [instance()], {"psf__requests-1142": "diff"}, directory=tmp_path, run_id="r1"
    )
    outcome = outcomes["psf__requests-1142"]

    assert not outcome.resolved and outcome.broke_something
    assert outcome.regressions == ("test_requests.py::RequestsTestCase::test_entry_points",)
    assert "回归 1" in outcome.summary()


async def test_a_patch_that_did_not_apply_is_not_a_wrong_fix(tmp_path: Path) -> None:
    """Two very different failures that a single boolean would merge."""
    entry = json.loads(json.dumps(REPORT))
    entry["psf__requests-1142"].update(patch_successfully_applied=False, resolved=False)
    entry["psf__requests-1142"]["tests_status"] = {}
    harness = ScriptedHarness(writes_report(entry))

    outcomes = await harness.evaluate(
        [instance()], {"psf__requests-1142": "diff"}, directory=tmp_path, run_id="r1"
    )

    assert outcomes["psf__requests-1142"].summary() == "补丁打不进去"


async def test_a_missing_report_is_reported_as_unscored_rather_than_unresolved(
    tmp_path: Path,
) -> None:
    """The harness ran and said nothing about this instance. That is not a zero."""
    harness = ScriptedHarness(writes_report(None))  # exits 0, writes no report

    outcomes = await harness.evaluate(
        [instance()], {"psf__requests-1142": "diff"}, directory=tmp_path, run_id="r1"
    )
    outcome = outcomes["psf__requests-1142"]

    assert not outcome.resolved and outcome.error
    assert outcome.fail_to_pass_total == 1  # the totals survive for the report


async def test_a_harness_that_exits_nonzero_raises_rather_than_scoring_zero(
    tmp_path: Path,
) -> None:
    harness = ScriptedHarness(writes_report(None, code=1))

    with pytest.raises(HarnessError, match="退出码 1"):
        await harness.evaluate(
            [instance()], {"psf__requests-1142": "diff"}, directory=tmp_path, run_id="r1"
        )


# MARK: what gets submitted


async def test_an_instance_without_a_patch_is_never_submitted(tmp_path: Path) -> None:
    """Submitting an empty patch would bill a container to learn nothing."""
    harness = ScriptedHarness(writes_report(REPORT))
    outcomes = await harness.evaluate(
        [instance(), instance("psf__requests-2000")],
        {"psf__requests-1142": "diff", "psf__requests-2000": "   "},
        directory=tmp_path,
        run_id="r1",
    )

    submitted = harness.calls[0]  # type: ignore[attr-defined]
    assert submitted[submitted.index("--instance_ids") + 1 :] == ["psf__requests-1142"]
    assert outcomes["psf__requests-2000"].error == "agent 没有产出补丁"


async def test_nothing_to_score_makes_no_subprocess_call(tmp_path: Path) -> None:
    harness = ScriptedHarness(writes_report(REPORT))
    outcomes = await harness.evaluate(
        [instance()], {"psf__requests-1142": ""}, directory=tmp_path, run_id="r1"
    )
    assert harness.calls == []  # type: ignore[attr-defined]
    assert not outcomes["psf__requests-1142"].resolved


async def test_the_predictions_file_is_what_the_harness_is_pointed_at(tmp_path: Path) -> None:
    harness = ScriptedHarness(writes_report(REPORT))
    await harness.evaluate(
        [instance()],
        {"psf__requests-1142": "diff --git a/x b/x\n"},
        directory=tmp_path,
        run_id="r1",
    )

    args = harness.calls[0]  # type: ignore[attr-defined]
    path = Path(args[args.index("--predictions_path") + 1])
    assert path == tmp_path / "predictions.json"
    assert json.loads(path.read_text(encoding="utf-8"))[0]["model_patch"] == (
        "diff --git a/x b/x\n"
    )


async def test_gold_mode_submits_every_instance_and_reads_the_gold_directory(
    tmp_path: Path,
) -> None:
    """The self-check: the dataset's own patches, judged by the same path."""
    harness = ScriptedHarness(writes_report(REPORT, model="gold"))

    outcomes = await harness.evaluate([instance()], {}, directory=tmp_path, run_id="r1", gold=True)

    args = harness.calls[0]  # type: ignore[attr-defined]
    assert args[args.index("--predictions_path") + 1] == "gold"
    assert outcomes["psf__requests-1142"].resolved


def test_an_outcome_with_no_tests_at_all_is_not_resolved() -> None:
    assert not Outcome(resolved=False).resolved
