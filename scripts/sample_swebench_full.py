#!/usr/bin/env python3
"""Reproducible stratified sampling for SWE-bench full JSONL data.

SWE-bench full (HuggingFace "princeton-nlp/SWE-bench") contains roughly
2300 instances from 12+ repos.  Unlike SWE-bench Verified, it has no
"difficulty" column, so stratification is done by **repo** only.

Sampling strategy
-----------------
1. Group instances by "repo".  If the number of repos is below
   "--target-size", every repo keeps at least one instance; otherwise repos
   with the most instances are preferred (same proportional allocation as the
   Verified script).
2. Inside each repo, instances are selected uniformly at random.
3. "random.Random(seed)" keeps every run reproducible; the output is sorted
   by "instance_id" so diffs stay stable.

Command line example
--------------------
.. code-block:: console

    $ python scripts/sample_swebench_full.py \
        --input swebench-full.jsonl \
        --output swebench-full-frozen-50.jsonl \
        --target-size 50 \
        --seed 42

Every output row keeps its original fields and adds "sample_seed" and
"sampled: true".
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_TARGET_SIZE = 50
DEFAULT_SEED = 42
_DEFAULT_REPO = "unknown-repo"


def _read_records(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of dict rows (blank lines are skipped)."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number} of {path}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number} of {path} is not a JSON object")
            records.append(row)
    return records


def _row_value(row: Mapping[str, Any], column: str, default: str) -> str:
    value = row.get(column, default)
    return default if value is None else str(value)


def _group_by(
    records: Sequence[Mapping[str, Any]],
    repo_col: str,
) -> dict[str, list[dict[str, Any]]]:
    """Group rows by repo, preserving input order inside each group."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        repo = _row_value(row, repo_col, _DEFAULT_REPO)
        groups.setdefault(repo, []).append(dict(row))
    return groups


def _allocate_repo_quotas(
    repo_sizes: Mapping[str, int],
    quota: int,
    rng: random.Random,
) -> dict[str, int]:
    """Distribute "quota" slots among repos proportionally to their size.

    Uses largest-remainder proportional allocation (same as the Verified
    script) so that larger repos get more slots while guaranteeing every repo
    at least one slot when "quota >= len(repo_sizes)".
    """
    repos = sorted(repo_sizes)
    total = sum(repo_sizes.values())
    if quota <= 0:
        return {repo: 0 for repo in repos}
    quota = min(quota, total)

    # floor
    result: dict[str, int] = {}
    remainders: dict[str, float] = {}
    for repo in repos:
        exact = quota * repo_sizes[repo] / total
        result[repo] = int(exact)
        remainders[repo] = exact - result[repo]

    remaining = quota - sum(result.values())

    # give one to every repo first if possible
    need_one = [repo for repo in repos if result[repo] == 0]
    while remaining > 0 and need_one:
        result[need_one.pop()] += 1
        remaining -= 1

    # largest remainder for the rest
    order = list(repos)
    rng.shuffle(order)
    order.sort(key=lambda repo: remainders[repo], reverse=True)
    for repo in order:
        if remaining <= 0:
            break
        result[repo] += 1
        remaining -= 1

    return result


def _enrich(records: Sequence[Mapping[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Add the "sample_seed" / "sampled" fields to each sampled row."""
    enriched: list[dict[str, Any]] = []
    for row in records:
        output = dict(row)
        output["sample_seed"] = seed
        output["sampled"] = True
        enriched.append(output)
    return enriched


def _write_records(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    """Atomically write rows as JSONL (UTF-8, sorted keys)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def sample_swebench_full(
    input_jsonl: Path,
    output_jsonl: Path,
    *,
    target_size: int = DEFAULT_TARGET_SIZE,
    seed: int = DEFAULT_SEED,
    repo_col: str = "repo",
    exclude_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Sample a frozen, repo-stratified subset from a SWE-bench full JSONL file.

    Args:
        input_jsonl: Path to the input JSONL (one instance per line).
        output_jsonl: Path where the sampled JSONL is written.
        target_size: Desired number of sampled instances.  If it exceeds the
            input size every input row is kept and a warning is printed to
            stderr; an empty input file raises "ValueError".
        seed: RNG seed; the same seed always yields the same subset.
        repo_col: Column holding the repo name.

    Returns:
        The selected rows (also written to "output_jsonl"), enriched with
        "sample_seed" and "sampled: true" and sorted by "instance_id".
    """
    records = _read_records(input_jsonl)
    if exclude_ids:
        before = len(records)
        records = [r for r in records if r.get("instance_id", "") not in exclude_ids]
        skipped = before - len(records)
        if skipped:
            print(f"excluded {skipped} instances via --exclude-ids, {len(records)} remaining", file=sys.stderr)
    if not records:
        raise ValueError(f"input file {input_jsonl} contains no rows")
    if target_size > len(records):
        print(
            f"warning: target_size={target_size} exceeds the {len(records)} input "
            f"rows; keeping all rows",
            file=sys.stderr,
        )
    target_size = max(0, min(target_size, len(records)))
    selected: list[dict[str, Any]] = []
    if target_size > 0:
        rng = random.Random(seed)  # noqa: S311 - reproducibility, not security
        repos = _group_by(records, repo_col)
        repo_quotas = _allocate_repo_quotas(
            {name: len(group) for name, group in repos.items()},
            target_size,
            rng,
        )
        for repo in sorted(repo_quotas):
            group = repos[repo]
            count = repo_quotas[repo]
            if count > 0:
                selected.extend(rng.sample(group, min(count, len(group))))
        selected.sort(key=lambda row: _row_value(row, "instance_id", ""))
    enriched = _enrich(selected, seed)
    _write_records(output_jsonl, enriched)
    return enriched


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="SWE-bench full JSONL input",
    )
    argument_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="sampled JSONL output",
    )
    argument_parser.add_argument(
        "--target-size",
        type=int,
        default=DEFAULT_TARGET_SIZE,
        help=f"number of instances to sample (default: {DEFAULT_TARGET_SIZE})",
    )
    argument_parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"RNG seed for reproducibility (default: {DEFAULT_SEED})",
    )
    argument_parser.add_argument(
        "--exclude-ids",
        type=Path,
        default=None,
        help="Path to a text file with instance IDs to exclude (one per line)",
    )
    return argument_parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    exclude_ids: set[str] | None = None
    if args.exclude_ids:
        exclude_ids = {
            line.strip()
            for line in args.exclude_ids.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    selected = sample_swebench_full(
        args.input,
        args.output,
        target_size=args.target_size,
        seed=args.seed,
        exclude_ids=exclude_ids,
    )
    print(f"sampled {len(selected)} instances -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
