"""Prepare a local Inspect-readable SWE-bench dataset from the cached HF parquet.

No network access is required: the Verified test split is read from the local
HuggingFace cache and exported as JSONL with all metadata the Inspect
swe_bench scorer needs.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pyarrow.parquet as pq  # type: ignore[import-untyped]

PARQUET_CACHE = Path(
    os.environ.get(
        "SWE_BENCH_PARQUET_CACHE",
        os.path.expanduser(
            "~/.cache/huggingface/hub/datasets--princeton-nlp--SWE-bench_Verified/"
            "snapshots/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/data/test-00000-of-00001.parquet"
        )
    )
)

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--ids",
        nargs="*",
        default=None,
        help="Restrict to these instance ids (default: all 500 Verified tasks).",
    )
    args = parser.parse_args()

    table = pq.read_table(PARQUET_CACHE)
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
