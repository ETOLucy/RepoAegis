from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from repo_maintenance_agent.agents.schemas import (
    ContextRequest,
    PatchEdit,
    PatchProposal,
    PlanOutput,
    ReviewOutput,
    TaskSpecOutput,
)
from repo_maintenance_agent.domain.models import RiskLevel
from repo_maintenance_agent.evaluation.models import ModelUsage
from repo_maintenance_agent.evaluation.swebench_runner import (
    GitSWEbenchRuntime,
    RepoAegisPatchAgent,
    SWEbenchDevelopmentFeedback,
    SWEbenchGenerationFailureEvidence,
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
        development_feedback: SWEbenchDevelopmentFeedback | None = None,
    ) -> None:
        del task, ledger, development_feedback
        self.calls += 1
        leftover = workspace / "leftover.txt"
        if self.calls > 1:
            assert not leftover.exists(), "every generation attempt must start clean"
        (workspace / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        leftover.write_text("discard before retry\n", encoding="utf-8")


class GraphFixtureModel:
    def __init__(self) -> None:
        self.inputs: dict[type, list[dict[str, object]]] = {}

    async def structured(self, *, system, input_text, schema, **kwargs):
        del system
        self.inputs.setdefault(schema, []).append(json.loads(input_text))
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
                edits=[
                    PatchEdit(
                        path="app.py",
                        old_text="VALUE = 1",
                        new_text="VALUE = 2",
                    )
                ],
            )
        if schema is ReviewOutput:
            return ReviewOutput(
                decision="approve",
                findings=[],
                summary="The patch matches the public issue.",
            )
        raise AssertionError(f"unexpected schema: {schema}")


class FailIfCalledAgent:
    async def run(self, task, workspace, ledger, development_feedback=None) -> None:
        raise AssertionError("completed prediction must be resumed from evidence")


class FailingUsageRuntime:
    model_name_or_path = "fixture-model"

    def development_feedback_digest(self, instance_id: str) -> str | None:
        del instance_id
        return "sha256:" + "b" * 64

    async def execute(
        self, task: SWEbenchTask, ledger: UsageLedger
    ) -> object:
        del task
        reservation = ledger.reserve(Decimal("0.25"))
        ledger.record(
            ModelUsage(input_cache_miss_tokens=1_000_000),
            reservation_cny=reservation,
        )
        raise RuntimeError("provider rejected sk-private-token\nrequest details")


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
async def test_generate_prediction_rebuilds_incomplete_checkout(tmp_path: Path) -> None:
    repository, commit = _repository(tmp_path)
    task = SWEbenchTask(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit=commit,
        problem_statement="Change VALUE from 1 to 2.",
    )
    workspace_root = tmp_path / "workspaces"
    partial = workspace_root / hashlib.sha256(task.instance_id.encode()).hexdigest()[:24]
    pack_directory = partial / ".git" / "objects" / "pack"
    pack_directory.mkdir(parents=True)
    interrupted_pack = pack_directory / "tmp_pack"
    interrupted_pack.write_bytes(b"partial clone")
    interrupted_pack.chmod(stat.S_IREAD)
    runtime = GitSWEbenchRuntime(
        repository_locators={"owner/repo": str(repository)},
        workspace_root=workspace_root,
        model_name_or_path="fixture-model",
        patch_agent=FixturePatchAgent(),
        runner=ProcessRunner(allowed_executables={"git"}),
    )

    prediction = await generate_prediction(task, runtime, _ledger())

    assert prediction.model_patch.endswith("-VALUE = 1\n+VALUE = 2\n")
    assert _git(partial, "rev-parse", "HEAD") == commit


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
async def test_calibration_feedback_reaches_planning_coding_and_review(
    tmp_path: Path,
) -> None:
    repository, commit = _repository(tmp_path)
    task = SWEbenchTask(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit=commit,
        problem_statement="Change VALUE from 1 to 2.",
    )
    feedback = _feedback(task.instance_id)
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
        development_feedback={task.instance_id: feedback},
    )

    await generate_prediction(task, runtime, _ledger())

    expected = feedback.model_dump(mode="json")
    assert model.inputs[PlanOutput][0]["repo_profile"] == {
        "development_feedback": expected,
        "retrieval_count": 0,
        "retrieved_files": [],
    }
    for schema in (ContextRequest, PatchProposal, ReviewOutput):
        assert model.inputs[schema][0]["development_feedback"] == expected


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


@pytest.mark.asyncio
async def test_prediction_evidence_rejects_changed_development_feedback(
    tmp_path: Path,
) -> None:
    repository, commit = _repository(tmp_path)
    task = SWEbenchTask(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit=commit,
        problem_statement="Change VALUE from 1 to 2.",
    )
    evidence = tmp_path / "evidence"
    output = tmp_path / "predictions.jsonl"
    first_feedback = _feedback(task.instance_id)
    runtime = GitSWEbenchRuntime(
        repository_locators={"owner/repo": str(repository)},
        workspace_root=tmp_path / "workspaces",
        model_name_or_path="fixture-model",
        patch_agent=FixturePatchAgent(),
        runner=ProcessRunner(allowed_executables={"git"}),
        development_feedback={task.instance_id: first_feedback},
    )
    await run_predictions(
        [task],
        runtime=runtime,
        ledger=_ledger(),
        evidence_directory=evidence,
        output_path=output,
        protocol_digest="sha256:" + "a" * 64,
        arm="baseline",
    )
    changed_feedback = first_feedback.model_copy(
        update={"summary": "The target test failed for a different reason."}
    )
    resumed_runtime = GitSWEbenchRuntime(
        repository_locators={"owner/repo": str(repository)},
        workspace_root=tmp_path / "unused-workspaces",
        model_name_or_path="fixture-model",
        patch_agent=FailIfCalledAgent(),
        runner=ProcessRunner(allowed_executables={"git"}),
        development_feedback={task.instance_id: changed_feedback},
    )

    with pytest.raises(
        ValueError, match="saved SWE-bench evidence does not match this run"
    ):
        await run_predictions(
            [task],
            runtime=resumed_runtime,
            ledger=_ledger(),
            evidence_directory=evidence,
            output_path=output,
            protocol_digest="sha256:" + "a" * 64,
            arm="baseline",
        )


@pytest.mark.asyncio
async def test_prediction_failure_persists_usage_and_redacts_credentials(
    tmp_path: Path,
) -> None:
    task = SWEbenchTask(
        instance_id="owner__repo-1",
        repo="owner/repo",
        base_commit="a" * 40,
        problem_statement="Change VALUE from 1 to 2.",
    )
    evidence_directory = tmp_path / "evidence"
    ledger = UsageLedger(
        limit_cny=Decimal("1"),
        rates=UsageRates(
            cache_hit_input_cny_per_million=Decimal("0"),
            cache_miss_input_cny_per_million=Decimal("0.1"),
            output_cny_per_million=Decimal("0"),
        ),
    )

    with pytest.raises(RuntimeError, match="provider rejected"):
        await run_predictions(
            [task],
            runtime=FailingUsageRuntime(),
            ledger=ledger,
            evidence_directory=evidence_directory,
            output_path=tmp_path / "predictions.jsonl",
            protocol_digest="sha256:" + "a" * 64,
            arm="candidate",
        )

    failure_paths = list(evidence_directory.glob("*.failure-*.json"))
    assert len(failure_paths) == 1
    failure = SWEbenchGenerationFailureEvidence.model_validate_json(
        failure_paths[0].read_text(encoding="utf-8")
    )
    assert failure.runtime_completed is False
    assert failure.usage.estimated_cost_cny == Decimal("0.1")
    assert failure.usage.input_cache_miss_tokens == 1_000_000
    assert failure.error_type == "RuntimeError"
    assert failure.error_summary == "provider rejected [REDACTED]"
    assert failure.development_feedback_digest == "sha256:" + "b" * 64

    with pytest.raises(RuntimeError, match="provider rejected"):
        await run_predictions(
            [task],
            runtime=FailingUsageRuntime(),
            ledger=ledger,
            evidence_directory=evidence_directory,
            output_path=tmp_path / "predictions.jsonl",
            protocol_digest="sha256:" + "a" * 64,
            arm="candidate",
        )

    assert len(list(evidence_directory.glob("*.failure-*.json"))) == 2


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


def _feedback(instance_id: str) -> SWEbenchDevelopmentFeedback:
    return SWEbenchDevelopmentFeedback(
        instance_id=instance_id,
        source_run_id="repoaegis-smoke-v3b",
        prediction_digest="sha256:" + "a" * 64,
        official_report_digest="sha256:" + "b" * 64,
        failing_tests=("tests/test_value.py::test_value",),
        summary="The target test still observed VALUE = 1.",
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

