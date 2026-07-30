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
        token="private-test-token",  # noqa: S106 - explicit non-secret test fixture
        transport=httpx.MockTransport(handler),
    )

    result = client.status("task-1")

    assert result["repo_id"] == "owner/repo"
    assert "private-test-token" not in json.dumps(result)


def test_cli_exposes_documented_control_and_evaluation_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("run", "status", "approve", "resume", "cancel", "evaluate"):
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
