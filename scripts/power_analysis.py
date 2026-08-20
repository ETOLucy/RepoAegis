#!/usr/bin/env python3
"""Sample-size power analysis CLI for the RepoAegis quantized-eval plan.

Reproduces the power table from plan section 1.1: for each baseline->candidate
pass-rate improvement it reports Cohen's h and the per-arm sample size needed
at power=0.80 / alpha=0.05 (paired-design normal approximation, see
``required_n_for_power`` in ``repo_maintenance_agent.evaluation.significance``).

Pure standard library. Run without arguments to print the table::

    python scripts/power_analysis.py
    python scripts/power_analysis.py --p1 0.30 --p2 0.40 --power 0.80
"""

from __future__ import annotations

import argparse

from repo_maintenance_agent.evaluation.significance import cohens_h, required_n_for_power

# (baseline, candidate) pass-rate pairs from the plan section 1.1 scenarios.
SCENARIOS: tuple[tuple[float, float], ...] = (
    (0.30, 0.36),  # +6 points
    (0.30, 0.40),  # +10 points
    (0.30, 0.48),  # +18 points
    (0.30, 0.55),  # +25 points
)


def power_table(*, power: float = 0.80, alpha: float = 0.05) -> str:
    """Render the plan section 1.1 power table as a markdown string."""
    header = f"| p1 | p2 | ?? | Cohen's h | ?? n?power={power:.2f}, alpha={alpha:.2f}? |"
    rows = ["|---|---|---|---|---|"]
    for p1, p2 in SCENARIOS:
        h = cohens_h(p1, p2)
        n = required_n_for_power(p1, p2, power=power, alpha=alpha)
        points = round((p2 - p1) * 100)
        rows.append(f"| {p1:.2f} | {p2:.2f} | +{points} ? | {h:.3f} | {n} |")
    return "\n".join([header, *rows])


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; prints the table or a single-scenario conclusion."""
    parser = argparse.ArgumentParser(
        description=(
            "Sample size needed to detect a pass-rate improvement with a "
            "paired design (normal approximation)."
        )
    )
    parser.add_argument("--p1", type=float, help="baseline pass rate in [0, 1]")
    parser.add_argument("--p2", type=float, help="candidate pass rate in [0, 1]")
    parser.add_argument("--power", type=float, default=0.80, help="target power (default: 0.80)")
    args = parser.parse_args(argv)

    if args.p1 is None and args.p2 is None:
        print(power_table(power=args.power))
        return 0
    if args.p1 is None or args.p2 is None:
        parser.error("--p1 and --p2 must be provided together")
    h = cohens_h(args.p1, args.p2)
    n = required_n_for_power(args.p1, args.p2, power=args.power)
    print(
        f"p1={args.p1:.2f} -> p2={args.p2:.2f}: "
        f"Cohen's h={h:.3f}, required n (power={args.power:.2f}) = {n}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
