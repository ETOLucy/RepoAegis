from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from repo_maintenance_agent.domain.models import RiskLevel


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskSpecOutput(AgentOutput):
    task_type: Literal["bugfix", "feature", "test", "documentation", "dependency", "refactor"]
    summary: str = Field(min_length=1, max_length=2_000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=20)


class PlanStep(AgentOutput):
    description: str = Field(min_length=1, max_length=2_000)
    paths: list[str] = Field(min_length=1, max_length=50)
    verification: str = Field(min_length=1, max_length=2_000)


class PlanOutput(AgentOutput):
    steps: list[PlanStep] = Field(min_length=1, max_length=30)
    risk: RiskLevel
    risk_reasons: list[str] = Field(default_factory=list, max_length=20)


class ContextRequest(AgentOutput):
    ready_to_patch: bool
    search_queries: list[str] = Field(default_factory=list, max_length=5)
    files: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def request_has_an_action(self) -> ContextRequest:
        if not self.ready_to_patch and not self.search_queries and not self.files:
            raise ValueError("context request must search, read, or be ready to patch")
        return self


class PatchEdit(AgentOutput):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    path: str = Field(min_length=1, max_length=1_000)
    old_text: str | None = Field(default=None, max_length=200_000)
    new_text: str = Field(max_length=200_000)

    @field_validator("path")
    @classmethod
    def path_is_safe(cls, value: str) -> str:
        normalized = value.strip().replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or path.is_absolute()
            or ".." in path.parts
            or normalized.startswith("-")
            or "\t" in normalized
            or "\n" in normalized
            or "\r" in normalized
        ):
            raise ValueError("path must be a safe repository-relative path")
        normalized = path.as_posix()
        if normalized in {"", "."}:
            raise ValueError("path must be a safe repository-relative path")
        return normalized

    @model_validator(mode="after")
    def edit_has_a_real_change(self) -> PatchEdit:
        if self.old_text is None:
            if not self.new_text:
                raise ValueError("new file content must be non-empty")
            return self
        if not self.old_text:
            raise ValueError("old_text must be non-empty for replacement")
        # no-op edits (old_text == new_text) are dropped at the PatchProposal level
        return self


class PatchProposal(AgentOutput):
    summary: str = Field(min_length=1, max_length=2_000)
    edits: list[PatchEdit] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def drop_noop_edits(self) -> PatchProposal:
        # The model sometimes emits no-op edits (old_text == new_text). Those carry no
        # change and make the whole proposal invalid under edit_has_a_real_change.
        # Drop them here so a proposal that still has at least one real edit survives.
        kept: list[PatchEdit] = []
        for edit in self.edits:
            if edit.old_text is None:
                kept.append(edit)
            elif edit.old_text and edit.old_text != edit.new_text:
                kept.append(edit)
        if not kept:
            raise ValueError("patch proposal contains no effective edits")
        self.edits = kept
        return self

class ReviewOutput(AgentOutput):
    decision: Literal["approve", "request_changes"]
    findings: list[str] = Field(default_factory=list, max_length=50)
    summary: str = Field(min_length=1, max_length=5_000)


class PullRequestDraft(AgentOutput):
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1, max_length=50_000)
    head: str = Field(min_length=1, max_length=255)
    base: str = Field(min_length=1, max_length=255)
