from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from repo_maintenance_agent.domain.models import Evidence
from repo_maintenance_agent.evaluation.adapters import (
    BenchmarkAdapter,
    BenchmarkPrediction,
    BenchmarkTask,
    SwebenchAdapter,
)
from repo_maintenance_agent.evaluation.models import EvaluationCase, ModelUsage
from repo_maintenance_agent.evaluation.swebench import SWEbenchPrediction
from repo_maintenance_agent.evaluation.swebench_runner import SWEbenchTask
from repo_maintenance_agent.models.usage import UsageLedger, UsageRates

_PROTOCOL_DIGEST = "sha256:" + "a" * 64
_PATCH = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
_INSTANCE_ID = "django__django-11099"
_REPO = "django/django"


class FakeRuntime:
    model_name_or_path = "fake-model/baseline"

    def development_feedback_digest(self, instance_id: str) -> str | None:
        del instance_id
        return None

    async def execute(self, task: SWEbenchTask, ledger: UsageLedger) -> SWEbenchPrediction:
        del ledger
        return SWEbenchPrediction(
            instance_id=task.instance_id,
            model_patch=_PATCH,
            model_name_or_path=self.model_name_or_path,
        )


class RecordingRuntime(FakeRuntime):
    async def execute(self, task: SWEbenchTask, ledger: UsageLedger) -> SWEbenchPrediction:
        reservation = ledger.reserve(Decimal("0.001"))
        ledger.record(
            ModelUsage(input_cache_miss_tokens=100, output_tokens=50),
            reservation_cny=reservation,
        )
        return SWEbenchPrediction(
            instance_id=task.instance_id,
            model_patch=_PATCH,
            model_name_or_path=self.model_name_or_path,
        )


def _task_jsonl(tmp_path: Path) -> Path:
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        json.dumps(
            {
                "instance_id": _INSTANCE_ID,
                "repo": _REPO,
                "base_commit": "a" * 40,
                "problem_statement": "Fix the admin list view crash.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _ledger() -> UsageLedger:
    return UsageLedger(
        limit_cny=Decimal("10"),
        rates=UsageRates(
            cache_hit_input_cny_per_million=Decimal("0"),
            cache_miss_input_cny_per_million=Decimal("1"),
            output_cny_per_million=Decimal("2"),
        ),
    )


def _adapter(
    tmp_path: Path,
    *,
    runtime: FakeRuntime | None = None,
    ledger: UsageLedger | None = None,
    tasks_path: Path | None = None,
) -> SwebenchAdapter:
    return SwebenchAdapter(
        runtime=runtime or FakeRuntime(),
        ledger=ledger or _ledger(),
        tasks_path=tasks_path or _task_jsonl(tmp_path),
        protocol_digest=_PROTOCOL_DIGEST,
        arm="candidate",
    )


def _prediction() -> BenchmarkPrediction:
    return BenchmarkPrediction(
        benchmark="swe-bench",
        instance_id=_INSTANCE_ID,
        model_name_or_path="fake-model/baseline",
        model_patch=_PATCH,
    )


# --- 接口可实例化 / 抽象约束 ---


def test_benchmark_adapter_is_abstract() -> None:
    with pytest.raises(TypeError):
        BenchmarkAdapter()  # type: ignore[abstract]


def test_swebench_adapter_instantiates_and_implements_interface(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    assert isinstance(adapter, BenchmarkAdapter)
    assert adapter.benchmark == "swe-bench"


def test_swebench_adapter_rejects_invalid_protocol_digest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="protocol digest"):
        SwebenchAdapter(
            runtime=FakeRuntime(),
            ledger=_ledger(),
            tasks_path=_task_jsonl(tmp_path),
            protocol_digest="not-a-digest",
            arm="candidate",
        )


def test_benchmark_task_validates_commit(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        BenchmarkTask(
            instance_id=_INSTANCE_ID,
            repo_id=_REPO,
            base_commit="short",
            problem_statement="p",
        )


# --- load_tasks ---


def test_load_tasks_reads_one_swebench_task(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)

    tasks = adapter.load_tasks()

    assert len(tasks) == 1
    task = tasks[0]
    assert isinstance(task, BenchmarkTask)
    assert task.instance_id == _INSTANCE_ID
    assert task.repo_id == _REPO
    assert task.base_commit == "a" * 40
    assert task.problem_statement.startswith("Fix")


def test_load_tasks_rejects_invalid_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"instance_id":"x"}\n', encoding="utf-8")
    adapter = _adapter(tmp_path, tasks_path=path)

    with pytest.raises(ValueError, match="line 1"):
        adapter.load_tasks()


def test_load_tasks_rejects_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    adapter = _adapter(tmp_path, tasks_path=path)

    with pytest.raises(ValueError, match="no SWE-bench tasks"):
        adapter.load_tasks()


# --- run_predictions + 归一化 ---


async def test_run_predictions_outputs_normalized_prediction(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    tasks = adapter.load_tasks()
    output = tmp_path / "predictions.jsonl"
    evidence_dir = tmp_path / "evidence"

    predictions = await adapter.run_predictions(
        tasks,
        output_path=output,
        evidence_directory=evidence_dir,
    )

    assert len(predictions) == 1
    prediction = predictions[0]
    assert isinstance(prediction, BenchmarkPrediction)
    assert prediction.benchmark == "swe-bench"
    assert prediction.instance_id == _INSTANCE_ID
    assert prediction.model_name_or_path == "fake-model/baseline"
    assert prediction.model_patch == _PATCH
    assert prediction.resolved is None
    assert prediction.usage.total_tokens == 0
    assert prediction.cost_cny == Decimal("0")
    assert prediction.total_tokens == 0
    assert output.read_text(encoding="utf-8").strip()
    assert any(evidence_dir.glob("*.json"))


async def test_run_predictions_records_usage_cost_tokens(tmp_path: Path) -> None:
    ledger = _ledger()
    adapter = _adapter(tmp_path, runtime=RecordingRuntime(), ledger=ledger)
    tasks = adapter.load_tasks()

    predictions = await adapter.run_predictions(
        tasks,
        output_path=tmp_path / "predictions.jsonl",
        evidence_directory=tmp_path / "evidence",
    )

    prediction = predictions[0]
    # rates: cache_miss=1 CNY/M, output=2 CNY/M -> (100*1 + 50*2) / 1e6
    assert prediction.usage.total_tokens == 150
    assert prediction.total_tokens == 150
    assert prediction.cost_cny == Decimal("0.0002")
    assert ledger.spent_cny == Decimal("0.0002")


# --- Harness / Evidence 桥接 ---


def test_to_evaluation_case_maps_harness_case(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    task = adapter.load_tasks()[0]

    case = adapter.to_evaluation_case(task)

    assert isinstance(case, EvaluationCase)
    assert case.case_id == task.instance_id
    assert case.repo_id == task.repo_id
    assert case.base_commit == task.base_commit
    assert case.gold_files == ()
    assert case.hidden_test_commands == ()


def test_to_evidence_builds_domain_record(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    prediction = _prediction()

    evidence = adapter.to_evidence(prediction)

    assert isinstance(evidence, Evidence)
    assert evidence.source == "swe-bench-adapter"
    assert evidence.locator == f"swe-bench:{_INSTANCE_ID}"
    assert len(evidence.content_hash or "") == 64
    assert evidence.summary


def test_with_resolution_backfills_official_scores(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    prediction = _prediction()
    assert prediction.resolved is None

    updated = adapter.with_resolution(
        [prediction],
        resolved_by_instance={_INSTANCE_ID: True},
    )

    assert len(updated) == 1
    assert updated[0].resolved is True
    assert prediction.resolved is None  # 原记录不可变
