#!/usr/bin/env python3
"""Compare RepoAegis evaluation results against public baseline references.

Reads an evaluate-suite JSON report (``run.aggregate.resolution_rate``, with a
flat ``aggregate.resolution_rate`` fallback for minimal fixtures) and renders a
markdown table with the RepoAegis result alongside public baselines, answering
"absolute score vs. relative improvement over a baseline".

Built-in default baselines (publicly reported reference scores; the underlying
subsets and conditions differ, so they are directional context only):

- SWE-agent (GPT-4o)  ~ 18.0%   (official run 2025)
- Claude 3.5 Sonnet   ~ 46.3%   (2026 public reference, approximate)
- OpenHands / CodeAct ~ 26.0%

> ??????????????????????????????

Command line example
--------------------
.. code-block:: console

    $ python scripts/report_baseline.py \
        --result artifacts/eval-report.json \
        --baselines baselines.csv \
        --output docs/eval-baseline.md

Baselines CSV format (``note`` column is optional)::

    name,resolution_rate,note
    SWE-agent GPT-4o,0.18,official run 2025
    Claude 3.5 Sonnet,0.463,official run 2026
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DEFAULT_BASELINES: dict[str, float] = {
    "Claude 3.5 Sonnet": 0.463,  # 2026 public reference, approximate
    "OpenHands / CodeAct": 0.26,
    "SWE-agent (GPT-4o)": 0.18,  # official run 2025
}

DISCLAIMER = "???? baseline ????????????????????????????????????????????"


def load_result(json_path: Path) -> dict[str, Any]:
    """Read an evaluate-suite JSON report and return its parsed payload."""
    payload: Any = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{json_path} does not contain a JSON object")
    return payload


def extract_resolution_rate(payload: Mapping[str, Any]) -> float:
    """Extract ``resolution_rate`` from a report payload.

    Supports the evaluate-suite layout (``run.aggregate.resolution_rate``) and
    a minimal fixture layout (``aggregate.resolution_rate``).
    """
    aggregate: Any = payload.get("run")
    if isinstance(aggregate, Mapping):
        aggregate = aggregate.get("aggregate")
    if not isinstance(aggregate, Mapping):
        aggregate = payload.get("aggregate")
    if not isinstance(aggregate, Mapping):
        raise ValueError("report payload is missing an 'aggregate' section")
    rate = aggregate.get("resolution_rate")
    if rate is None:
        raise ValueError("aggregate section is missing 'resolution_rate'")
    return float(rate)


def load_baselines(csv_path: Path) -> dict[str, float]:
    """Read a ``name,resolution_rate[,note]`` CSV into ``{name: rate}``."""
    baselines: dict[str, float] = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = {"name", "resolution_rate"} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{csv_path} is missing required columns: {', '.join(sorted(missing))}"
            )
        for row_number, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            raw_rate = (row.get("resolution_rate") or "").strip()
            if not name and not raw_rate:
                continue
            if not name:
                raise ValueError(f"row {row_number} of {csv_path}: empty name")
            try:
                rate = float(raw_rate)
            except ValueError as exc:
                raise ValueError(
                    f"row {row_number} of {csv_path}: invalid resolution_rate {raw_rate!r}"
                ) from exc
            baselines[name] = rate
    return baselines


def render_baseline_table(
    result_rate: float,
    baselines: dict[str, float],
    *,
    result_label: str = "RepoAegis (frozen subset)",
) -> str:
    """Render a markdown table of methods sorted by resolution (descending).

    Columns are ``Method | Resolution | Delta vs RepoAegis``.  The RepoAegis
    row is bolded and its delta column shows ``?``.
    """
    rows: list[tuple[str, float]] = [(result_label, result_rate)]
    rows.extend(sorted(baselines.items(), key=lambda item: item[1], reverse=True))
    rows.sort(key=lambda item: item[1], reverse=True)
    lines = [
        "| Method | Resolution | Delta vs RepoAegis |",
        "|---|---:|---:|",
    ]
    for name, rate in rows:
        method = f"**{name}**" if name == result_label else name
        delta = "?" if name == result_label else f"{rate - result_rate:+.3f}"
        lines.append(f"| {method} | {rate:.3f} | {delta} |")
    lines.append("")
    lines.append(f"> {DISCLAIMER}")
    return "\n".join(lines) + "\n"


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--result",
        type=Path,
        required=True,
        help="evaluate-suite JSON report path",
    )
    argument_parser.add_argument(
        "--baselines",
        type=Path,
        default=None,
        help="optional CSV (name,resolution_rate[,note]); defaults to built-in references",
    )
    argument_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="markdown output path (default: stdout)",
    )
    return argument_parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    result_rate = extract_resolution_rate(load_result(args.result))
    baselines = load_baselines(args.baselines) if args.baselines else dict(DEFAULT_BASELINES)
    markdown = render_baseline_table(result_rate, baselines)
    if args.output is None:
        sys.stdout.write(markdown)
    else:
        args.output.write_text(markdown, encoding="utf-8")
        print(f"wrote baseline report -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
