import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from repo_maintenance_agent.cli import (
    ControlPlaneClient,
    _evidence_spend,
    _protocol_arm_configuration,
    _protocol_cost_policy,
    _protocol_model_api_style,
    _read_development_feedback,
    _validated_protocol_digest,
    app,
)
from repo_maintenance_agent.evaluation.models import ModelUsage
from repo_maintenance_agent.evaluation.swebench import SWEbenchPrediction
from repo_maintenance_agent.evaluation.swebench_runner import (
    SWEbenchGenerationEvidence,
    SWEbenchGenerationFailureEvidence,
)


def test_doctor_reports_key_availability_without_printing_secret(monkeypatch) -> None:
    fake_key = "sk-" + "not-a-real-key-value"
    monkeypatch.setenv("OPENAI_API_KEY", fake_key)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "openai_credentials_available: true" in result.stdout
    assert fake_key not in result.stdout


def test_control_plane_client_sends_bearer_token_without_exposing_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer private-test-token"
        assert request.url.path == "/v1/tasks/task-1"
        return httpx.Response(
            200,
            json={
                "task_id": "task-1",
                "repo_id": "owner/repo",
                "status": "pending",
            },
        )

    client = ControlPlaneClient(
        base_url="https://agent.example.invalid",
        token="private-test-token",
        transport=httpx.MockTransport(handler),
    )

    result = client.status("task-1")

    assert result["repo_id"] == "owner/repo"
    assert "private-test-token" not in json.dumps(result)


def test_control_plane_client_binds_approval_to_target_and_tools() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["plan_hash"] == "b" * 64
        assert body["target_commit"] == "a" * 40
        assert body["allowed_tools"] == ["repo_read", "git_write"]
        return httpx.Response(200, json={"status": "coding"})

    client = ControlPlaneClient(
        base_url="https://agent.example.invalid",
        token="private-test-token",
        transport=httpx.MockTransport(handler),
    )

    result = client.decide(
        "task-1",
        approved=True,
        plan_hash="b" * 64,
        target_commit="a" * 40,
        allowed_tools=["repo_read", "git_write"],
        reason="Reviewed complete scope.",
    )

    assert result["status"] == "coding"


def test_cli_exposes_documented_control_and_evaluation_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "RepoAegis control plane" in result.stdout
    for command in (
        "run",
        "status",
        "approve",
        "resume",
        "cancel",
        "evaluate",
        "evaluate-suite",
        "swebench-generate",
    ):
        assert command in result.stdout


def test_evaluate_rejects_string_boolean_observations(tmp_path: Path) -> None:
    case_file = tmp_path / "case.json"
    result_file = tmp_path / "result.json"
    case_file.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "repo_id": "owner/repo",
                "base_commit": "a" * 40,
                "gold_files": ["src/app.py"],
                "hidden_test_commands": [["pytest"]],
            }
        ),
        encoding="utf-8",
    )
    result_file.write_text(
        json.dumps({"hidden_tests_passed": "false"}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["evaluate", str(case_file), str(result_file)])

    assert result.exit_code != 0
    assert result.exception is not None


def test_swebench_cost_policy_is_bound_to_the_frozen_protocol() -> None:
    maximum_spend, maximum_call_cost, rates = _protocol_cost_policy(
        {
            "maximum_spend_cny": "50",
            "maximum_call_cost_cny": "0.25",
            "cost_rates_cny_per_million": {
                "cache_hit_input": "0.028",
                "cache_miss_input": "0.14",
                "output": "0.28",
            },
        }
    )

    assert maximum_spend == Decimal("50")
    assert maximum_call_cost == Decimal("0.25")
    assert rates.cache_hit_input_cny_per_million == Decimal("0.028")
    assert rates.cache_miss_input_cny_per_million == Decimal("0.14")
    assert rates.output_cny_per_million == Decimal("0.28")


def test_swebench_model_api_style_is_bound_to_the_frozen_protocol() -> None:
    import pytest

    assert _protocol_model_api_style({"model_api_style": "chat-json"}) == "chat-json"
    assert _protocol_model_api_style({"model_api_style": "responses"}) == "responses"
    with pytest.raises(Exception, match="model API style"):
        _protocol_model_api_style({})
    with pytest.raises(Exception, match="model API style"):
        _protocol_model_api_style({"model_api_style": "unbound"})


def test_swebench_protocol_digest_rejects_body_tampering() -> None:
    import hashlib

    import pytest

    body = {
        "schema_version": "swebench-protocol/v1",
        "model_api_style": "chat-json",
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    protocol = {**body, "protocol_digest": digest}

    assert _validated_protocol_digest(protocol) == digest
    with pytest.raises(Exception, match="protocol digest"):
        _validated_protocol_digest(protocol | {"model_api_style": "responses"})


def test_swebench_arm_configuration_is_digest_bound_and_ready() -> None:
    config = {
        "max_context_rounds": 1,
        "max_context_tool_calls": 8,
        "max_iterations": 3,
        "max_patch_attempts": 2,
    }
    import hashlib

    canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()

    assert _protocol_arm_configuration(
        {
            "arms": {
                "baseline": {
                    "status": "ready",
                    "generation_config": config,
                    "generation_config_digest": digest,
                }
            }
        },
        "baseline",
    ) == (3, 1, 8, 2)


def test_swebench_arm_configuration_rejects_pending_candidate() -> None:
    import pytest

    with pytest.raises(Exception, match="not finalized"):
        _protocol_arm_configuration(
            {"arms": {"candidate": {"status": "pending_development_analysis"}}},
            "candidate",
        )


def test_swebench_development_feedback_is_rejected_for_frozen_role(
    tmp_path: Path,
) -> None:
    feedback = _feedback_file(tmp_path, ["owner__repo-1"])

    with pytest.raises(Exception, match=r"frozen.*feedback"):
        _read_development_feedback(
            feedback,
            selected_ids=["owner__repo-1"],
            role="frozen",
        )


def test_swebench_development_feedback_requires_exact_unique_selected_tasks(
    tmp_path: Path,
) -> None:
    duplicate = _feedback_file(tmp_path, ["owner__repo-1", "owner__repo-1"])
    with pytest.raises(Exception, match="unique"):
        _read_development_feedback(
            duplicate,
            selected_ids=["owner__repo-1"],
            role="calibration",
        )

    unselected = _feedback_file(tmp_path, ["owner__repo-2"])
    with pytest.raises(Exception, match="selected tasks"):
        _read_development_feedback(
            unselected,
            selected_ids=["owner__repo-1"],
            role="development",
        )


def test_swebench_evidence_spend_includes_failed_generation(tmp_path: Path) -> None:
    protocol_digest = "sha256:" + "a" * 64
    usage = ModelUsage(estimated_cost_cny=Decimal("0.125"))
    success = SWEbenchGenerationEvidence(
        protocol_digest=protocol_digest,
        arm="candidate",
        instance_id="owner__repo-1",
        model_name_or_path="fixture-model",
        prediction=SWEbenchPrediction(
            instance_id="owner__repo-1",
            model_patch="diff --git a/app.py b/app.py\n",
            model_name_or_path="fixture-model",
        ),
        usage=usage,
        latency_ms=10,
    )
    failure = SWEbenchGenerationFailureEvidence(
        protocol_digest=protocol_digest,
        arm="candidate",
        instance_id="owner__repo-2",
        model_name_or_path="fixture-model",
        usage=usage,
        latency_ms=20,
        error_type="RuntimeError",
        error_summary="patch did not apply",
    )
    (tmp_path / "success.json").write_text(success.model_dump_json(), encoding="utf-8")
    (tmp_path / "failure.json").write_text(failure.model_dump_json(), encoding="utf-8")

    assert _evidence_spend(tmp_path, protocol_digest) == Decimal("0.250")


def test_evaluate_suite_writes_json_and_markdown_reports(tmp_path: Path) -> None:
    suite_file, observations_file = _suite_files(tmp_path, hidden_tests_passed=True)
    json_report = tmp_path / "report.json"
    markdown_report = tmp_path / "report.md"

    result = CliRunner().invoke(
        app,
        [
            "evaluate-suite",
            str(suite_file),
            str(observations_file),
            "--json-report",
            str(json_report),
            "--markdown-report",
            str(markdown_report),
            "--candidate-label",
            "fixture-candidate",
        ],
    )

    payload = json.loads(json_report.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert payload["candidate_label"] == "fixture-candidate"
    assert payload["gate_decision"]["passed"] is True
    assert "tenant_id" not in payload
    assert "# Evaluation Report" in markdown_report.read_text(encoding="utf-8")


def test_evaluate_suite_returns_failure_exit_code_after_writing_evidence(
    tmp_path: Path,
) -> None:
    suite_file, observations_file = _suite_files(tmp_path, hidden_tests_passed=False)
    json_report = tmp_path / "report.json"
    markdown_report = tmp_path / "report.md"

    result = CliRunner().invoke(
        app,
        [
            "evaluate-suite",
            str(suite_file),
            str(observations_file),
            "--json-report",
            str(json_report),
            "--markdown-report",
            str(markdown_report),
        ],
    )

    assert result.exit_code == 1
    assert json_report.exists()
    assert markdown_report.exists()


def _suite_files(
    root: Path,
    *,
    hidden_tests_passed: bool,
) -> tuple[Path, Path]:
    suite_file = root / "suite.json"
    observations_file = root / "observations.json"
    suite_file.write_text(
        json.dumps(
            {
                "suite_id": "fixture",
                "name": "Fixture suite",
                "version": "v1",
                "cases": [
                    {
                        "case_id": "case-1",
                        "repo_id": "owner/repo",
                        "base_commit": "a" * 40,
                        "gold_files": ["src/app.py"],
                        "hidden_test_commands": [["pytest"]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    observations_file.write_text(
        json.dumps(
            {
                "case-1": {
                    "retrieved_files": ["src/app.py"],
                    "hidden_tests_passed": hidden_tests_passed,
                    "regression": False,
                    "total_tool_calls": 2,
                    "denied_tool_calls": 0,
                    "wall_clock_ms": 10,
                    "model_calls": 1,
                    "input_tokens": 20,
                    "output_tokens": 5,
                }
            }
        ),
        encoding="utf-8",
    )
    return suite_file, observations_file


def _feedback_file(root: Path, instance_ids: list[str]) -> Path:
    path = root / f"feedback-{len(list(root.glob('feedback-*.jsonl')))}.jsonl"
    records = [
        {
            "instance_id": instance_id,
            "source_run_id": "repoaegis-smoke-v3b",
            "prediction_digest": "sha256:" + "a" * 64,
            "official_report_digest": "sha256:" + "b" * 64,
            "failing_tests": ["tests/test_value.py::test_value"],
            "summary": "The target test still observed VALUE = 1.",
        }
        for instance_id in instance_ids
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )
    return path
