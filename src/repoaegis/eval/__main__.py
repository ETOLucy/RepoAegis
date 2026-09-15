"""``python -m repoaegis.eval`` -- run the benchmark and write a report.

One instance at a time, on purpose. The model calls are the slow part and they
are already rate-limited upstream; running several containers at once on a
laptop mostly buys flaky timing measurements. Concurrency belongs here only
once the numbers are stable enough that their variance is understood.

Every run lands in its own directory, including the failed ones. A benchmark
you cannot re-read afterwards is a benchmark you have to re-run to argue with.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog

from repoaegis.eval.dataset import Instance, load
from repoaegis.eval.report import Result, render, result_for, summarise, write
from repoaegis.eval.runner import Attempt, InstanceRunner
from repoaegis.eval.sandbox import DockerSandbox, SandboxError
from repoaegis.eval.scoring import Outcome, judge, not_run, parse_pytest
from repoaegis.server.config import Settings, configure_logging

log = structlog.get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="repoaegis.eval", description="跑 SWE-bench 子集")
    parser.add_argument("--repo", action="append", help="只跑这个仓库的题，可重复")
    parser.add_argument("--instance", action="append", help="只跑这些实例 id，可重复")
    parser.add_argument("--difficulty", action="append", help="按难度筛选，如 '<15 min fix'")
    parser.add_argument("--limit", type=int, help="最多跑几道")
    parser.add_argument("--policy", default="auto_approve", help="审批策略（消融用）")
    parser.add_argument(
        "--docker",
        default="docker",
        help="调用 docker 的命令；WSL 上用 'wsl -d Ubuntu -- docker'",
    )
    parser.add_argument("--out", type=Path, default=Path("data/eval/runs"), help="报告目录")
    parser.add_argument(
        "--no-tests", action="store_true", help="只跑 agent，不判分（不需要 Docker）"
    )
    return parser.parse_args(argv)


async def score(
    sandbox: DockerSandbox, instance: Instance, attempt: Attempt, skip: bool
) -> Outcome:
    """Run the benchmark's tests against the produced patch."""
    if skip or not attempt.produced_patch:
        return not_run(instance.fail_to_pass, instance.pass_to_pass)
    try:
        run = await sandbox.run_tests(instance, attempt.patch)
    except SandboxError as exc:
        log.warning("eval.sandbox_failed", instance=instance.instance_id, error=str(exc))
        return not_run(instance.fail_to_pass, instance.pass_to_pass)
    if not run.patch_applied:
        return not_run(instance.fail_to_pass, instance.pass_to_pass)
    return judge(instance.fail_to_pass, instance.pass_to_pass, parse_pytest(run.output))


async def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings()
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

    sandbox = DockerSandbox(command=args.docker)
    if not args.no_tests and not await sandbox.available():
        print(
            f"docker 不可用（命令：{args.docker}）。加 --no-tests 可以只跑 agent。", file=sys.stderr
        )
        return 2

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    directory = args.out / stamp
    runner = InstanceRunner(settings, directory / "work", policy=args.policy)

    results: list[Result] = []
    for number, instance in enumerate(instances, start=1):
        print(f"[{number}/{len(instances)}] {instance.instance_id}", flush=True)
        attempt = await runner.run(instance)
        outcome = await score(sandbox, instance, attempt, args.no_tests)
        results.append(result_for(instance, attempt, outcome))
        mark = "✓" if outcome.resolved else "✗"
        print(
            f"    {mark} {attempt.status}  {attempt.steps} 步  "
            f"${attempt.cost_usd:.4f}  {outcome.summary()}",
            flush=True,
        )

    summary = summarise(results, policy=args.policy, model=settings.llm_model)
    write(directory, results, summary)
    print()
    print(render(summary))
    print(f"\n报告写入 {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
