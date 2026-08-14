# ruff: noqa: RUF001, RUF002
"""Benchmark-agnostic evaluation adapters.

RepoAegis 的评测框架是 benchmark-agnostic 的：Evaluation Harness 只依赖
BenchmarkAdapter 抽象，不感知具体基准（SWE-bench / Pro / Fresh）。每个基准通过
一个 adapter 暴露「任务加载 → 预测执行 → 结果归一化」三段能力，并把原生结果
归一化为统一的 BenchmarkPrediction 记录（含 resolved / usage / cost / token）。

SWE-bench 是第一个 adapter（SwebenchAdapter），它只封装 swebench_runner 的既有
能力，不改动 swebench_runner 的任何调用方。
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from repo_maintenance_agent.domain.models import Evidence
from repo_maintenance_agent.evaluation import swebench_runner
from repo_maintenance_agent.evaluation.models import EvaluationCase, ModelUsage
from repo_maintenance_agent.evaluation.swebench import SWEbenchPrediction
from repo_maintenance_agent.evaluation.swebench_runner import (
    SWEbenchGenerationEvidence,
    SWEbenchTask,
)
from repo_maintenance_agent.models.usage import UsageLedger

__all__ = [
    "BenchmarkAdapter",
    "BenchmarkPrediction",
    "BenchmarkTask",
    "SwebenchAdapter",
]


class BenchmarkTask(BaseModel):
    """benchmark-agnostic 任务视图（adapter 的统一输入）。

    SWEbenchTask 的四个核心字段与之同构，SwebenchAdapter 负责双向映射。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str = Field(min_length=1, max_length=256)
    repo_id: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    problem_statement: str = Field(min_length=1, max_length=100_000)


class BenchmarkPrediction(BaseModel):
    """benchmark-agnostic 归一化预测记录（adapter 的统一输出）。

    ``resolved`` 由权威判分层（Inspect / 官方 scorer）回填：None 表示尚未判分，
    生成 patch 不等于解决任务（见 docs/evaluation.md 的完整性契约）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark: str = Field(min_length=1, max_length=64)
    instance_id: str = Field(min_length=1, max_length=256)
    model_name_or_path: str = Field(min_length=1, max_length=256)
    model_patch: str | None = None
    resolved: bool | None = Field(
        default=None,
        description="官方判分结果；None 表示尚未经过权威判分",
    )
    usage: ModelUsage = Field(default_factory=ModelUsage)
    latency_ms: int = Field(default=0, ge=0)
    error_type: str | None = Field(default=None, max_length=256)
    error_summary: str | None = Field(default=None, max_length=2_000)

    @property
    def cost_cny(self) -> Decimal:
        return self.usage.estimated_cost_cny

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens


class BenchmarkAdapter(ABC):
    """统一评测基准适配器接口。

    扩展点：新增基准（Pro / Fresh）时实现三个抽象方法即可接入 Evaluation
    Framework；``with_resolution`` 与 ``to_evidence`` 由基类提供，无需重写。
    """

    benchmark: ClassVar[str] = "benchmark"

    @abstractmethod
    def load_tasks(self) -> Sequence[BenchmarkTask]:
        """加载并校验基准任务/实例，返回统一任务视图。"""

    @abstractmethod
    async def run_predictions(
        self,
        tasks: Sequence[BenchmarkTask],
        *,
        output_path: Path,
        evidence_directory: Path,
    ) -> Sequence[BenchmarkPrediction]:
        """对任务执行预测，并把原生结果归一化为统一记录。

        output_path：predictions 落盘路径；evidence_directory：逐实例证据目录。
        失败时应写出失败证据并抛异常（与 swebench_runner 语义一致）。
        """

    @abstractmethod
    def to_evaluation_case(self, task: BenchmarkTask) -> EvaluationCase:
        """把 adapter 任务映射为 EvaluationHarness 可消费的 EvaluationCase。"""

    def with_resolution(
        self,
        predictions: Sequence[BenchmarkPrediction],
        *,
        resolved_by_instance: Mapping[str, bool],
    ) -> tuple[BenchmarkPrediction, ...]:
        """回填权威判分结果（官方 scorer / Inspect 的 0/1 结论）。"""
        return tuple(
            prediction.model_copy(
                update={"resolved": resolved_by_instance[prediction.instance_id]}
            )
            if prediction.instance_id in resolved_by_instance
            else prediction
            for prediction in predictions
        )

    def to_evidence(
        self,
        prediction: BenchmarkPrediction,
        *,
        source: str | None = None,
    ) -> Evidence:
        """把归一化记录落成 domain Evidence（可写存储 / 审计）。"""
        record = prediction.model_dump(mode="json")
        summary = json.dumps(record, sort_keys=True, ensure_ascii=False)
        return Evidence(
            source=source or f"{self.benchmark}-adapter",
            locator=f"{prediction.benchmark}:{prediction.instance_id}",
            summary=summary,
            content_hash=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
        )


class SwebenchAdapter(BenchmarkAdapter):
    """SWE-bench 第一个 adapter：封装 swebench_runner 的核心能力。

    包装 SWEbenchTask 加载、GitSWEbenchRuntime / RepoAegisPatchAgent 执行、
    SWEbenchPrediction 输出，通过 BenchmarkAdapter 接口暴露；不改变
    swebench_runner 自身的 run_predictions 调用。
    """

    benchmark: ClassVar[str] = "swe-bench"

    def __init__(
        self,
        *,
        runtime: swebench_runner.RuntimeExecutor,
        ledger: UsageLedger,
        tasks_path: Path,
        protocol_digest: str,
        arm: Literal["baseline", "candidate"],
    ) -> None:
        if not protocol_digest.startswith("sha256:") or len(protocol_digest) != 71:
            raise ValueError("protocol digest is invalid")
        self._runtime = runtime
        self._ledger = ledger
        self._tasks_path = Path(tasks_path)
        self._protocol_digest = protocol_digest
        self._arm = arm

    def load_tasks(self) -> tuple[BenchmarkTask, ...]:
        tasks: list[BenchmarkTask] = []
        lines = self._tasks_path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                native = SWEbenchTask.model_validate_json(line)
            except ValueError as error:
                raise ValueError(
                    f"invalid SWE-bench task at line {line_number} "
                    f"in {self._tasks_path}"
                ) from error
            tasks.append(self._from_swebench_task(native))
        if not tasks:
            raise ValueError(f"no SWE-bench tasks found in {self._tasks_path}")
        return tuple(tasks)

    async def run_predictions(
        self,
        tasks: Sequence[BenchmarkTask],
        *,
        output_path: Path,
        evidence_directory: Path,
    ) -> tuple[BenchmarkPrediction, ...]:
        native_tasks = tuple(self._to_swebench_task(task) for task in tasks)
        predictions = await swebench_runner.run_predictions(
            native_tasks,
            runtime=self._runtime,
            ledger=self._ledger,
            evidence_directory=evidence_directory,
            output_path=output_path,
            protocol_digest=self._protocol_digest,
            arm=self._arm,
        )
        return tuple(
            self.normalize(prediction, evidence_directory=evidence_directory)
            for prediction in predictions
        )

    def normalize(
        self,
        prediction: SWEbenchPrediction,
        *,
        evidence_directory: Path,
    ) -> BenchmarkPrediction:
        """把原生 SWEbenchPrediction + 落盘证据归一化为统一记录。"""
        evidence_path = Path(evidence_directory) / (
            hashlib.sha256(prediction.instance_id.encode()).hexdigest() + ".json"
        )
        usage = ModelUsage()
        latency_ms = 0
        if evidence_path.exists():
            evidence = SWEbenchGenerationEvidence.model_validate_json(
                evidence_path.read_text(encoding="utf-8")
            )
            usage = evidence.usage
            latency_ms = evidence.latency_ms
        return BenchmarkPrediction(
            benchmark=self.benchmark,
            instance_id=prediction.instance_id,
            model_name_or_path=prediction.model_name_or_path,
            model_patch=prediction.model_patch,
            resolved=None,
            usage=usage,
            latency_ms=latency_ms,
        )

    def to_evaluation_case(self, task: BenchmarkTask) -> EvaluationCase:
        return EvaluationCase(
            case_id=task.instance_id,
            repo_id=task.repo_id,
            base_commit=task.base_commit,
            gold_files=(),
            hidden_test_commands=(),
        )

    def _to_swebench_task(self, task: BenchmarkTask) -> SWEbenchTask:
        return SWEbenchTask(
            instance_id=task.instance_id,
            repo=task.repo_id,
            base_commit=task.base_commit,
            problem_statement=task.problem_statement,
        )

    @staticmethod
    def _from_swebench_task(task: SWEbenchTask) -> BenchmarkTask:
        return BenchmarkTask(
            instance_id=task.instance_id,
            repo_id=task.repo,
            base_commit=task.base_commit,
            problem_statement=task.problem_statement,
        )
