"""Scoring by SWE-bench's own harness rather than by a reimplementation of it.

An earlier round judged runs here: a pytest log parser plus the two-list rule,
about two hundred lines. It agreed with the official harness on the one
instance it was ever checked against, and disagreed with it in at least four
ways that all pushed the same direction -- too strict:

* a ``PASS_TO_PASS`` test reported ``SKIPPED`` was counted as a regression,
  though a test that never ran cannot have been broken by the patch. The
  evaluation container has no network, so whole families of tests skip;
* ``XFAIL`` -- "the author expected this to fail, and it failed" -- was counted
  as a failure rather than as the normal outcome it is;
* parametrised test ids that the dataset stores truncated (676 of them in
  Verified) matched nothing and scored as never having run;
* the package under test was never reinstalled, so on any repository with a
  compiled extension the patch would not have taken effect at all.

The rule is short enough to copy correctly; what is not short is everything
around it -- per-framework log parsers, the container lifecycle, and the
workarounds for the dataset's own defects. More importantly, a resolve rate
produced by a private judge cannot be compared with a published one, which
makes the project's headline number unciteable. So the judging is theirs and
this module is only the seam: write a predictions file, run their harness, read
their report.

The harness runs wherever Docker is. On this machine the engine lives inside
WSL, so ``prefix`` is ``wsl -d Ubuntu --`` and paths handed across the boundary
are rewritten from ``D:\\x`` to ``/mnt/d/x``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import structlog

from repoaegis.eval.dataset import Instance

log = structlog.get_logger(__name__)

MODEL_NAME = "repoaegis"
DATASET = "SWE-bench/SWE-bench_Verified"
GOLD = "gold"


class HarnessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Outcome:
    """One instance's verdict, as the official harness reported it.

    ``resolved`` is the harness's own field, not something recomputed here.
    The counts and the two name lists exist so a report can say where a run
    fell short without opening the harness's log directory.
    """

    resolved: bool
    fail_to_pass_passed: int = 0
    fail_to_pass_total: int = 0
    pass_to_pass_passed: int = 0
    pass_to_pass_total: int = 0
    patch_applied: bool = True
    infra_failure: bool = False
    unfixed: tuple[str, ...] = ()
    regressions: tuple[str, ...] = ()
    error: str = ""

    @property
    def broke_something(self) -> bool:
        return bool(self.regressions)

    def summary(self) -> str:
        if self.error:
            return f"未判分：{self.error}"
        if not self.patch_applied:
            return "补丁打不进去"
        return (
            f"F2P {self.fail_to_pass_passed}/{self.fail_to_pass_total} · "
            f"P2P {self.pass_to_pass_passed}/{self.pass_to_pass_total}"
            + (f" · 回归 {len(self.regressions)}" if self.regressions else "")
        )


def not_run(instance: Instance, reason: str = "") -> Outcome:
    """The verdict when the harness never got to judge: no patch, or it crashed."""
    return Outcome(
        resolved=False,
        fail_to_pass_total=len(instance.fail_to_pass),
        pass_to_pass_total=len(instance.pass_to_pass),
        patch_applied=False,
        error=reason,
    )


def _outcome_from(report: Mapping[str, object]) -> Outcome:
    """Read one entry of the harness's per-instance ``report.json``."""
    status = report.get("tests_status") or {}
    assert isinstance(status, Mapping)

    def split(key: str) -> tuple[list[str], list[str]]:
        entry = status.get(key) or {}
        assert isinstance(entry, Mapping)
        return list(entry.get("success") or []), list(entry.get("failure") or [])

    f2p_ok, f2p_bad = split("FAIL_TO_PASS")
    p2p_ok, p2p_bad = split("PASS_TO_PASS")
    return Outcome(
        resolved=bool(report.get("resolved")),
        fail_to_pass_passed=len(f2p_ok),
        fail_to_pass_total=len(f2p_ok) + len(f2p_bad),
        pass_to_pass_passed=len(p2p_ok),
        pass_to_pass_total=len(p2p_ok) + len(p2p_bad),
        patch_applied=bool(report.get("patch_successfully_applied")),
        infra_failure=bool(report.get("infra_failure")),
        unfixed=tuple(f2p_bad),
        regressions=tuple(p2p_bad),
    )


def harness_path(path: Path, *, translate: bool) -> str:
    """A path as the harness's own filesystem spells it.

    ``wsl`` maps every Windows drive under ``/mnt``, so ``D:\\a\\b`` is
    ``/mnt/d/a/b`` on the other side. Without a prefix the harness runs here and
    the path is already correct.
    """
    if not translate:
        return str(path)
    # Read the drive off the path as given before resolving: on a POSIX host
    # (CI) ``resolve`` would glue "D:/x" under the working directory and the
    # drive letter would be lost.
    windows = PureWindowsPath(str(path))
    if not windows.drive:
        windows = PureWindowsPath(str(path.resolve()))
    if not windows.drive:
        return path.resolve().as_posix()
    tail = "/".join(windows.parts[1:])
    return f"/mnt/{windows.drive[0].lower()}/{tail}"


def predictions_for(attempts: Mapping[str, str]) -> list[dict[str, str]]:
    """The harness's input format: one object per instance, patch as text."""
    return [
        {"instance_id": iid, "model_name_or_path": MODEL_NAME, "model_patch": patch}
        for iid, patch in attempts.items()
    ]


@dataclass(frozen=True, slots=True)
class Harness:
    """SWE-bench's ``run_evaluation``, invoked as a subprocess."""

    python: str
    dataset: str = DATASET
    prefix: tuple[str, ...] = ()
    max_workers: int = 4
    timeout_seconds: float = 3600.0

    @property
    def translate_paths(self) -> bool:
        return bool(self.prefix)

    async def _run(self, *args: str, cwd: Path | None = None) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *self.prefix,
            self.python,
            *args,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(process.communicate(), self.timeout_seconds)
        except TimeoutError:
            process.kill()
            raise HarnessError(f"判分超时（{self.timeout_seconds:.0f}s）") from None
        return process.returncode or 0, out.decode("utf-8", "replace")

    async def version(self) -> str:
        """The installed swebench version, or "" when it is not reachable."""
        code, out = await self._run("-c", "import swebench; print(swebench.__version__)")
        return out.strip().splitlines()[-1] if code == 0 and out.strip() else ""

    async def evaluate(
        self,
        instances: Sequence[Instance],
        patches: Mapping[str, str],
        *,
        directory: Path,
        run_id: str,
        gold: bool = False,
    ) -> dict[str, Outcome]:
        """Score every instance that produced a patch. Returns id -> outcome.

        Instances without a patch are never submitted: the harness would report
        them as empty and the distinction between "the agent gave up" and "the
        fix was wrong" is the most useful one in the whole report.
        """
        scored = [i for i in instances if gold or patches.get(i.instance_id, "").strip()]
        skipped = {
            i.instance_id: not_run(i, "agent 没有产出补丁")
            for i in instances
            if i.instance_id not in {s.instance_id for s in scored}
        }
        if not scored:
            return skipped

        directory.mkdir(parents=True, exist_ok=True)
        if gold:
            predictions = GOLD
        else:
            path = directory / "predictions.json"
            path.write_text(
                json.dumps(
                    predictions_for({i.instance_id: patches[i.instance_id] for i in scored})
                ),
                encoding="utf-8",
            )
            predictions = harness_path(path, translate=self.translate_paths)

        code, output = await self._run(
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            self.dataset,
            "--predictions_path",
            predictions,
            "--run_id",
            run_id,
            "--max_workers",
            str(self.max_workers),
            "--report_dir",
            ".",
            "--instance_ids",
            *[i.instance_id for i in scored],
            cwd=directory,
        )
        log.info("eval.harness_finished", run_id=run_id, exit_code=code, instances=len(scored))
        if code != 0:
            tail = " / ".join(output.strip().splitlines()[-3:])
            raise HarnessError(f"run_evaluation 退出码 {code}：{tail}")

        model = GOLD if gold else MODEL_NAME
        reports = directory / "logs" / "run_evaluation" / run_id / model
        outcomes = dict(skipped)
        for instance in scored:
            outcomes[instance.instance_id] = self._read(reports, instance)
        return outcomes

    def _read(self, reports: Path, instance: Instance) -> Outcome:
        path = reports / instance.instance_id / "report.json"
        if not path.is_file():
            # The harness ran but wrote nothing for this instance: a container
            # that never started, or a build that failed. Not a wrong fix.
            return not_run(instance, "判分器没有产出这道题的报告")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return not_run(instance, f"报告读不出来：{exc}")
        entry = payload.get(instance.instance_id)
        if not isinstance(entry, Mapping):
            return not_run(instance, "报告里没有这道题")
        return _outcome_from(entry)
