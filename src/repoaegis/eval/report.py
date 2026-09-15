"""Turning a pile of attempts into the numbers a run is judged by.

Resolve rate is the headline and everything else exists to explain it. A run
that resolves 20% is not interesting until you can say where the other 80%
went: did the agent never produce a patch, produce one that broke other tests,
or run out of steps halfway? That breakdown is what turns a score into a list
of things to fix.

Localisation precision and recall are reported alongside, not instead. They
measure an intermediate stage against the reference fix's files, and a run can
localise perfectly while fixing nothing.

Every number is written to JSON as well as printed, because a score you cannot
diff against last week's score is a score you cannot improve.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from repoaegis.eval.dataset import Instance
from repoaegis.eval.runner import Attempt
from repoaegis.eval.scoring import Outcome


@dataclass(frozen=True, slots=True)
class Result:
    """One instance: what the agent did, and whether it worked."""

    instance_id: str
    repo: str
    difficulty: str
    attempt: Attempt
    outcome: Outcome
    gold_files: tuple[str, ...]

    @property
    def resolved(self) -> bool:
        return self.outcome.resolved

    @property
    def failure_bucket(self) -> str:
        """One word for why this instance did not resolve."""
        if self.outcome.resolved:
            return "resolved"
        if self.attempt.status == "failed":
            reason = self.attempt.failure.split(":", 1)[0] or "failed"
            return f"no_patch:{reason}"
        if not self.attempt.produced_patch:
            return "no_patch:empty"
        if self.outcome.regressions:
            return "broke_other_tests"
        if self.outcome.missing:
            return "tests_did_not_run"
        return "did_not_fix"


def _overlap(produced: Sequence[str], gold: Sequence[str]) -> tuple[int, int, int]:
    hit = len(set(produced) & set(gold))
    return hit, len(produced), len(gold)


@dataclass(frozen=True, slots=True)
class Summary:
    """Aggregate numbers for one evaluation run."""

    instances: int
    resolved: int
    produced_patch: int
    total_cost_usd: float
    mean_cost_usd: float
    median_seconds: float
    mean_steps: float
    localisation_precision: float
    localisation_recall: float
    failures: dict[str, int] = field(default_factory=dict)
    by_repo: dict[str, dict[str, int]] = field(default_factory=dict)
    policy: str = "auto_approve"
    model: str = ""
    created_at: str = ""

    @property
    def resolve_rate(self) -> float:
        return self.resolved / self.instances if self.instances else 0.0


def summarise(
    results: Sequence[Result], *, policy: str = "auto_approve", model: str = ""
) -> Summary:
    if not results:
        return Summary(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, created_at=_now())

    costs = [r.attempt.cost_usd for r in results]
    hits = precision_total = recall_total = 0
    for result in results:
        hit, produced, gold = _overlap(result.attempt.patched_files, result.gold_files)
        hits += hit
        precision_total += produced
        recall_total += gold

    by_repo: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = by_repo.setdefault(result.repo, {"instances": 0, "resolved": 0})
        bucket["instances"] += 1
        bucket["resolved"] += int(result.resolved)

    return Summary(
        instances=len(results),
        resolved=sum(r.resolved for r in results),
        produced_patch=sum(r.attempt.produced_patch for r in results),
        total_cost_usd=round(sum(costs), 6),
        mean_cost_usd=round(statistics.fmean(costs), 6),
        median_seconds=round(statistics.median(r.attempt.seconds for r in results), 1),
        mean_steps=round(statistics.fmean([r.attempt.steps for r in results]), 1),
        localisation_precision=round(hits / precision_total, 3) if precision_total else 0.0,
        localisation_recall=round(hits / recall_total, 3) if recall_total else 0.0,
        failures=dict(Counter(r.failure_bucket for r in results).most_common()),
        by_repo=by_repo,
        policy=policy,
        model=model,
        created_at=_now(),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def render(summary: Summary) -> str:
    """A plain-text report. The headline first, then why it is what it is."""
    if not summary.instances:
        return "没有任何实例结果。"

    lines = [
        f"实例 {summary.instances} · 策略 {summary.policy}"
        + (f" · 模型 {summary.model}" if summary.model else ""),
        "",
        f"  修复率     {summary.resolve_rate:>6.1%}   ({summary.resolved}/{summary.instances})",
        f"  产出补丁率 {summary.produced_patch / summary.instances:>6.1%}   "
        f"({summary.produced_patch}/{summary.instances})",
        f"  总花费     ${summary.total_cost_usd:.4f}   平均 ${summary.mean_cost_usd:.4f}/题",
        f"  平均步数   {summary.mean_steps:<6}  中位耗时 {summary.median_seconds}s",
        f"  定位准确率 {summary.localisation_precision:.2f}   召回率 "
        f"{summary.localisation_recall:.2f}",
        "",
        "  失败去向:",
    ]
    for bucket, count in summary.failures.items():
        share = count / summary.instances
        lines.append(f"    {bucket:<26} {count:>3}  {share:>5.1%}")

    if len(summary.by_repo) > 1:
        lines += ["", "  分仓库:"]
        for repo, counts in sorted(summary.by_repo.items()):
            rate = counts["resolved"] / counts["instances"]
            lines.append(
                f"    {repo:<26} {counts['resolved']:>3}/{counts['instances']:<3} {rate:>6.1%}"
            )
    return "\n".join(lines)


def write(directory: Path, results: Sequence[Result], summary: Summary) -> Path:
    """Persist the run. Patches go to their own files; JSON keeps the numbers."""
    directory.mkdir(parents=True, exist_ok=True)
    patches = directory / "patches"
    patches.mkdir(exist_ok=True)

    rows = []
    for result in results:
        if result.attempt.produced_patch:
            (patches / f"{result.instance_id}.diff").write_text(
                result.attempt.patch, encoding="utf-8"
            )
        row = {
            "instance_id": result.instance_id,
            "repo": result.repo,
            "difficulty": result.difficulty,
            "resolved": result.resolved,
            "failure_bucket": result.failure_bucket,
            "gold_files": list(result.gold_files),
            "outcome": asdict(result.outcome),
            "attempt": {
                **{k: v for k, v in asdict(result.attempt).items() if k != "patch"},
                "patch_lines": result.attempt.patch.count("\n"),
            },
        }
        rows.append(row)

    (directory / "summary.json").write_text(
        json.dumps(asdict(summary), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "results.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (directory / "report.txt").write_text(render(summary), encoding="utf-8")
    return directory


def result_for(instance: Instance, attempt: Attempt, outcome: Outcome) -> Result:
    return Result(
        instance_id=instance.instance_id,
        repo=instance.repo,
        difficulty=instance.difficulty,
        attempt=attempt,
        outcome=outcome,
        gold_files=instance.gold_files,
    )
