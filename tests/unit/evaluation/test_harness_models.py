from __future__ import annotations

import pytest
from pydantic import ValidationError

from repo_maintenance_agent.evaluation.models import (
    EvaluationCase,
    EvaluationProvenance,
    EvaluationSuite,
    ReleaseGates,
)


def _case(case_id: str = "case-1") -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        repo_id="owner/repository",
        base_commit="a" * 40,
        gold_files=("src/app.py",),
        hidden_test_commands=(("pytest", "-q"),),
    )


def test_suite_rejects_duplicate_case_ids() -> None:
    with pytest.raises(ValidationError, match="case IDs"):
        EvaluationSuite(
            suite_id="core",
            name="Core regression",
            version="2026.07.31",
            cases=(_case(), _case()),
        )


def test_suite_and_provenance_are_strict_and_versioned() -> None:
    suite = EvaluationSuite(
        suite_id="core",
        name="Core regression",
        version="2026.07.31",
        cases=(_case(),),
        concurrency=4,
        max_attempts=2,
        gates=ReleaseGates(resolution_regression_max=0.02),
    )
    provenance = EvaluationProvenance(
        model="gpt-test",
        provider="deterministic",
        prompt_version="prompt-v1",
        tool_schema_version="tools-v1",
        policy_version="policy-v1",
        dataset_version=suite.version,
        environment_fingerprint="python-3.12-windows-amd64",
        seed=17,
    )

    assert suite.case_ids == ("case-1",)
    assert provenance.seed == 17

    with pytest.raises(ValidationError):
        EvaluationProvenance(
            model="gpt-test",
            provider="deterministic",
            prompt_version="prompt-v1",
            tool_schema_version="tools-v1",
            policy_version="policy-v1",
            dataset_version=suite.version,
            environment_fingerprint="fingerprint",
            seed=17,
            unsupported_field="must fail",
        )
