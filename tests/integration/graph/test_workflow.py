from collections.abc import Awaitable, Callable

import pytest

from repo_maintenance_agent.domain.models import (
    RepoTaskState,
    TaskStatus,
    VerificationResult,
)
from repo_maintenance_agent.graph.builder import AgentNodes, build_graph
from repo_maintenance_agent.graph.state import GraphState

Node = Callable[[GraphState], Awaitable[dict[str, object]]]


def transition_node(target: TaskStatus, name: str, **updates: object) -> Node:
    async def node(graph_state: GraphState) -> dict[str, object]:
        task = graph_state["task"].transition(target).model_copy(update=updates)
        return {"task": task, "trace": [name]}

    return node


@pytest.mark.asyncio
async def test_graph_completes_verified_low_risk_change() -> None:
    nodes = AgentNodes(
        intake=transition_node(TaskStatus.INTAKE, "intake"),
        research=transition_node(TaskStatus.RESEARCH, "research"),
        planning=transition_node(TaskStatus.PLANNING, "planning"),
        approval=transition_node(TaskStatus.CODING, "approval"),
        coding=transition_node(TaskStatus.CODING, "coding", iteration=1),
        verification=transition_node(
            TaskStatus.VERIFYING,
            "verification",
            verification=VerificationResult(passed=True),
        ),
        review=transition_node(
            TaskStatus.REVIEWING,
            "review",
            review={"decision": "approve"},
        ),
        pr=transition_node(TaskStatus.DELIVERING, "pr"),
        failure=transition_node(TaskStatus.FAILED, "failure"),
    )
    task = RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
    )
    graph = build_graph(nodes)

    result = await graph.ainvoke({"task": task, "trace": []})

    assert result["task"].status is TaskStatus.COMPLETED
    assert result["trace"] == [
        "intake",
        "research",
        "planning",
        "coding",
        "verification",
        "review",
        "pr",
    ]
