from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from repo_maintenance_agent.domain.models import TaskStatus
from repo_maintenance_agent.graph.routes import (
    route_after_approval,
    route_after_planning,
    route_after_review,
    route_after_verification,
    route_entry,
)
from repo_maintenance_agent.graph.state import GraphState

Node = Callable[[GraphState], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class AgentNodes:
    intake: Node
    research: Node
    planning: Node
    approval: Node
    coding: Node
    verification: Node
    review: Node
    pr: Node
    failure: Node


async def _finalize(state: GraphState) -> dict[str, object]:
    return {"task": state["task"].transition(TaskStatus.COMPLETED)}


def _graph_action(node: Node) -> Any:
    """Narrow adapter for LangGraph's stricter callable overload annotations."""
    return cast(Any, node)


def build_graph(nodes: AgentNodes, *, checkpointer: Any | None = None) -> Any:
    graph = StateGraph(GraphState)
    graph.add_node("intake", _graph_action(nodes.intake))
    graph.add_node("research", _graph_action(nodes.research))
    graph.add_node("planning", _graph_action(nodes.planning))
    graph.add_node("approval", _graph_action(nodes.approval))
    graph.add_node("code", _graph_action(nodes.coding))
    graph.add_node("verification", _graph_action(nodes.verification))
    graph.add_node("review", _graph_action(nodes.review))
    graph.add_node("pr", _graph_action(nodes.pr))
    graph.add_node("failure", _graph_action(nodes.failure))
    graph.add_node("finalize", _finalize)

    graph.add_conditional_edges(START, route_entry)
    graph.add_edge("intake", "research")
    graph.add_edge("research", "planning")
    graph.add_conditional_edges("planning", route_after_planning)
    graph.add_conditional_edges("approval", route_after_approval)
    graph.add_edge("code", "verification")
    graph.add_conditional_edges("verification", route_after_verification)
    graph.add_conditional_edges("review", route_after_review)
    graph.add_edge("pr", "finalize")
    graph.add_edge("finalize", END)
    graph.add_edge("failure", END)
    return graph.compile(checkpointer=checkpointer)
