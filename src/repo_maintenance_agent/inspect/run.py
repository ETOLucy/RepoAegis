"""Run the Inspect grading harness for RepoAegis on SWE-bench Verified.

Replay mode (default): apply a previously generated official-format prediction
JSONL inside the Inspect Docker sandbox and score it with Inspect's official
``swe_bench_scorer`` (no model calls, free). Generate mode: call the real
RepoAegis pipeline to produce a patch, then score it the same way (costs API
calls).

Usage (from the RepoAegis repo root, with the RepoAegis venv python):

  export PYTHONUTF8=1
  export PYTHONPATH="$PWD/src"
  .venv/Scripts/python.exe -m repo_maintenance_agent.inspect.run \
    --dataset data/verified.jsonl \
    --replay /path/to/predictions.jsonl \
    --sample-id django__django-13568 --allow-internet
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from inspect_ai import eval

from repo_maintenance_agent.inspect.windows_shims import install_windows_shims

# swebench grading reads test logs with the locale default encoding; on Windows
# that is GBK and UTF-8 logs crash. Force UTF-8 everywhere.
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

install_windows_shims()

_PACKAGE_ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset", type=Path, default=_PACKAGE_ROOT / "data" / "verified.jsonl"
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=None,
        help="Replay an official-format prediction JSONL (no model calls).",
    )
    parser.add_argument(
        "--sample-id",
        action="append",
        default=None,
        help="Only evaluate these sample ids (repeatable).",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Only evaluate the first N samples."
    )
    parser.add_argument("--max-connections", type=int, default=1)
    parser.add_argument(
        "--allow-internet",
        action="store_true",
        help="Let sandboxes reach the internet (needed by eval scripts that pip-install).",
    )
    parser.add_argument("--log-dir", type=Path, default=_PACKAGE_ROOT / "logs")
    args = parser.parse_args()

    from repo_maintenance_agent.inspect.pilot_task import repoaegis_verified

    task = repoaegis_verified(
        dataset=str(args.dataset),
        predictions_path=str(args.replay) if args.replay else None,
        allow_internet=args.allow_internet,
    )
    result = eval(
        task,
        model="mockllm/noop",
        sample_id=args.sample_id,
        limit=args.limit,
        max_connections=args.max_connections,
        log_dir=str(args.log_dir),
    )
    for log in result:
        print(f"status={log.status}")
        # inspect_ai 0.3.x: per-sample scores live on EvalLog.reductions.
        if log.reductions:
            for reduction in log.reductions:
                for sample_score in reduction.samples:
                    print(f"  {sample_score.sample_id}: score={sample_score.value}")
        elif log.results:
            print(
                f"  aggregate: total={log.results.total_samples} "
                f"completed={log.results.completed_samples}"
            )
        elif log.error:
            print(f"  error: {log.error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())