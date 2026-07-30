from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from repo_maintenance_agent.api.app import create_app
from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.evaluation.storage import InMemoryEvaluationRepository
from repo_maintenance_agent.storage.memory import InMemoryTaskRepository


@asynccontextmanager
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app(
        repository=InMemoryTaskRepository(),
        evaluation_repository=InMemoryEvaluationRepository(),
        authenticator=StaticTokenAuthenticator(
            {
                "token-a": Principal(tenant_id="tenant-a", subject="reviewer-a"),
                "token-b": Principal(tenant_id="tenant-b", subject="reviewer-b"),
            }
        ),
        production=False,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as api:
        yield api


def _headers(identity: str = "token-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {identity}"}


def _payload() -> dict[str, object]:
    return {
        "suite": {
            "suite_id": "core",
            "name": "Core regression",
            "version": "v1",
            "concurrency": 2,
            "max_attempts": 2,
            "cases": [
                {
                    "case_id": "case-1",
                    "repo_id": "owner/repo",
                    "base_commit": "a" * 40,
                    "gold_files": ["src/app.py"],
                    "hidden_test_commands": [["pytest", "-q"]],
                    "timeout_seconds": 5,
                }
            ],
        },
        "candidate_label": "candidate",
        "provenance": {
            "model": "deterministic",
            "provider": "fixture",
            "prompt_version": "p1",
            "tool_schema_version": "t1",
            "policy_version": "policy1",
            "dataset_version": "v1",
            "environment_fingerprint": "ci-linux-amd64",
            "seed": 7,
        },
        "observations": {
            "case-1": {
                "retrieved_files": ["src/app.py"],
                "hidden_tests_passed": True,
                "regression": False,
                "total_tool_calls": 4,
                "denied_tool_calls": 0,
                "wall_clock_ms": 125,
                "model_calls": 1,
                "input_tokens": 100,
                "output_tokens": 25,
            }
        },
    }


async def test_evaluation_run_create_list_detail_and_exports_are_tenant_scoped() -> None:
    async with client() as api:
        created = await api.post(
            "/v1/evaluations/runs",
            json=_payload(),
            headers=_headers(),
        )
        run_id = created.json()["run_id"]
        listed = await api.get("/v1/evaluations/runs?limit=10", headers=_headers())
        loaded = await api.get(f"/v1/evaluations/runs/{run_id}", headers=_headers())
        hidden = await api.get(
            f"/v1/evaluations/runs/{run_id}",
            headers=_headers("token-b"),
        )
        markdown = await api.get(
            f"/v1/evaluations/runs/{run_id}/report.md",
            headers=_headers(),
        )
        exported = await api.get(
            f"/v1/evaluations/runs/{run_id}/report.json",
            headers=_headers(),
        )

    assert created.status_code == 201
    assert created.json()["gate_decision"]["passed"] is True
    assert listed.status_code == 200
    assert listed.json()["items"][0]["run_id"] == run_id
    assert loaded.status_code == 200
    assert hidden.status_code == 404
    assert "tenant_id" not in loaded.text
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "# Evaluation Report" in markdown.text
    assert exported.json()["run_id"] == run_id
    assert "tenant_id" not in exported.text


async def test_evaluation_replay_creates_a_distinct_run() -> None:
    async with client() as api:
        created = await api.post(
            "/v1/evaluations/runs",
            json=_payload(),
            headers=_headers(),
        )
        run_id = created.json()["run_id"]
        replayed = await api.post(
            f"/v1/evaluations/runs/{run_id}/replay",
            json={"case_ids": ["case-1"]},
            headers=_headers(),
        )

    assert replayed.status_code == 201
    assert replayed.json()["run_id"] != run_id
    assert replayed.json()["replay_of_run_id"] == run_id
    assert replayed.json()["selected_case_ids"] == ["case-1"]


async def test_evaluation_create_requires_exact_observation_set_and_strict_input() -> None:
    body = _payload()
    body["observations"] = {}
    body["tenant_id"] = "attacker"

    async with client() as api:
        response = await api.post(
            "/v1/evaluations/runs",
            json=body,
            headers=_headers(),
        )

    assert response.status_code == 422
