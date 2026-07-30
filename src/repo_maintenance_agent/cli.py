from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import typer

from repo_maintenance_agent import __version__
from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.evaluation.models import (
    EvaluationCase,
    EvaluationObservation,
)
from repo_maintenance_agent.evaluation.runner import grade_case

app = typer.Typer(
    name="repo-agent",
    help="Operate and evaluate the Repo Maintenance Agent control plane.",
    no_args_is_help=True,
)


class ControlPlaneClient:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout
        self._transport = transport

    def create_task(
        self,
        *,
        repo_id: str,
        commit_sha: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/tasks",
            json={
                "repo_id": repo_id,
                "commit_sha": commit_sha,
                "base_branch": base_branch,
                "issue": {"title": title, "body": body},
            },
        )

    def status(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/tasks/{task_id}")

    def decide(
        self,
        task_id: str,
        *,
        approved: bool,
        plan_hash: str,
        reason: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/tasks/{task_id}/approval",
            json={
                "approved": approved,
                "plan_hash": plan_hash,
                "reason": reason,
            },
        )

    def cancel(self, task_id: str) -> dict[str, Any]:
        return self._request("POST", f"/v1/tasks/{task_id}/cancel")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                base_url=self._base_url,
                headers=self._headers,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = client.request(method, path, json=json)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"control plane returned HTTP {error.response.status_code}"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise RuntimeError("control plane request failed") from error
        if not isinstance(payload, dict):
            raise RuntimeError("control plane returned an invalid response")
        return payload


@app.command()
def doctor() -> None:
    """Report configuration readiness without printing credential values."""
    settings = Settings()
    typer.echo(f"version: {__version__}")
    typer.echo(f"environment: {settings.environment}")
    typer.echo(
        "openai_credentials_available: "
        + ("true" if settings.has_openai_credentials else "false")
    )
    typer.echo(f"openai_model: {settings.openai_model}")


@app.command()
def config() -> None:
    """Print non-sensitive effective configuration."""
    settings = Settings()
    safe = {
        "environment": settings.environment,
        "openai_credentials_available": settings.has_openai_credentials,
        "openai_model": settings.openai_model,
        "artifact_root": settings.artifact_root,
        "max_iterations": settings.max_iterations,
    }
    typer.echo(json.dumps(safe, indent=2, sort_keys=True))


@app.command()
def run(
    repo_id: str,
    commit_sha: str,
    title: str,
    body: str = typer.Option("", help="Issue body."),
    base_branch: str = typer.Option("main", help="Immutable task base branch."),
) -> None:
    """Create and enqueue a repository maintenance task."""
    client = _control_plane_client()
    _emit(
        client.create_task(
            repo_id=repo_id,
            commit_sha=commit_sha,
            base_branch=base_branch,
            title=title,
            body=body,
        )
    )


@app.command()
def status(task_id: str) -> None:
    """Read tenant-scoped task status."""
    _emit(_control_plane_client().status(task_id))


@app.command()
def approve(
    task_id: str,
    plan_hash: str,
    reason: str = typer.Option(..., help="Auditable approval reason."),
    reject: bool = typer.Option(False, help="Reject instead of approving."),
) -> None:
    """Approve or reject the active immutable plan."""
    _emit(
        _control_plane_client().decide(
            task_id,
            approved=not reject,
            plan_hash=plan_hash,
            reason=reason,
        )
    )


@app.command()
def resume(
    task_id: str,
    plan_hash: str,
    reason: str = typer.Option(..., help="Auditable resume reason."),
) -> None:
    """Approve the active plan and resume queued execution."""
    _emit(
        _control_plane_client().decide(
            task_id,
            approved=True,
            plan_hash=plan_hash,
            reason=reason,
        )
    )


@app.command()
def cancel(task_id: str) -> None:
    """Cancel a non-terminal task."""
    _emit(_control_plane_client().cancel(task_id))


@app.command()
def evaluate(case_file: Path, result_file: Path) -> None:
    """Grade a deterministic evaluation result against a case definition."""
    case = EvaluationCase.model_validate_json(case_file.read_text(encoding="utf-8"))
    observation = EvaluationObservation.model_validate_json(
        result_file.read_text(encoding="utf-8")
    )
    report = grade_case(
        case,
        retrieved_files=list(observation.retrieved_files),
        hidden_tests_passed=observation.hidden_tests_passed,
        regression=observation.regression,
        total_tool_calls=observation.total_tool_calls,
        denied_tool_calls=observation.denied_tool_calls,
        wall_clock_ms=observation.wall_clock_ms,
        model_calls=observation.model_calls,
        input_tokens=observation.input_tokens,
        output_tokens=observation.output_tokens,
    )
    typer.echo(report.model_dump_json(indent=2))


def _control_plane_client() -> ControlPlaneClient:
    settings = Settings()
    if settings.api_token is None:
        raise typer.BadParameter("REPO_AGENT_API_TOKEN is required")
    return ControlPlaneClient(
        base_url=settings.api_url,
        token=settings.api_token.get_secret_value(),
        timeout=settings.api_timeout_seconds,
    )


def _emit(value: dict[str, Any]) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
