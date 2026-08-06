from __future__ import annotations

import asyncio
import hashlib
import json
import platform
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import typer
from pydantic import TypeAdapter

from repo_maintenance_agent import __version__
from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.evaluation.harness import (
    EvaluationHarness,
    ObservationExecutor,
)
from repo_maintenance_agent.evaluation.models import (
    EvaluationCase,
    EvaluationObservation,
    EvaluationProvenance,
    EvaluationSuite,
)
from repo_maintenance_agent.evaluation.reports import render_markdown_report
from repo_maintenance_agent.evaluation.runner import grade_case
from repo_maintenance_agent.evaluation.swebench_runner import (
    GitSWEbenchRuntime,
    RepoAegisPatchAgent,
    SWEbenchGenerationEvidence,
    SWEbenchTask,
    run_predictions,
)
from repo_maintenance_agent.models.openai_gateway import OpenAIModelGateway
from repo_maintenance_agent.models.usage import UsageLedger, UsageRates
from repo_maintenance_agent.tools.process import ProcessRunner

app = typer.Typer(
    name="repo-agent",
    help="Operate and evaluate the RepoAegis control plane.",
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
        target_commit: str,
        allowed_tools: list[str],
        reason: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/tasks/{task_id}/approval",
            json={
                "approved": approved,
                "plan_hash": plan_hash,
                "target_commit": target_commit,
                "allowed_tools": allowed_tools,
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
    client = _control_plane_client()
    task = client.status(task_id)
    _emit(
        client.decide(
            task_id,
            approved=not reject,
            plan_hash=plan_hash,
            target_commit=str(task["commit_sha"]),
            allowed_tools=list(task["allowed_tools"]),
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
    client = _control_plane_client()
    task = client.status(task_id)
    _emit(
        client.decide(
            task_id,
            approved=True,
            plan_hash=plan_hash,
            target_commit=str(task["commit_sha"]),
            allowed_tools=list(task["allowed_tools"]),
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


@app.command("evaluate-suite")
def evaluate_suite(
    suite_file: Path,
    observations_file: Path,
    json_report: Annotated[
        Path,
        typer.Option(help="Destination for the complete run report."),
    ],
    markdown_report: Annotated[
        Path,
        typer.Option(help="Destination for the review report."),
    ],
    candidate_label: str = typer.Option("candidate", help="Candidate build or model label."),
    model: str = typer.Option("deterministic", help="Model identifier recorded in provenance."),
    provider: str = typer.Option("fixture", help="Provider identifier recorded in provenance."),
    seed: int = typer.Option(0, min=0, help="Deterministic suite seed."),
) -> None:
    """Execute a versioned observation suite and enforce its release gates."""
    suite = EvaluationSuite.model_validate_json(
        suite_file.read_text(encoding="utf-8")
    )
    observations = TypeAdapter(dict[str, EvaluationObservation]).validate_json(
        observations_file.read_text(encoding="utf-8")
    )
    if set(observations) != set(suite.case_ids):
        raise typer.BadParameter("observations must exactly match evaluation case IDs")
    provenance = EvaluationProvenance(
        model=model,
        provider=provider,
        prompt_version="cli-observation-v1",
        tool_schema_version="tools-v1",
        policy_version="policy-v1",
        dataset_version=suite.version,
        environment_fingerprint=(
            f"python-{platform.python_version()}-"
            f"{platform.system().lower()}-{platform.machine().lower()}"
        ),
        seed=seed,
    )
    run = asyncio.run(
        EvaluationHarness(ObservationExecutor(observations)).run(
            tenant_id="local-evaluation",
            suite=suite,
            candidate_label=candidate_label,
            provenance=provenance,
        )
    )
    if run.aggregate is None or run.gate_decision is None:
        raise RuntimeError("evaluation run did not produce a report")
    json_report.parent.mkdir(parents=True, exist_ok=True)
    markdown_report.parent.mkdir(parents=True, exist_ok=True)
    json_report.write_text(
        run.model_dump_json(exclude={"tenant_id"}, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_report.write_text(
        render_markdown_report(
            run_id=run.run_id,
            candidate_label=run.candidate_label,
            aggregate=run.aggregate,
            comparison=run.comparison,
            decision=run.gate_decision,
            results=run.results,
        ),
        encoding="utf-8",
    )
    typer.echo(
        json.dumps(
            {
                "run_id": run.run_id,
                "passed": run.gate_decision.passed,
                "json_report": str(json_report),
                "markdown_report": str(markdown_report),
            },
            sort_keys=True,
        )
    )
    if not run.gate_decision.passed:
        raise typer.Exit(code=1)


@app.command("swebench-generate")
def swebench_generate(
    tasks_file: Path,
    output: Annotated[Path, typer.Option(help="Official prediction JSONL destination.")],
    protocol: Annotated[Path, typer.Option(help="Frozen SWE-bench protocol JSON.")],
    repository_locators: Annotated[
        Path, typer.Option(help="Private JSON map from repository IDs to Git locators.")
    ],
    evidence_directory: Annotated[
        Path, typer.Option(help="Private resumable generation evidence root.")
    ],
    workspace_root: Annotated[
        Path, typer.Option(help="Disposable benchmark checkout root.")
    ],
    artifact_root: Annotated[Path, typer.Option(help="Private patch artifact root.")],
    arm: Literal["baseline", "candidate"] = typer.Option(...),
    role: Literal["calibration", "development", "frozen"] = typer.Option(...),
) -> None:
    """Generate resumable predictions for one frozen SWE-bench protocol role."""
    protocol_value = _json_object(protocol, "SWE-bench protocol")
    protocol_digest = protocol_value.get("protocol_digest")
    maximum_spend, maximum_call_cost_cny, rates = _protocol_cost_policy(
        protocol_value
    )
    model_api_style = _protocol_model_api_style(protocol_value)
    (
        max_iterations,
        max_context_rounds,
        max_context_tool_calls,
        max_patch_attempts,
    ) = _protocol_arm_configuration(protocol_value, arm)
    if not isinstance(protocol_digest, str) or not protocol_digest.startswith("sha256:"):
        raise typer.BadParameter("protocol digest is invalid")
    roles = protocol_value.get("task_roles")
    if not isinstance(roles, dict) or not isinstance(roles.get(role), list):
        raise typer.BadParameter(f"protocol does not define task role: {role}")
    selected_ids = roles[role]
    if not selected_ids or any(not isinstance(value, str) for value in selected_ids):
        raise typer.BadParameter("protocol task IDs are invalid")

    parsed_tasks = _read_swebench_tasks(tasks_file)
    tasks_by_id = {task.instance_id: task for task in parsed_tasks}
    if len(tasks_by_id) != len(parsed_tasks):
        raise typer.BadParameter("SWE-bench task IDs must be unique")
    try:
        selected_tasks = [tasks_by_id[instance_id] for instance_id in selected_ids]
    except KeyError as error:
        raise typer.BadParameter(
            f"task file is missing protocol instance: {error.args[0]}"
        ) from error

    locators = _json_object(repository_locators, "repository locator map")
    invalid_locator = any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in locators.items()
    )
    if invalid_locator:
        raise typer.BadParameter("repository locators must map strings to strings")
    previous_spend = _evidence_spend(evidence_directory, protocol_digest)
    remaining = maximum_spend - previous_spend
    if remaining <= 0:
        raise typer.BadParameter("SWE-bench experiment has exhausted its CNY budget")

    settings = Settings(model_api_style=model_api_style)
    ledger = UsageLedger(
        limit_cny=remaining,
        rates=rates,
    )
    patch_agent = RepoAegisPatchAgent(
        model_factory=lambda active_ledger: OpenAIModelGateway.from_settings(
            settings,
            usage_ledger=active_ledger,
            maximum_call_cost_cny=maximum_call_cost_cny,
        ),
        artifact_root=artifact_root,
        max_iterations=max_iterations,
        max_context_rounds=max_context_rounds,
        max_context_tool_calls=max_context_tool_calls,
        max_patch_attempts=max_patch_attempts,
    )
    runtime = GitSWEbenchRuntime(
        repository_locators=locators,
        workspace_root=workspace_root,
        model_name_or_path=settings.openai_model,
        patch_agent=patch_agent,
        runner=ProcessRunner(allowed_executables={"git"}, timeout_seconds=900),
    )
    predictions = asyncio.run(
        run_predictions(
            selected_tasks,
            runtime=runtime,
            ledger=ledger,
            evidence_directory=evidence_directory / arm,
            output_path=output,
            protocol_digest=protocol_digest,
            arm=arm,
        )
    )
    typer.echo(
        json.dumps(
            {
                "arm": arm,
                "role": role,
                "predictions": len(predictions),
                "new_spend_cny": str(ledger.spent_cny),
                "remaining_cny": str(maximum_spend - previous_spend - ledger.spent_cny),
            },
            sort_keys=True,
        )
    )


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


def _json_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise typer.BadParameter(f"{label} must contain a JSON object")
    return value


def _read_swebench_tasks(path: Path) -> list[SWEbenchTask]:
    tasks: list[SWEbenchTask] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            tasks.append(SWEbenchTask.model_validate_json(line))
        except ValueError as error:
            raise typer.BadParameter(f"invalid SWE-bench task at line {line_number}") from error
    return tasks


def _decimal(value: object, label: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except Exception as error:
        raise typer.BadParameter(f"{label} must be a decimal") from error
    if amount <= 0:
        raise typer.BadParameter(f"{label} must be positive")
    return amount


def _nonnegative_decimal(value: object, label: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except Exception as error:
        raise typer.BadParameter(f"{label} must be a decimal") from error
    if amount < 0:
        raise typer.BadParameter(f"{label} must not be negative")
    return amount


def _protocol_cost_policy(
    protocol: dict[str, Any],
) -> tuple[Decimal, Decimal, UsageRates]:
    maximum_spend = _decimal(protocol.get("maximum_spend_cny"), "maximum spend")
    if maximum_spend > Decimal("50"):
        raise typer.BadParameter("protocol maximum spend exceeds the CNY 50 hard limit")
    maximum_call_cost = _decimal(
        protocol.get("maximum_call_cost_cny"), "maximum call cost"
    )
    raw_rates = protocol.get("cost_rates_cny_per_million")
    if not isinstance(raw_rates, dict):
        raise typer.BadParameter("protocol cost rates are required")
    rates = UsageRates(
        cache_hit_input_cny_per_million=_nonnegative_decimal(
            raw_rates.get("cache_hit_input"), "cache-hit input rate"
        ),
        cache_miss_input_cny_per_million=_nonnegative_decimal(
            raw_rates.get("cache_miss_input"), "cache-miss input rate"
        ),
        output_cny_per_million=_nonnegative_decimal(
            raw_rates.get("output"), "output rate"
        ),
    )
    return maximum_spend, maximum_call_cost, rates


def _protocol_model_api_style(
    protocol: dict[str, Any],
) -> Literal["responses", "chat-json"]:
    value = protocol.get("model_api_style")
    if value == "responses":
        return "responses"
    if value == "chat-json":
        return "chat-json"
    raise typer.BadParameter("protocol model API style is invalid")


def _protocol_arm_configuration(
    protocol: dict[str, Any], arm: Literal["baseline", "candidate"]
) -> tuple[int, int, int, int]:
    arms = protocol.get("arms")
    if not isinstance(arms, dict) or not isinstance(arms.get(arm), dict):
        raise typer.BadParameter(f"protocol does not define experiment arm: {arm}")
    arm_value = arms[arm]
    if arm_value.get("status") != "ready":
        raise typer.BadParameter(
            f"SWE-bench {arm} configuration is not finalized after development analysis"
        )
    config = arm_value.get("generation_config")
    if not isinstance(config, dict):
        raise typer.BadParameter(f"SWE-bench {arm} generation config is missing")
    expected_fields = {
        "max_iterations": (1, 10),
        "max_context_rounds": (1, 5),
        "max_context_tool_calls": (1, 20),
        "max_patch_attempts": (1, 5),
    }
    if set(config) != set(expected_fields):
        raise typer.BadParameter(f"SWE-bench {arm} generation config fields are invalid")
    normalized: dict[str, int] = {}
    for field, (minimum, maximum) in expected_fields.items():
        value = config[field]
        if isinstance(value, bool) or not isinstance(value, int):
            raise typer.BadParameter(f"SWE-bench {arm} {field} must be an integer")
        if not minimum <= value <= maximum:
            raise typer.BadParameter(
                f"SWE-bench {arm} {field} must be between {minimum} and {maximum}"
            )
        normalized[field] = value
    canonical = json.dumps(
        normalized, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    if arm_value.get("generation_config_digest") != expected_digest:
        raise typer.BadParameter(f"SWE-bench {arm} generation config digest is invalid")
    return (
        normalized["max_iterations"],
        normalized["max_context_rounds"],
        normalized["max_context_tool_calls"],
        normalized["max_patch_attempts"],
    )


def _evidence_spend(root: Path, protocol_digest: str) -> Decimal:
    total = Decimal("0")
    if not root.exists():
        return total
    for path in root.rglob("*.json"):
        evidence = SWEbenchGenerationEvidence.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if evidence.protocol_digest != protocol_digest:
            raise typer.BadParameter("evidence directory mixes SWE-bench protocols")
        total += evidence.usage.estimated_cost_cny
    return total


if __name__ == "__main__":
    app()
