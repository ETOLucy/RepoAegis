from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import ErrorKind, RepoTaskState
from repo_maintenance_agent.sandbox.profiles import EnvironmentProfiler
from repo_maintenance_agent.sandbox.verifier import SandboxVerifier
from repo_maintenance_agent.tools.process import ProcessResult


def task() -> RepoTaskState:
    return RepoTaskState(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        base_branch="main",
        issue={"title": "Fix", "body": "Details"},
    )


class FakeSandbox:
    def __init__(self, returncodes: list[int]) -> None:
        self.returncodes = returncodes
        self.commands: list[tuple[str, ...]] = []
        self.network_modes: list[bool] = []

    async def execute(self, spec):
        self.commands.append(spec.command)
        self.network_modes.append(spec.network_enabled)
        return ProcessResult(
            returncode=self.returncodes.pop(0),
            stdout="output",
            stderr="failure",
            duration_ms=10,
        )


@pytest.mark.asyncio
async def test_verifier_runs_profile_tests_and_lints_in_hardened_sandbox(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    sandbox = FakeSandbox([0, 0, 0, 0, 0])
    verifier = SandboxVerifier(
        workspace=tmp_path,
        profiler=EnvironmentProfiler(),
        sandbox=sandbox,
        image_digests={"python-3.12": "python@sha256:" + "a" * 64},
        summary_limit=8_000,
    )

    result = await verifier.verify(task())

    assert result.passed
    assert result.error_kind is None
    assert result.summary == "2 setup and 3 checks passed"
    # 默认截断仍是 2000,可配置后行为不变;此用例验证参数透传
    assert sandbox.commands[0][:3] == ("python", "-m", "venv")
    assert (".repo-agent/venv/bin/python", "-m", "pytest") in sandbox.commands
    assert (".repo-agent/venv/bin/ruff", "check", ".") in sandbox.commands
    assert sandbox.network_modes == [False, True, False, False, False]


@pytest.mark.asyncio
async def test_verifier_classifies_nonzero_test_as_code_failure(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    sandbox = FakeSandbox([0, 0, 1])
    verifier = SandboxVerifier(
        workspace=tmp_path,
        profiler=EnvironmentProfiler(),
        sandbox=sandbox,
        image_digests={"python-3.12": "python@sha256:" + "a" * 64},
    )

    result = await verifier.verify(task())

    assert not result.passed
    assert result.error_kind is ErrorKind.CODE
    assert "failure" in result.summary
