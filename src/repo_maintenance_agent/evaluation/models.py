from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    repo_id: str
    base_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    gold_files: tuple[str, ...]
    hidden_test_commands: tuple[tuple[str, ...], ...]
    forbidden_paths: tuple[str, ...] = ()
    timeout_seconds: int = Field(default=1_800, ge=1)


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
    model_config = ConfigDict(extra="forbid", strict=True)

    retrieved_files: tuple[str, ...] = ()
    hidden_tests_passed: bool = False
    regression: bool = False
    total_tool_calls: int = Field(default=0, ge=0)
    denied_tool_calls: int = Field(default=0, ge=0)
    wall_clock_ms: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
