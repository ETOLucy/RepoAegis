#!/usr/bin/env python3
"""Generate reproducible difficulty-stratified instance-id lists (daily 100).

??? v1 ?1.2 ???
----------------------
?1.2 ???????? 8 ??? 25/50 ??? repo ?? + difficulty ?????????
?????????????????????**????????**?????
``difficulty``?easy/medium/hard?? baseline ????``resolved_by_baseline`` /
``baseline_pass_rate`` ??????? SWE-bench Verified ???????????
``random.Random(seed)`` ?????????? easy 20 / medium 40 / hard 40 ?
``daily_100_ids.txt``??? 100 ?????? Inspect / ?? harness ????????

??????
------------
1. ``difficulty`` ???? easy/medium/hard??? Easy/Medium/Hard ?????
2. ? ``difficulty`` ?? ``resolved_by_baseline`` / ``baseline_pass_rate`` /
   ``pass_rate`` / ``resolution_rate`` ?????????????
   ``>= 0.6`` easy?``0.3 ~ 0.6`` medium?``< 0.3`` hard?
3. ?????? repo ??????? repo ????????? stderr ??
   ``difficulty absent, using repo proxy``?

??
----
* ``daily_100_ids.txt``????? ``instance_id``?? easy/medium/hard ????????
* ``layers.json``?seed?targets ????????
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

DEFAULT_TARGETS = {"easy": 20, "medium": 40, "hard": 40}
DEFAULT_SEED = 42
LAYERS = ("easy", "medium", "hard")

#: Ratio-like columns accepted when ``difficulty`` is absent.
_RATIO_KEYS = (
    "resolved_by_baseline",
    "baseline_pass_rate",
    "pass_rate",
    "resolution_rate",
    "pass@1",
)
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
                raise ValueError(
                    f"invalid JSON on line {line_number} of {path}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number} of {path} is not a JSON object")
            if not row.get("instance_id"):
                raise ValueError(f"line {line_number} of {path} has no instance_id")
            records.append(row)
    return records


def _normalize_difficulty(value: Any) -> str | None:
    """Normalize a difficulty value to easy/medium/hard or ``None``."""
    if value is None:
        return None
    label = str(value).strip().lower()
    return label if label in LAYERS else None


def _ratio_of(row: Mapping[str, Any]) -> float | None:
    """Extract a baseline resolvability ratio from a row, or ``None``."""
    for key in _RATIO_KEYS:
        if key not in row or row[key] is None:
            continue
        value = row[key]
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except ValueError:
            continue
    return None


def _classify_by_ratio(ratio: float) -> str:
    """Map a baseline resolvability ratio to a difficulty layer."""
    if ratio >= 0.6:
        return "easy"
    if ratio >= 0.3:
        return "medium"
    return "hard"


def _repo_of(row: Mapping[str, Any]) -> str:
    repo = row.get("repo")
    return _DEFAULT_REPO if repo is None else str(repo)


def _instance_id(row: Mapping[str, Any]) -> str:
    return str(row["instance_id"])


def _repo_proxy_layers(records: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Shard sorted repos round-robin into easy/medium/hard (stable 3-way split)."""
    repos = sorted({_repo_of(row) for row in records})
    return {repo: LAYERS[index % len(LAYERS)] for index, repo in enumerate(repos)}


def _bucketize(
    records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], str]:
    """Bucket records into easy/medium/hard; returns (buckets, mode)."""
    buckets: dict[str, list[dict[str, Any]]] = {layer: [] for layer in LAYERS}
    repo_layers = _repo_proxy_layers(records)
    has_difficulty = any(
        _normalize_difficulty(row.get("difficulty")) is not None for row in records
    )
    has_ratio = any(_ratio_of(row) is not None for row in records)

    if has_difficulty:
        for row in records:
            layer = _normalize_difficulty(row.get("difficulty"))
            if layer is None:
                ratio = _ratio_of(row)
                layer = (
                    _classify_by_ratio(ratio)
                    if ratio is not None
                    else repo_layers[_repo_of(row)]
                )
            buckets[layer].append(dict(row))
        return buckets, "difficulty"

    if has_ratio:
        for row in records:
            ratio = _ratio_of(row)
            layer = (
                _classify_by_ratio(ratio)
                if ratio is not None
                else repo_layers[_repo_of(row)]
            )
            buckets[layer].append(dict(row))
        return buckets, "ratio"

    for row in records:
        buckets[repo_layers[_repo_of(row)]].append(dict(row))
    return buckets, "repo"


def gen_layer_ids(
    input_jsonl: Path,
    *,
    target_easy: int = DEFAULT_TARGETS["easy"],
    target_medium: int = DEFAULT_TARGETS["medium"],
    target_hard: int = DEFAULT_TARGETS["hard"],
    seed: int = DEFAULT_SEED,
) -> dict[str, list[str]]:
    """Stratify SWE-bench Verified rows into easy/medium/hard instance-id lists.

    Args:
        input_jsonl: Path to the SWE-bench Verified JSONL (one instance per line).
        target_easy: Desired number of easy instances (20 by default).
        target_medium: Desired number of medium instances (40 by default).
        target_hard: Desired number of hard instances (40 by default).
        seed: RNG seed; the same seed always yields the same ids per layer.

    Returns:
        A dict ``{"easy": [...], "medium": [...], "hard": [...]}`` of instance ids,
        each list sorted by ``instance_id``. When a layer holds fewer rows than
        its target, all rows of that layer are kept and a warning is printed to
        stderr. An empty input file raises ``ValueError``.
    """
    records = _read_records(input_jsonl)
    if not records:
        raise ValueError(f"input file {input_jsonl} contains no rows")

    buckets, mode = _bucketize(records)
    if mode == "repo":
        print("warning: difficulty absent, using repo proxy", file=sys.stderr)

    targets = {
        "easy": max(0, target_easy),
        "medium": max(0, target_medium),
        "hard": max(0, target_hard),
    }
    selected: dict[str, list[str]] = {}
    for layer in LAYERS:
        pool = buckets[layer]
        target = targets[layer]
        if len(pool) < target:
            print(
                f"warning: {layer} layer has {len(pool)} instances, "
                f"below target_{layer}={target}; keeping all",
                file=sys.stderr,
            )
            selected[layer] = sorted(_instance_id(row) for row in pool)
        elif target <= 0:
            selected[layer] = []
        else:
            rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not crypto
            picked = rng.sample(pool, target)
            selected[layer] = sorted(_instance_id(row) for row in picked)
    return selected


def _write_outputs(
    output_dir: Path,
    layers: dict[str, list[str]],
    *,
    seed: int,
    targets: Mapping[str, int],
) -> None:
    """Write ``daily_100_ids.txt`` and ``layers.json`` under ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ids_path = output_dir / "daily_100_ids.txt"
    temporary = output_dir / ".daily_100_ids.txt.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        for layer in LAYERS:
            for instance_id in layers[layer]:
                handle.write(instance_id + "\n")
    os.replace(temporary, ids_path)

    layers_path = output_dir / "layers.json"
    temporary_json = output_dir / ".layers.json.tmp"
    payload = {
        "seed": seed,
        "targets": dict(targets),
        "layers": layers,
    }
    temporary_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_json, layers_path)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="SWE-bench Verified JSONL input",
    )
    argument_parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory for daily_100_ids.txt and layers.json",
    )
    argument_parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"RNG seed for reproducibility (default: {DEFAULT_SEED})",
    )
    for layer in LAYERS:
        argument_parser.add_argument(
            f"--target-{layer}",
            type=int,
            default=DEFAULT_TARGETS[layer],
            help=f"target count for the {layer} layer (default: {DEFAULT_TARGETS[layer]})",
        )
    return argument_parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    targets = {layer: getattr(args, f"target_{layer}") for layer in LAYERS}
    layers = gen_layer_ids(
        args.input,
        target_easy=targets["easy"],
        target_medium=targets["medium"],
        target_hard=targets["hard"],
        seed=args.seed,
    )
    _write_outputs(args.output_dir, layers, seed=args.seed, targets=targets)
    total = sum(len(ids) for ids in layers.values())
    print(
        f"wrote {total} instance ids -> {args.output_dir / 'daily_100_ids.txt'} "
        f"(easy={len(layers['easy'])} medium={len(layers['medium'])} "
        f"hard={len(layers['hard'])})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
