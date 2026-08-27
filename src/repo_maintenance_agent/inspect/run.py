"""Run the Inspect grading harness for RepoAegis on SWE-bench Verified.

Replay mode (default): apply a previously generated official-format prediction
JSONL inside the Inspect Docker sandbox and score it with Inspect's official
``swe_bench_scorer`` (no model calls, free).

Generate mode: call the real RepoAegis pipeline to produce a patch, then score
it the same way (costs API calls).  Use --repoaegis-root and related args to
configure the generate mode.

Usage (from the RepoAegis repo root, with the RepoAegis venv python):

  export PYTHONUTF8=1
  export PYTHONPATH="$PWD/src"
  .venv/Scripts/python.exe -m repo_maintenance_agent.inspect.run \
    --dataset data/verified.jsonl \
    --replay /path/to/predictions.jsonl \
    --sample-id django__django-13568 --allow-internet

Generate mode example:

  .venv/Scripts/python.exe -m repo_maintenance_agent.inspect.run \
    --dataset data/verified.jsonl \
    --repoaegis-root . \
    --cc-switch-db /path/to/cc_switch.db \
    --task-root /tmp/swe-tasks \
    --model-alias deepseek-chat \
    --protocol-digest abc123 \
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
    parser = argparse.ArgumentParser(
        description="Run RepoAegis Inspect grading harness (replay or generate)."
    )
    parser.add_argument("--dataset", type=Path, default=_PACKAGE_ROOT / "data" / "verified.jsonl")
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

    # Generate-mode arguments (all optional; omitted => replay only).
    gen_group = parser.add_argument_group("generate mode")
    gen_group.add_argument(
        "--repoaegis-root",
        type=Path,
        default=None,
        help="Path to the RepoAegis repository root.",
    )
    gen_group.add_argument(
        "--locators",
        nargs="*",
        default=None,
        help="Repository locators as key=value pairs (repeatable).",
    )
    gen_group.add_argument(
        "--cc-switch-db",
        type=Path,
        default=None,
        help="Path to the CC Switch SQLite database.",
    )
    gen_group.add_argument(
        "--task-root",
        type=Path,
        default=None,
        help="Temporary directory for per-task artifacts, workspaces, etc.",
    )
    gen_group.add_argument(
        "--model-alias",
        type=str,
        default=None,
        help="Model alias to use (e.g. deepseek-chat).",
    )
    gen_group.add_argument(
        "--api-style",
        type=str,
        default="chat-json",
        help="API style (default: chat-json).",
    )
    gen_group.add_argument(
        "--protocol-digest",
        type=str,
        default=None,
        help="Protocol digest string for the RepoAegis pipeline.",
    )
    gen_group.add_argument(
        "--arm",
        type=str,
        default="candidate",
        help="Arm identifier (default: candidate).",
    )
    gen_group.add_argument(
        "--maximum-call-cost-cny",
        type=str,
        default="0.5",
        help="Maximum cost per model call in CNY (default: 0.5).",
    )
    gen_group.add_argument(
        "--config",
        type=int,
        nargs=4,
        default=None,
        metavar=("MAX_ITER", "CTX_ROUNDS", "TOOL_CALLS", "PATCH_ATTEMPTS"),
        help="Configuration tuple: max_iterations context_rounds tool_calls patch_attempts.",
    )

    args = parser.parse_args()

    # Parse locators from key=value pairs.
    locators: dict[str, str] | None = None
    if args.locators:
        locators = {}
        for kv in args.locators:
            k, _, v = kv.partition("=")
            locators[k] = v

    from repo_maintenance_agent.inspect.pilot_task import repoaegis_verified

    task = repoaegis_verified(
        dataset=str(args.dataset),
        predictions_path=str(args.replay) if args.replay else None,
        allow_internet=args.allow_internet,
        repoaegis_root=args.repoaegis_root,
        locators=locators,
        cc_switch_db=args.cc_switch_db,
        task_root=args.task_root,
        model_alias=args.model_alias,
        api_style=args.api_style,
        protocol_digest=args.protocol_digest,
        arm=args.arm,
        maximum_call_cost_cny=args.maximum_call_cost_cny,
        rates=None,
        configuration=tuple(args.config) if args.config else None,
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
