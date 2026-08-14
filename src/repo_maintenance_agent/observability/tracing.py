from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from repo_maintenance_agent.policies.redaction import Redactor


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model_id: str
    prompt_hash: str
    tool_schema_version: str
    policy_version: str
    attributes: dict[str, Any]


class TraceSink(Protocol):
    def write(self, event: TraceEvent) -> None: ...


class InMemoryTraceSink:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def write(self, event: TraceEvent) -> None:
        self.events.append(event)


class StructuredTracer:
    def __init__(
        self,
        *,
        sink: TraceSink,
        model_id: str,
        prompt_hash: str,
        tool_schema_version: str,
        policy_version: str,
        redactor: Redactor | None = None,
    ) -> None:
        self._sink = sink
        self._model_id = model_id
        self._prompt_hash = prompt_hash
        self._tool_schema_version = tool_schema_version
        self._policy_version = policy_version
        self._redactor = redactor or Redactor()

    def emit(self, name: str, attributes: dict[str, Any]) -> None:
        self._sink.write(
            TraceEvent(
                name=name,
                model_id=self._model_id,
                prompt_hash=self._prompt_hash,
                tool_schema_version=self._tool_schema_version,
                policy_version=self._policy_version,
                attributes=self._redactor.redact(attributes),
            )
        )
