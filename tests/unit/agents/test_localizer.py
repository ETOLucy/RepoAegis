from __future__ import annotations

import json

import pytest

from repo_maintenance_agent.agents.localizer import Localizer, LocalizerAction
from repo_maintenance_agent.domain.models import (
    RepoTaskState,
    SearchHit,
    ToolResult,
)


class FakeLocalizerModel:
    def __init__(self, actions: list[str]) -> None:
        self.actions = list(actions)
        self.inputs: list[dict[str, object]] = []

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        self.inputs.append(json.loads(input_text))
        action = self.actions.pop(0)
        query = "load_config" if action in ("search", "goto_definition", "find_references") else ""
        return LocalizerAction(
            action=action,
            query=query,
            files=["src/config.py"] if action == "read" else [],
            rationale="follow evidence",
        )


class FakeGateway:
    async def execute(self, call, state):
        if call.name == "search_code":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={
                    "hits": [
                        SearchHit(
                            hit_id="h1",
                            path="src/config.py",
                            content="def load_config(): ...",
                            score=0.9,
                            source="bm25",
                            line_start=1,
                        ).model_dump(mode="json")
                    ]
                },
            )
        if call.name == "read_files":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"files": {"src/config.py": "def load_config(): return {}"}},
            )
        if call.name == "git_blame":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={"blame": "abc123 (Author 2026-01-01) def load_config()"},
            )
        if call.name == "goto_definition":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={
                    "definitions": [
                        SearchHit(
                            hit_id="d1",
                            path="src/config.py",
                            content="def load_config(): ...",
                            score=3.0,
                            source="goto_definition:function",
                            symbol="load_config",
                            line_start=1,
                            line_end=1,
                        ).model_dump(mode="json")
                    ]
                },
            )
        if call.name == "find_references":
            return ToolResult(
                call_id=call.call_id,
                success=True,
                output={
                    "references": [
                        SearchHit(
                            hit_id="r1",
                            path="src/app.py",
                            content="load_config()",
                            score=2.0,
                            source="find_references",
                            symbol="load_config",
                            line_start=10,
                            line_end=10,
                        ).model_dump(mode="json")
                    ]
                },
            )
        raise AssertionError(f"unexpected tool: {call.name}")


def _task() -> RepoTaskState:
    return RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix config", "body": "load_config crashes"},
    )


@pytest.mark.asyncio
async def test_localizer_finishes_after_bounded_rounds() -> None:
    model = FakeLocalizerModel(["search", "finish"])
    localizer = Localizer(model=model, gateway=FakeGateway(), max_rounds=3)
    outcome = await localizer.localize(issue_text="fix config", task=_task())
    assert outcome.rounds <= 3
    assert outcome.queries == ["load_config"]
    assert outcome.evidence
    assert outcome.evidence[0].path == "src/config.py"


@pytest.mark.asyncio
async def test_localizer_reads_and_blames() -> None:
    model = FakeLocalizerModel(["read", "blame", "finish"])
    localizer = Localizer(model=model, gateway=FakeGateway(), max_rounds=3)
    outcome = await localizer.localize(issue_text="fix config", task=_task())
    assert any(hit.source == "localizer-read" for hit in outcome.evidence)
    assert any(hit.source == "localizer-blame" for hit in outcome.evidence)


@pytest.mark.asyncio
async def test_localizer_goto_definition_adds_definition_evidence() -> None:
    model = FakeLocalizerModel(["goto_definition", "finish"])
    localizer = Localizer(model=model, gateway=FakeGateway(), max_rounds=3)
    outcome = await localizer.localize(issue_text="fix config", task=_task())
    assert outcome.queries == ["goto_definition load_config"]
    assert any(hit.source == "goto_definition:function" for hit in outcome.evidence)


@pytest.mark.asyncio
async def test_localizer_find_references_adds_reference_evidence() -> None:
    model = FakeLocalizerModel(["find_references", "finish"])
    localizer = Localizer(model=model, gateway=FakeGateway(), max_rounds=3)
    outcome = await localizer.localize(issue_text="fix config", task=_task())
    assert outcome.queries == ["find_references load_config"]
    assert any(hit.source == "find_references" for hit in outcome.evidence)


@pytest.mark.asyncio
async def test_localizer_stops_at_max_rounds_without_finish() -> None:
    model = FakeLocalizerModel(["search", "search", "search", "search"])
    localizer = Localizer(model=model, gateway=FakeGateway(), max_rounds=2)
    outcome = await localizer.localize(issue_text="fix config", task=_task())
    assert len(model.inputs) == 2
    assert outcome.queries == ["load_config", "load_config"]
