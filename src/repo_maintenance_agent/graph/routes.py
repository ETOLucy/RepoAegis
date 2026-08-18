from __future__ import annotations

from repo_maintenance_agent.domain.models import ErrorKind, TaskStatus, ToolPermission
from repo_maintenance_agent.graph.state import GraphState
from repo_maintenance_agent.policies.risk import deterministic_risk


def route_entry(state: GraphState) -> str:
    status = state["task"].status
    if status is TaskStatus.PENDING:
        return "intake"
    if status is TaskStatus.CODING:
        return "code"
    return "failure"


def route_after_planning(state: GraphState) -> str:
    task = state["task"]
    if task.status is TaskStatus.NEEDS_APPROVAL:
        return "approval"
    if task.status is TaskStatus.FAILED:
        return "failure"
    return "code"


def route_after_verification(state: GraphState) -> str:
    task = state["task"]
    verification = task.verification
    if verification is not None and verification.passed:
        return "review"
    if (
        verification is not None
        and verification.error_kind is ErrorKind.CODE
        and task.iteration < task.max_iterations
    ):
        return "code"
    return "failure"


def route_after_approval(state: GraphState) -> str:
    return "code" if state["task"].status is TaskStatus.CODING else "failure"


def route_after_review(state: GraphState) -> str:
    task = state["task"]
    if task.review.get("decision") == "approve":
        return "pr"
    if task.review.get("decision") == "request_changes" and task.iteration < task.max_iterations:
        return "code"
    # Evidence-driven fallback: verified, in-plan, low-risk changes are accepted even if
    # the LLM reviewer keeps requesting changes (the warning is kept in the review record).
    if task.review.get("decision") == "request_changes":
        verification = task.verification
        changed = set(task.changed_files)
        if (
            verification is not None
            and verification.passed
            and changed
            and changed <= set(task.declared_files)
        ):
            _, risk_reasons = deterministic_risk(
                tuple(task.changed_files), (ToolPermission.REPO_READ,)
            )
            if not risk_reasons:
                return "pr"
    return "failure"
