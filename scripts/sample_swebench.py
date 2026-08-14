#!/usr/bin/env python3
"""Reproducible stratified sampling for SWE-bench Verified JSONL data.

SWE-bench Verified ships roughly 500 instances grouped by ``repo`` with an
uneven ``difficulty`` (easy / medium / hard) spread.  This script draws a
small, reproducible "frozen" subset that keeps repo coverage and approximates
the overall difficulty mix, giving a principled path from the current 8 frozen
tasks up to 25 / 50 task subsets.

Sampling strategy
-----------------
1. Group instances by ``repo`` (column ``repo``).  If the number of repos is
   below ``--target-size`` every repo keeps at least one instance; otherwise
   the repos with the most instances are preferred.
2. Inside each repo, difficulty quotas follow the overall difficulty shares of
   the *input* file.  Quotas use a seeded largest-remainder pass (floor first,
   remainder assigned by the seeded RNG) and are capped at what each repo
   actually contains.
3. ``random.Random(seed)`` keeps every run reproducible; the output is sorted
   by ``instance_id`` so diffs stay stable.

Command line example
--------------------
.. code-block:: console

    $ python scripts/sample_swebench.py \
        --input swebench-verified.jsonl \
        --output swebench-frozen-50.jsonl \
        --target-size 50 \
        --seed 42

Every output row keeps its original fields and adds ``sample_seed`` and
``sampled: true``.
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
_DEFAULT_DIFFICULTY = "unknown"


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
                raise ValueError(
                    f"invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number} of {path} is not a JSON object")
            records.append(row)
    return records


def _row_value(row: Mapping[str, Any], column: str, default: str) -> str:
    value = row.get(column, default)
    return default if value is None else str(value)


def _difficulty_proportions(
    records: Sequence[Mapping[str, Any]],
    difficulty_col: str,
) -> dict[str, float]:
    """Return the overall difficulty share of each label in ``records``."""
    counts: dict[str, int] = {}
    for row in records:
        difficulty = _row_value(row, difficulty_col, _DEFAULT_DIFFICULTY)
        counts[difficulty] = counts.get(difficulty, 0) + 1
    total = sum(counts.values())
    return {difficulty: count / total for difficulty, count in counts.items()}


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


def _allocate_capped(
    bins: Sequence[str],
    weights: Mapping[str, float],
    caps: Mapping[str, int],
    quota: int,
    rng: random.Random,
) -> dict[str, int]:
    """Distribute ``quota`` slots among ``bins`` proportionally to ``weights``.

    A seeded largest-remainder pass first assigns the floor of each weighted
    share, then hands leftover slots to the largest fractional remainders (tie
    order decided by ``rng``).  Each bin is capped at ``caps`` and overflow is
    redistributed to bins that still have capacity, so the returned quotas sum
    to ``min(quota, sum(caps))``.
    """
    bins = sorted(bins)
    total_cap = sum(caps[bin_] for bin_ in bins)
    quota = min(quota, total_cap)
    if quota <= 0:
        return {bin_: 0 for bin_ in bins}

    total_weight = sum(weights.get(bin_, 0.0) for bin_ in bins)
    if total_weight <= 0.0:
        total_weight = 1.0
    exact = {bin_: quota * weights.get(bin_, 0.0) / total_weight for bin_ in bins}
    result = {bin_: int(exact[bin_]) for bin_ in bins}
    remaining = quota - sum(result.values())

    order = list(bins)
    rng.shuffle(order)
    order.sort(key=lambda bin_: exact[bin_] - result[bin_], reverse=True)
    for bin_ in order:
        if remaining <= 0:
            break
        result[bin_] += 1
        remaining -= 1

    overflow = 0
    for bin_ in bins:
        if result[bin_] > caps[bin_]:
            overflow += result[bin_] - caps[bin_]
            result[bin_] = caps[bin_]
    while overflow > 0:
        candidates = [bin_ for bin_ in order if result[bin_] < caps[bin_]]
        if not candidates:
            break
        rng.shuffle(candidates)
        for bin_ in candidates:
            if overflow <= 0:
                break
            result[bin_] += 1
            overflow -= 1
    return result


def _allocate_repo_quotas(
    sizes: Mapping[str, int],
    target_size: int,
    rng: random.Random,
) -> dict[str, int]:
    """Allocate the per-repo sampling budget.

    With fewer repos than ``target_size`` every repo keeps at least one
    instance and the remaining budget is spread proportionally to repo size.
    Otherwise only the repos with the most instances are kept (one each).
    """
    names = sorted(sizes)
    total = sum(sizes.values())
    if target_size >= total:
        return dict(sizes)
    if target_size <= len(names):
        order = list(names)
        rng.shuffle(order)
        order.sort(key=lambda name: sizes[name], reverse=True)
        return {name: 1 for name in order[:target_size]}
    caps = {name: sizes[name] - 1 for name in names}
    extra = _allocate_capped(names, sizes, caps, target_size - len(names), rng)
    return {name: 1 + extra[name] for name in names}


def _enrich(records: Sequence[Mapping[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Add the ``sample_seed`` / ``sampled`` fields to each sampled row."""
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


def sample_swebench(
    input_jsonl: Path,
    output_jsonl: Path,
    *,
    target_size: int = DEFAULT_TARGET_SIZE,
    seed: int = DEFAULT_SEED,
    difficulty_col: str = "difficulty",
    repo_col: str = "repo",
) -> list[dict[str, Any]]:
    """Sample a frozen, stratified subset from a SWE-bench Verified JSONL file.

    Args:
        input_jsonl: Path to the input JSONL (one instance per line).
        output_jsonl: Path where the sampled JSONL is written.
        target_size: Desired number of sampled instances.  If it exceeds the
            input size every input row is kept and a warning is printed to
            stderr; an empty input file raises ``ValueError``.
        seed: RNG seed; the same seed always yields the same subset.
        difficulty_col: Column holding the difficulty label.
        repo_col: Column holding the repo name.

    Returns:
        The selected rows (also written to ``output_jsonl``), enriched with
        ``sample_seed`` and ``sampled: true`` and sorted by ``instance_id``.
    """
    records = _read_records(input_jsonl)
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
        overall_props = _difficulty_proportions(records, difficulty_col)
        repos = _group_by(records, repo_col)
        repo_quotas = _allocate_repo_quotas(
            {name: len(group) for name, group in repos.items()},
            target_size,
            rng,
        )
        for repo in sorted(repo_quotas):
            group = repos[repo]
            avail: dict[str, int] = {}
            for row in group:
                difficulty = _row_value(row, difficulty_col, _DEFAULT_DIFFICULTY)
                avail[difficulty] = avail.get(difficulty, 0) + 1
            difficulty_quotas = _allocate_capped(
                list(avail),
                overall_props,
                avail,
                repo_quotas[repo],
                rng,
            )
            for difficulty in sorted(difficulty_quotas):
                pool = [
                    row
                    for row in group
                    if _row_value(row, difficulty_col, _DEFAULT_DIFFICULTY)
                    == difficulty
                ]
                count = difficulty_quotas[difficulty]
                if count:
                    selected.extend(rng.sample(pool, count))
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
        help="SWE-bench Verified JSONL input",
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
    return argument_parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    selected = sample_swebench(
        args.input,
        args.output,
        target_size=args.target_size,
        seed=args.seed,
    )
    print(f"sampled {len(selected)} instances -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
