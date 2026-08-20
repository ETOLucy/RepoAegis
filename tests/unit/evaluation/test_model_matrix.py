"""Unit tests for the multi-model Model Matrix runner."""

from __future__ import annotations

import pytest

from repo_maintenance_agent.evaluation.model_matrix import (
    build_cost_quality_table,
    pairwise_deltas,
    run_model_matrix,
)
from repo_maintenance_agent.evaluation.models import (
    EvaluationAggregate,
    EvaluationCase,
    EvaluationProvenance,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
)


def _case(case_id: str) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        repo_id="owner/repository",
        base_commit="a" * 40,
        gold_files=(f"src/{case_id}.py",),
        hidden_test_commands=(("pytest", case_id),),
        timeout_seconds=2,
    )


def _suite() -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="suite",
        name="Model matrix suite",
        version="v1",
        cases=(_case("case-a"), _case("case-b")),
    )


def _provenance(model: str, seed: int) -> EvaluationProvenance:
    return EvaluationProvenance(
        model=model,
        provider="fixture",
        prompt_version="p1",
        tool_schema_version="t1",
        policy_version="policy1",
        dataset_version="v1",
        environment_fingerprint="test-platform",
        seed=seed,
    )


def _aggregate(
    resolution_rate: float,
    *,
    tests_ratio: float = 1.0,
    p50_ms: int = 10,
    input_tokens: int = 100,
    output_tokens: int = 20,
) -> EvaluationAggregate:
    return EvaluationAggregate(
        case_count=2,
        resolution_rate=resolution_rate,
        relevant_file_recall_at_10=1.0,
        mrr=1.0,
        unauthorized_tool_call_rate=0.0,
        regression_rate=0.0,
        mean_tests_passed_ratio=tests_ratio,
        cache_hit_rate=0.0,
        latency_p50_ms=p50_ms,
        latency_p95_ms=p50_ms,
        model_calls=2,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        terminal_failure_count=0,
    )


def _run(
    model: str,
    *,
    resolution_rate: float,
    tests_ratio: float = 1.0,
    p50_ms: int = 10,
    input_tokens: int = 100,
    output_tokens: int = 20,
    seed: int = 17,
) -> EvaluationRun:
    return EvaluationRun(
        tenant_id="tenant-a",
        suite=_suite(),
        candidate_label=model,
        provenance=_provenance(model, seed),
        status=EvaluationRunStatus.COMPLETED,
        aggregate=_aggregate(
            resolution_rate,
            tests_ratio=tests_ratio,
            p50_ms=p50_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )


class _FakeHarness:
    """Records calls and returns a scripted EvaluationRun per model."""

    def __init__(self, resolutions: dict[str, float] | None = None) -> None:
        self.resolutions = resolutions or {}
        self.calls: list[dict[str, object]] = []

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
    ) -> EvaluationRun:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "suite": suite,
                "candidate_label": candidate_label,
                "provenance": provenance,
                "baseline": baseline,
                "selected_case_ids": selected_case_ids,
                "replay_of_run_id": replay_of_run_id,
            }
        )
        return _run(
            candidate_label,
            resolution_rate=self.resolutions.get(candidate_label, 0.5),
            seed=provenance.seed,
        )


async def test_run_model_matrix_runs_each_model_with_seeded_provenance() -> None:
    harness = _FakeHarness({"alpha": 0.6, "beta": 0.8})
    seen: list[tuple[str, int]] = []

    def provenance_factory(model: str, seed: int) -> EvaluationProvenance:
        seen.append((model, seed))
        return _provenance(model, seed)

    runs = await run_model_matrix(
        harness,
        _suite(),
        ("alpha", "beta"),
        provenance_factory=provenance_factory,
        seed=42,
    )

    assert set(runs) == {"alpha", "beta"}
    assert seen == [("alpha", 42), ("beta", 42)]
    for model, run in runs.items():
        assert run.candidate_label == model
        assert run.provenance.model == model
        assert run.provenance.seed == 42
    assert len(harness.calls) == 2
    assert harness.calls[0]["candidate_label"] == "alpha"
    assert harness.calls[0]["provenance"].seed == 42
    assert harness.calls[0]["selected_case_ids"] == ()
    assert harness.calls[0]["tenant_id"] == "model-matrix"


async def test_run_model_matrix_defaults_seed_to_seventeen() -> None:
    harness = _FakeHarness()

    def provenance_factory(model: str, seed: int) -> EvaluationProvenance:
        return _provenance(model, seed)

    runs = await run_model_matrix(
        harness,
        _suite(),
        ("alpha",),
        provenance_factory=provenance_factory,
    )

    assert runs["alpha"].provenance.seed == 17


async def test_run_model_matrix_forwards_selected_case_ids() -> None:
    harness = _FakeHarness()

    def provenance_factory(model: str, seed: int) -> EvaluationProvenance:
        return _provenance(model, seed)

    await run_model_matrix(
        harness,
        _suite(),
        ("alpha",),
        provenance_factory=provenance_factory,
        selected_case_ids=("case-a",),
    )

    assert harness.calls[0]["selected_case_ids"] == ("case-a",)


def test_build_cost_quality_table_sorted_by_resolution_descending() -> None:
    runs = {
        "alpha": _run(
            "alpha",
            resolution_rate=0.6,
            tests_ratio=0.75,
            p50_ms=30,
            input_tokens=200,
            output_tokens=50,
        ),
        "beta": _run(
            "beta",
            resolution_rate=1.0,
            tests_ratio=1.0,
            p50_ms=10,
            input_tokens=100,
            output_tokens=20,
        ),
        "gamma": _run(
            "gamma",
            resolution_rate=0.8,
            tests_ratio=0.9,
            p50_ms=20,
            input_tokens=150,
            output_tokens=30,
        ),
    }

    table = build_cost_quality_table(runs)
    lines = table.splitlines()

    assert lines[0] == "| Model | Resolution | Tests ratio | p50 ms | Total tokens |"
    assert lines[1] == "|" + "---|" * 5
    data_rows = lines[2:]
    models = [line.split("|")[1].strip() for line in data_rows]
    assert models == ["beta", "gamma", "alpha"]
    assert "| beta | 100.0% | 100.0% | 10 | 120 |" in table
    assert "| gamma | 80.0% | 90.0% | 20 | 180 |" in table
    assert "| alpha | 60.0% | 75.0% | 30 | 250 |" in table


def test_build_cost_quality_table_empty_runs() -> None:
    assert build_cost_quality_table({}) == "No model runs."


def test_pairwise_deltas_relative_to_baseline() -> None:
    runs = {
        "alpha": _run("alpha", resolution_rate=0.5),
        "beta": _run("beta", resolution_rate=0.8),
        "gamma": _run("gamma", resolution_rate=1.0),
    }

    deltas = pairwise_deltas(runs, baseline_model="beta")

    assert deltas == pytest.approx({"alpha": -0.3, "beta": 0.0, "gamma": 0.2})


def test_pairwise_deltas_unknown_baseline_raises() -> None:
    runs = {"alpha": _run("alpha", resolution_rate=0.5)}

    with pytest.raises(ValueError, match="baseline"):
        pairwise_deltas(runs, baseline_model="missing")
