#!/usr/bin/env python3
"""Run a deterministic evaluation smoke from fixture observations (no model calls)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from repo_maintenance_agent.evaluation.harness import (
    EvaluationHarness,
    ObservationExecutor,
)
from repo_maintenance_agent.evaluation.models import (
    EvaluationObservation,
    EvaluationProvenance,
    EvaluationRun,
    EvaluationSuite,
)

TENANT_ID = "ci-eval-smoke"
CANDIDATE_LABEL = "eval-smoke"
PROVENANCE = EvaluationProvenance(
    model="fixture-observation",
    provider="deterministic",
    prompt_version="smoke-1",
    tool_schema_version="smoke-1",
    policy_version="smoke-1",
    dataset_version="examples-2026.07.31",
    environment_fingerprint="github-actions-ubuntu-latest",
    seed=20260811,
)


def load_suite(path: Path) -> EvaluationSuite:
    return EvaluationSuite.model_validate_json(path.read_text(encoding="utf-8"))


def load_observations(path: Path) -> dict[str, EvaluationObservation]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return {
        case_id: EvaluationObservation.model_validate(observation)
        for case_id, observation in payload.items()
    }


async def run_smoke(suite_path: Path, observations_path: Path) -> EvaluationRun:
    suite = load_suite(suite_path)
    observations = load_observations(observations_path)
    harness = EvaluationHarness(ObservationExecutor(observations))
    return await harness.run(
        tenant_id=TENANT_ID,
        suite=suite,
        candidate_label=CANDIDATE_LABEL,
        provenance=PROVENANCE,
    )


def print_report(run: EvaluationRun) -> None:
    aggregate = run.aggregate
    if aggregate is None:
        raise RuntimeError("evaluation run produced no aggregate")
    print(f"run_id={run.run_id} status={run.status.value}")
    for result in run.results:
        report = result.report
        resolution = f"{report.issue_resolution:.2f}" if report is not None else "n/a"
        print(
            f"case {result.case_id}: failure={result.failure_category.value} "
            f"resolution={resolution} attempts={result.attempts}"
        )
    print(
        f"aggregate cases={aggregate.case_count} "
        f"resolution_rate={aggregate.resolution_rate:.4f} "
        f"recall_at_10={aggregate.relevant_file_recall_at_10:.4f} "
        f"mrr={aggregate.mrr:.4f} "
        f"unauthorized_tool_call_rate={aggregate.unauthorized_tool_call_rate:.4f} "
        f"regression_rate={aggregate.regression_rate:.4f}"
    )
    decision = run.gate_decision
    if decision is None:
        raise RuntimeError("evaluation run produced no gate decision")
    for check in decision.checks:
        print(
            f"gate {check.name}: {'pass' if check.passed else 'fail'} "
            f"actual={_display(check.actual)} threshold={_display(check.threshold)}"
        )
    print(f"gate_decision.passed={decision.passed}")


def write_json_report(run: EvaluationRun, path: Path) -> None:
    report = {
        "schema_version": "repoaegis-eval-smoke/v1",
        "evidence_kind": "deterministic-fixture-smoke-not-model-quality",
        "run": run.model_dump(mode="json"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--suite", type=Path, required=True)
    argument_parser.add_argument("--observations", type=Path, required=True)
    argument_parser.add_argument("--json-report", type=Path, required=True)
    return argument_parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = parser().parse_args(arguments)
    run = asyncio.run(run_smoke(args.suite, args.observations))
    print_report(run)
    write_json_report(run, args.json_report)
    decision = run.gate_decision
    if decision is None:
        raise RuntimeError("evaluation run produced no gate decision")
    return 0 if decision.passed else 1


def _display(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())