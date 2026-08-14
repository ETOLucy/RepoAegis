"""Multi-model side-by-side evaluation runner (Model Matrix).

Runs the same ``EvaluationSuite`` once per model with aligned seeds so the
per-model runs are directly comparable, then renders a cost-quality table and
pairwise resolution deltas that can be fed into the statistical gating layer
(e.g. ``paired_bootstrap_delta`` from :mod:`significance`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from repo_maintenance_agent.evaluation.models import (
    EvaluationProvenance,
    EvaluationRun,
    EvaluationSuite,
)

DEFAULT_MATRIX_TENANT_ID = "model-matrix"


@dataclass(frozen=True)
class ModelMatrixResult:
    """One cost-quality row for a single model in the matrix.

    ``total_tokens`` is input + output tokens; estimated cost can be derived
    from token counts once provider pricing is known.
    """

    model: str
    resolution_rate: float
    mean_tests_passed_ratio: float
    mean_latency_ms: float
    total_tokens: int


class ModelMatrixHarness(Protocol):
    """Minimal harness interface required by :func:`run_model_matrix`."""

    async def run(
        self,
        *,
        tenant_id: str,
        suite: EvaluationSuite,
        candidate_label: str,
        provenance: EvaluationProvenance,
        baseline: EvaluationRun | None = None,
        selected_case_ids: tuple[str, ...] = (),
        replay_of_run_id: str | None = None,
    ) -> EvaluationRun: ...


async def run_model_matrix(
    harness: ModelMatrixHarness,
    suite: EvaluationSuite,
    models: Sequence[str],
    *,
    provenance_factory: Callable[[str, int], EvaluationProvenance],
    seed: int = 17,
    selected_case_ids: tuple[str, ...] = (),
) -> dict[str, EvaluationRun]:
    """Run the same suite once per model with an aligned seed.

    Each model is executed through ``harness.run`` with
    ``candidate_label=model`` and ``provenance=provenance_factory(model, seed)``
    so the matrix is seed-aligned and directly comparable. Returns
    ``{model: EvaluationRun}`` keyed by model name.
    """
    runs: dict[str, EvaluationRun] = {}
    for model in models:
        provenance = provenance_factory(model, seed=seed)
        runs[model] = await harness.run(
            tenant_id=DEFAULT_MATRIX_TENANT_ID,
            suite=suite,
            candidate_label=model,
            provenance=provenance,
            selected_case_ids=selected_case_ids,
        )
    return runs


def build_cost_quality_table(runs: dict[str, EvaluationRun]) -> str:
    """Render the cost-quality matrix as a Markdown table.

    Columns are Model | Resolution | Tests ratio | p50 ms | Total tokens,
    sorted by resolution rate in descending order. Total tokens is the sum of
    input and output tokens from the run aggregate.
    """
    if not runs:
        return "No model runs."
    summaries = sorted(
        (_summarize(model, run) for model, run in runs.items()),
        key=lambda summary: summary.resolution_rate,
        reverse=True,
    )
    header = "| Model | Resolution | Tests ratio | p50 ms | Total tokens |"
    separator = "|" + "---|" * 5
    rows: list[str] = []
    for summary in summaries:
        aggregate = runs[summary.model].aggregate
        p50_ms = aggregate.latency_p50_ms if aggregate is not None else 0
        rows.append(
            f"| {summary.model} | {summary.resolution_rate:.1%} | "
            f"{summary.mean_tests_passed_ratio:.1%} | {p50_ms} | "
            f"{summary.total_tokens} |"
        )
    return "\n".join([header, separator, *rows])


def pairwise_deltas(
    runs: dict[str, EvaluationRun],
    *,
    baseline_model: str,
) -> dict[str, float]:
    """Return the resolution-rate delta of every model vs ``baseline_model``.

    The baseline model itself maps to ``0.0``. A missing baseline raises
    ``ValueError`` so callers notice a typo before feeding deltas to the
    statistical gating layer.
    """
    if baseline_model not in runs:
        raise ValueError(
            f"baseline model {baseline_model!r} is not a key of runs"
        )
    baseline_resolution = _resolution(runs[baseline_model])
    return {
        model: _resolution(run) - baseline_resolution
        for model, run in runs.items()
    }


def _resolution(run: EvaluationRun) -> float:
    return run.aggregate.resolution_rate if run.aggregate is not None else 0.0


def _summarize(model: str, run: EvaluationRun) -> ModelMatrixResult:
    aggregate = run.aggregate
    reports = [
        result.report for result in run.results if result.report is not None
    ]
    return ModelMatrixResult(
        model=model,
        resolution_rate=(
            aggregate.resolution_rate if aggregate is not None else 0.0
        ),
        mean_tests_passed_ratio=(
            aggregate.mean_tests_passed_ratio if aggregate is not None else 0.0
        ),
        mean_latency_ms=(
            sum(report.wall_clock_ms for report in reports) / len(reports)
            if reports
            else 0.0
        ),
        total_tokens=(
            aggregate.input_tokens + aggregate.output_tokens
            if aggregate is not None
            else 0
        ),
    )