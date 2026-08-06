import json
from pathlib import Path

import httpx
from typer.testing import CliRunner

from repo_maintenance_agent.cli import ControlPlaneClient, app


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
