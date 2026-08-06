from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class ModelUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_cache_hit_tokens: int = Field(default=0, ge=0)
    input_cache_miss_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    estimated_cost_cny: Decimal = Field(default=Decimal("0"), ge=0)

    @property
    def input_tokens(self) -> int:
        return self.input_cache_hit_tokens + self.input_cache_miss_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    repo_id: str
    base_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    gold_files: tuple[str, ...]
    hidden_test_commands: tuple[tuple[str, ...], ...]
    forbidden_paths: tuple[str, ...] = ()
    timeout_seconds: int = Field(default=1_800, ge=1)


class ReleaseGates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resolution_rate_min: float = Field(default=1.0, ge=0, le=1)
    resolution_regression_max: float = Field(default=0.02, ge=0, le=1)
    unauthorized_tool_call_rate_max: float = Field(default=0.0, ge=0, le=1)
    regression_rate_max: float = Field(default=0.0, ge=0, le=1)
    privacy_findings_max: int = Field(default=0, ge=0)


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1, max_length=10_000)
    concurrency: int = Field(default=4, ge=1, le=64)
    max_attempts: int = Field(default=2, ge=1, le=5)
    gates: ReleaseGates = Field(default_factory=ReleaseGates)

    @model_validator(mode="after")
    def unique_case_ids(self) -> EvaluationSuite:
        case_ids = self.case_ids
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case IDs must be unique")
        return self

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)


class EvaluationProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=256)
    provider: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    tool_schema_version: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    dataset_version: str = Field(min_length=1, max_length=128)
    environment_fingerprint: str = Field(min_length=1, max_length=512)
    seed: int = Field(ge=0)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    issue_resolution: float = Field(ge=0, le=1)
    relevant_file_recall_at_10: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    unauthorized_tool_call_rate: float = Field(ge=0, le=1)
    wall_clock_ms: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class EvaluationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retrieved_files: tuple[str, ...] = ()
    hidden_tests_passed: StrictBool = False
    regression: StrictBool = False
    total_tool_calls: int = Field(default=0, ge=0)
    denied_tool_calls: int = Field(default=0, ge=0)
    wall_clock_ms: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class FailureCategory(StrEnum):
    NONE = "none"
    TIMEOUT = "timeout"
    INFRASTRUCTURE = "infrastructure"
    POLICY = "policy"
    EXECUTION = "execution"
    INVALID_OUTPUT = "invalid_output"


class EvaluationCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=128)
    attempts: int = Field(ge=1, le=5)
    failure_category: FailureCategory = FailureCategory.NONE
    observation: EvaluationObservation | None = None
    report: EvaluationReport | None = None
    error_summary: str | None = Field(default=None, max_length=2_000)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EvaluationAggregate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_count: int = Field(ge=0)
    resolution_rate: float = Field(ge=0, le=1)
    relevant_file_recall_at_10: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    unauthorized_tool_call_rate: float = Field(ge=0, le=1)
    regression_rate: float = Field(ge=0, le=1)
    latency_p50_ms: int = Field(ge=0)
    latency_p95_ms: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    terminal_failure_count: int = Field(ge=0)


class EvaluationComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_run_id: str
    resolution_rate_delta: float = Field(ge=-1, le=1)
    relevant_file_recall_at_10_delta: float = Field(ge=-1, le=1)
    mrr_delta: float = Field(ge=-1, le=1)
    unauthorized_tool_call_rate_delta: float = Field(ge=-1, le=1)
    regression_rate_delta: float = Field(ge=-1, le=1)
    latency_p50_ms_delta: int
    latency_p95_ms_delta: int
    total_tokens_delta: int


class GateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    passed: bool
    actual: float | int | None
    threshold: float | int | None
    detail: str


class GateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    checks: tuple[GateCheck, ...]


class EvaluationRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvaluationRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    suite: EvaluationSuite
    candidate_label: str = Field(min_length=1, max_length=256)
    provenance: EvaluationProvenance
    status: EvaluationRunStatus = EvaluationRunStatus.QUEUED
    baseline_run_id: str | None = None
    replay_of_run_id: str | None = None
    selected_case_ids: tuple[str, ...] = ()
    results: tuple[EvaluationCaseResult, ...] = ()
    aggregate: EvaluationAggregate | None = None
    comparison: EvaluationComparison | None = None
    gate_decision: GateDecision | None = None
    privacy_findings: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    version: int = Field(default=0, ge=0)
