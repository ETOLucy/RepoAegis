"""Prepare a local Inspect-readable SWE-bench dataset from the cached HF parquet.

No network access is required: the dataset split is read from the local
HuggingFace cache and exported as JSONL with all metadata the Inspect
swe_bench scorer needs.

Supports SWE-bench Verified (default) and SWE-bench Full via --dataset.
The parquet cache path can be overridden with --parquet-cache or the
SWE_BENCH_PARQUET_CACHE environment variable.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]

# HuggingFace cache directory patterns for each dataset variant.
_DATASET_PATTERNS: dict[str, str] = {
    "verified": (
        "~/.cache/huggingface/hub/datasets--princeton-nlp--SWE-bench_Verified/"
        "snapshots/*/data/test-*-of-*.parquet"
    ),
    "full": (
        "~/.cache/huggingface/hub/datasets--princeton-nlp--SWE-bench/"
        "snapshots/*/data/test-*-of-*.parquet"
    ),
}

REQUIRED_COLUMNS = (
    "repo",
    "instance_id",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "hints_text",
    "created_at",
    "version",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "environment_setup_commit",
)


def _resolve_parquet_path(
    dataset: str,
    explicit_path: str | None,
) -> Path:
    """Resolve the parquet file path.

    Priority:
    1. `--parquet-cache` CLI argument (explicit_path).
    2. `SWE_BENCH_PARQUET_CACHE` environment variable.
    3. Glob pattern for the selected dataset.
    """
    # 1. User-supplied CLI argument.
    if explicit_path is not None:
        return Path(explicit_path)

    # 2. Environment variable override.
    env_path = os.environ.get("SWE_BENCH_PARQUET_CACHE")
    if env_path:
        return Path(env_path)

    # 3. Glob for the dataset.
    pattern = os.path.expanduser(_DATASET_PATTERNS[dataset])
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise SystemExit(
            f"no parquet files found matching '{pattern}'. "
            f"Use --parquet-cache to specify the path manually, or "
            f"set SWE_BENCH_PARQUET_CACHE."
        )
    if len(matches) > 1:
        print(f"warning: multiple parquet files matched, using first: {matches[0]}")
    return Path(matches[0])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export SWE-bench parquet cache to Inspect JSONL."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--dataset",
        choices=("verified", "full"),
        default="verified",
        help="Which SWE-bench split to export (default: verified).",
    )
    parser.add_argument(
        "--parquet-cache",
        type=str,
        default=None,
        help="Path to the parquet file (overrides SWE_BENCH_PARQUET_CACHE and dataset default).",
    )
    parser.add_argument(
        "--ids",
        nargs="*",
        default=None,
        help="Restrict to these instance ids (default: all tasks).",
    )
    args = parser.parse_args()

    parquet_path = _resolve_parquet_path(args.dataset, args.parquet_cache)
    print(f"reading {parquet_path} (dataset={args.dataset})")
    table = pq.read_table(parquet_path)

    missing = [c for c in REQUIRED_COLUMNS if c not in table.column_names]
    if missing:
        raise SystemExit(f"missing columns: {missing}")

    rows = []
    for record in table.to_pylist():
        if args.ids and record["instance_id"] not in args.ids:
            continue
        rows.append(record)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for record in rows:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
