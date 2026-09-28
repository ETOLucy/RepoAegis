"""``python -m repoaegis.eval`` -- run the benchmark and write a report.

Two phases, because the two halves want opposite things. The agent phase is one
instance at a time: the model calls are the slow part, they are rate-limited
upstream, and running several at once on a laptop mostly buys flaky timing
measurements. The scoring phase is a single call into SWE-bench's own harness
with every patch at once, because that is the interface it offers and it
parallelises containers better than this loop would.

Every run lands in its own directory, including the failed ones. A benchmark
you cannot re-read afterwards is a benchmark you have to re-run to argue with.

``--gold`` skips the agent entirely and submits the dataset's own reference
patches. It must score 100%; anything less is a broken harness rather than a
weak agent, and finding that out from a real run is finding out too late.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog

from repoaegis.eval.dataset import Instance, load
from repoaegis.eval.official import Harness, HarnessError, Outcome, not_run
from repoaegis.eval.report import Result, render, result_for, summarise, write
from repoaegis.eval.runner import Attempt, InstanceRunner
from repoaegis.server.config import Settings, configure_logging

log = structlog.get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="repoaegis.eval", description="跑 SWE-bench 子集")
    parser.add_argument("--repo", action="append", help="只跑这个仓库的题，可重复")
    parser.add_argument("--instance", action="append", help="只跑这些实例 id，可重复")
    parser.add_argument("--difficulty", action="append", help="按难度筛选，如 '<15 min fix'")
    parser.add_argument("--limit", type=int, help="最多跑几道")
    parser.add_argument("--policy", default="auto_approve", help="审批策略（消融用）")
    parser.add_argument("--out", type=Path, default=Path("data/eval/runs"), help="报告目录")
    parser.add_argument(
        "--no-tests", action="store_true", help="只跑 agent，不判分（不需要 Docker）"
    )
    parser.add_argument(
        "--gold",
        action="store_true",
        help="不跑 agent，直接用数据集的参考补丁判分；这是判分链路的自检，必须 100%%",
    )
    return parser.parse_args(argv)


def harness_from(settings: Settings) -> Harness:
    return Harness(
        python=settings.eval_harness_python,
        dataset=settings.eval_dataset_name,
        prefix=tuple(shlex.split(settings.eval_harness_prefix)),
        max_workers=settings.eval_max_workers,
        timeout_seconds=settings.eval_timeout_seconds,
    )


async def score(
    harness: Harness,
    instances: list[Instance],
    attempts: dict[str, Attempt],
    *,
    directory: Path,
    run_id: str,
    gold: bool,
) -> dict[str, Outcome]:
    """Hand every patch to the official harness at once."""
    patches = {iid: attempt.patch for iid, attempt in attempts.items()}
    try:
        return await harness.evaluate(
            instances, patches, directory=directory, run_id=run_id, gold=gold
        )
    except HarnessError as exc:
        log.warning("eval.harness_failed", error=str(exc))
        return {i.instance_id: not_run(i, str(exc)) for i in instances}


def _utf8_console() -> None:
    """A Windows console defaults to GBK, which has no "✓" and raises on it."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings()
    _utf8_console()
    configure_logging(json=settings.log_json)

    instances = load(
        repos=args.repo,
        instance_ids=args.instance,
        difficulties=args.difficulty,
        limit=args.limit,
    )
    if not instances:
        print("筛选条件没有匹配到任何实例。", file=sys.stderr)
        return 1

    harness = harness_from(settings)
    if not args.no_tests:
        if not harness.python:
            print(
                "没有配置判分器。请先在 WSL 里装好 swebench，再设置\n"
                "  REPOAEGIS_EVAL_HARNESS_PYTHON=/path/to/.venv/bin/python\n"
                "  REPOAEGIS_EVAL_HARNESS_PREFIX='wsl -d Ubuntu --'\n"
                "加 --no-tests 可以只跑 agent。",
                file=sys.stderr,
            )
            return 2
        version = await harness.version()
        if not version:
            print(
                f"判分器跑不起来（{harness.python}）。加 --no-tests 可以只跑 agent。",
                file=sys.stderr,
            )
            return 2
        print(f"判分器：swebench {version} · 数据集 {harness.dataset}")

    if args.gold and args.no_tests:
        print("--gold 是判分自检，不能和 --no-tests 一起用。", file=sys.stderr)
        return 1

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    directory = args.out / stamp
    runner = InstanceRunner(settings, directory / "work", policy=args.policy)

    attempts: dict[str, Attempt] = {}
    if args.gold:
        # The reference patch is the attempt, files and all, so localisation
        # reads 1.00 -- trivially, which is the point: in gold mode every
        # number except the resolve rate is meaningless by construction.
        attempts = {
            i.instance_id: Attempt(
                i.instance_id, "gold", patch=i.gold_patch, patched_files=i.gold_files
            )
            for i in instances
        }
    else:
        for number, instance in enumerate(instances, start=1):
            print(f"[{number}/{len(instances)}] {instance.instance_id}", flush=True)
            attempt = await runner.run(instance)
            attempts[instance.instance_id] = attempt
            print(
                f"    {attempt.status}  {attempt.steps} 步  ${attempt.cost_usd:.4f}  "
                f"{'有补丁' if attempt.produced_patch else '无补丁'}",
                flush=True,
            )

    if args.no_tests:
        outcomes = {i.instance_id: not_run(i, "本次没有判分") for i in instances}
    else:
        print(f"\n判分中（{len(instances)} 道，官方 harness）…", flush=True)
        outcomes = await score(
            harness,
            instances,
            attempts,
            directory=directory,
            run_id=f"repoaegis_{stamp}",
            gold=args.gold,
        )

    results: list[Result] = []
    for instance in instances:
        outcome = outcomes[instance.instance_id]
        results.append(result_for(instance, attempts[instance.instance_id], outcome))
        mark = "✓" if outcome.resolved else "✗"
        print(f"  {mark} {instance.instance_id:<34} {outcome.summary()}")

    summary = summarise(results, policy=args.policy, model=settings.llm_model)
    write(directory, results, summary)
    print()
    print(render(summary))
    print(f"\n报告写入 {directory}")
    if args.gold and summary.resolved != summary.instances:
        print("\n参考补丁没有全部判成解决 —— 判分链路有问题，不是 agent 的问题。", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
