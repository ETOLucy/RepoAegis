from __future__ import annotations

import hashlib
import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from repo_maintenance_agent.agents.schemas import (
    ContextRequest,
    PatchProposal,
    PlanOutput,
    ReviewOutput,
    TaskSpecOutput,
)
from repo_maintenance_agent.domain.models import RiskLevel
from repo_maintenance_agent.evaluation.swebench_runner import (
    GitSWEbenchRuntime,
    RepoAegisPatchAgent,
    SWEbenchDevelopmentFeedback,
    SWEbenchTask,
    generate_prediction,
    run_predictions,
)
from repo_maintenance_agent.models.usage import UsageLedger, UsageRates
from repo_maintenance_agent.tools.process import ProcessRunner


class FixturePatchAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        task: SWEbenchTask,
        workspace: Path,
        ledger: UsageLedger,
    ) -> None:
        del task, ledger
        self.calls += 1
        leftover = workspace / "leftover.txt"
        if self.calls > 1:
            assert not leftover.exists(), "every generation attempt must start clean"
        (workspace / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        leftover.write_text("discard before retry\n", encoding="utf-8")


class GraphFixtureModel:
    async def structured(self, *, system, input_text, schema):
        del system, input_text
        if schema is TaskSpecOutput:
            return TaskSpecOutput(
                task_type="bugfix",
                summary="Change the fixture value.",
                acceptance_criteria=["VALUE is 2"],
            )
        if schema is PlanOutput:
            return PlanOutput(
                steps=[
                    {
                        "description": "Update the value.",
                        "paths": ["app.py"],
                        "verification": "Official SWE-bench harness",
                    }
                ],
                risk=RiskLevel.LOW,
            )
        if schema is ContextRequest:
            return ContextRequest(ready_to_patch=True, reason="Search evidence is enough.")
        if schema is PatchProposal:
            return PatchProposal(
                summary="Update the value.",
                unified_diff=(
                    "diff --git a/app.py b/app.py\n"
                    "--- a/app.py\n"
                    "+++ b/app.py\n"
                    "@@ -1 +1 @@\n"
                    "-VALUE = 1\n"
                    "+VALUE = 2\n"
                ),
                changed_files=["app.py"],
            )
        if schema is ReviewOutput:
            return ReviewOutput(
                decision="approve",
                findings=[],
                summary="The patch matches the public issue.",
            )
        raise AssertionError(f"unexpected schema: {schema}")


class FailIfCalledAgent:
    async def run(self, task, workspace, ledger) -> None:
        raise AssertionError("completed prediction must be resumed from evidence")


@pytest.mark.asyncio
async def test_generate_prediction_uses_clean_checkout_and_exact_official_contract(
    tmp_path: Path,
) -> None:
    repository, commit = _repository(tmp_path)
    agent = FixturePatchAgent()
    runtime = GitSWEbenchRuntime(
        repository_locators={"owner/repo": str(repository)},
        workspace_root=tmp_path / "workspaces",
        model_name_or_path="fixture-model",
        patch_agent=agent,
        runner=ProcessRunner(allowed_executables={"git"}),
    )
    task = SWEbenchTask(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit=commit,
        problem_statement="Change VALUE from 1 to 2.",
    )
    ledger = _ledger()

    first = await generate_prediction(task, runtime, ledger)
    second = await generate_prediction(task, runtime, ledger)

    assert first == second
    assert first.model_dump() == {
        "instance_id": "owner__repo-1",
        "model_patch": (
            "diff --git a/app.py b/app.py\n"
            "index b15b1b0..c8fcecd 100644\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1 +1 @@\n"
            "-VALUE = 1\n"
            "+VALUE = 2\n"
        ),
        "model_name_or_path": "fixture-model",
    }
    assert agent.calls == 2


@pytest.mark.asyncio
async def test_repoaegis_patch_agent_reuses_the_real_agent_nodes(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    model = GraphFixtureModel()
    runtime = GitSWEbenchRuntime(
        repository_locators={"owner/repo": str(repository)},
        workspace_root=tmp_path / "workspaces",
        model_name_or_path="fixture-model",
        patch_agent=RepoAegisPatchAgent(
            model_factory=lambda ledger: model,
            artifact_root=tmp_path / "artifacts",
        ),
        runner=ProcessRunner(allowed_executables={"git"}),
    )
    task = SWEbenchTask(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit=commit,
        problem_statement="Change VALUE from 1 to 2.",
    )

    prediction = await generate_prediction(task, runtime, _ledger())

    assert prediction.model_patch.endswith("-VALUE = 1\n+VALUE = 2\n")


@pytest.mark.asyncio
async def test_prediction_evidence_resumes_without_regenerating(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    task = SWEbenchTask(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit=commit,
        problem_statement="Change VALUE from 1 to 2.",
    )
    output = tmp_path / "predictions.jsonl"
    evidence = tmp_path / "evidence"
    runtime = GitSWEbenchRuntime(
        repository_locators={"owner/repo": str(repository)},
        workspace_root=tmp_path / "workspaces",
        model_name_or_path="fixture-model",
        patch_agent=FixturePatchAgent(),
        runner=ProcessRunner(allowed_executables={"git"}),
    )

    first = await run_predictions(
        [task],
        runtime=runtime,
        ledger=_ledger(),
        evidence_directory=evidence,
        output_path=output,
        protocol_digest="sha256:" + "a" * 64,
        arm="baseline",
    )
    resumed_runtime = GitSWEbenchRuntime(
        repository_locators={"owner/repo": str(repository)},
        workspace_root=tmp_path / "unused-workspaces",
        model_name_or_path="fixture-model",
        patch_agent=FailIfCalledAgent(),
        runner=ProcessRunner(allowed_executables={"git"}),
    )
    second = await run_predictions(
        [task],
        runtime=resumed_runtime,
        ledger=_ledger(),
        evidence_directory=evidence,
        output_path=output,
        protocol_digest="sha256:" + "a" * 64,
        arm="baseline",
    )

    assert second == first
    assert len(list(evidence.glob("*.json"))) == 1
    assert output.read_text(encoding="utf-8").count("\n") == 1


def test_swebench_task_rejects_answer_and_hidden_test_fields() -> None:
    safe = {
        "instance_id": "owner__repo-1",
        "repo": "owner/repo",
        "base_commit": "a" * 40,
        "problem_statement": "Fix the public issue.",
    }

    for forbidden in ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            SWEbenchTask.model_validate(safe | {forbidden: "must not reach the agent"})


def test_development_feedback_has_stable_digest_and_rejects_benchmark_answers() -> None:
    payload = {
        "instance_id": "owner__repo-1",
        "source_run_id": "repoaegis-smoke-v3b",
        "prediction_digest": "sha256:" + "a" * 64,
        "official_report_digest": "sha256:" + "b" * 64,
        "failing_tests": ["tests/test_value.py::test_value"],
        "summary": "The target test still observed VALUE = 1.",
    }
    feedback = SWEbenchDevelopmentFeedback.model_validate(payload)
    canonical = json.dumps(
        feedback.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()

    assert feedback.digest() == "sha256:" + hashlib.sha256(canonical).hexdigest()
    for forbidden in ("patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"):
        with pytest.raises(ValidationError, match="extra_forbidden"):
            SWEbenchDevelopmentFeedback.model_validate(
                payload | {forbidden: "must not reach the agent"}
            )


def _ledger() -> UsageLedger:
    return UsageLedger(
        limit_cny=Decimal("1"),
        rates=UsageRates(
            cache_hit_input_cny_per_million=Decimal("0"),
            cache_miss_input_cny_per_million=Decimal("0"),
            output_cny_per_million=Decimal("0"),
        ),
    )


def _repository(root: Path) -> tuple[Path, str]:
    repository = root / "source"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "Fixture")
    (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repository, "add", "app.py")
    _git(repository, "commit", "-m", "fixture")
    return repository, _git(repository, "rev-parse", "HEAD")


def _git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()
