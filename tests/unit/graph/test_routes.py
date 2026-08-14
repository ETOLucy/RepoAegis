from repo_maintenance_agent.domain.models import (
    ErrorKind,
    RepoTaskState,
    TaskStatus,
    VerificationResult,
)
from repo_maintenance_agent.graph.routes import (
    route_after_planning,
    route_after_review,
    route_after_verification,
    route_entry,
)


def state(status: TaskStatus, *, iteration: int = 0) -> RepoTaskState:
    return RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
        status=status,
        iteration=iteration,
        max_iterations=3,
    )


def test_planning_routes_high_risk_task_to_approval() -> None:
    task = state(TaskStatus.NEEDS_APPROVAL)

    assert route_after_planning({"task": task}) == "approval"


def test_graph_resumes_approved_task_at_coding_node() -> None:
    approved = state(TaskStatus.CODING)

    assert route_entry({"task": approved}) == "code"


def test_code_failure_retries_while_budget_remains() -> None:
    task = state(TaskStatus.VERIFYING, iteration=1).model_copy(
        update={"verification": VerificationResult(passed=False, error_kind=ErrorKind.CODE)}
    )

    assert route_after_verification({"task": task}) == "code"


def test_environment_failure_does_not_trigger_code_changes() -> None:
    task = state(TaskStatus.VERIFYING).model_copy(
        update={
            "verification": VerificationResult(
                passed=False,
                error_kind=ErrorKind.ENVIRONMENT,
            )
        }
    )

    assert route_after_verification({"task": task}) == "failure"


def test_review_requests_changes_until_iteration_budget_exhausted() -> None:
    retry = state(TaskStatus.REVIEWING, iteration=2).model_copy(
        update={"review": {"decision": "request_changes"}}
    )
    exhausted = retry.model_copy(update={"iteration": 3})

    assert route_after_review({"task": retry}) == "code"
    assert route_after_review({"task": exhausted}) == "failure"
