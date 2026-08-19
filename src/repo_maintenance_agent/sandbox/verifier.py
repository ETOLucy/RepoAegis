from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.domain.models import (
    ErrorKind,
    RepoTaskState,
    VerificationResult,
)
from repo_maintenance_agent.policies.redaction import Redactor
from repo_maintenance_agent.sandbox.docker import SandboxSpec
from repo_maintenance_agent.sandbox.profiles import EnvironmentProfiler, Language
from repo_maintenance_agent.tools.process import ProcessResult
from repo_maintenance_agent.sandbox.failure_parser import parse_failures


class SandboxExecutor(Protocol):
    async def execute(self, spec: SandboxSpec) -> ProcessResult: ...


class SandboxVerifier:
    def __init__(
        self,
        *,
        workspace: Path,
        profiler: EnvironmentProfiler,
        sandbox: SandboxExecutor,
        image_digests: dict[str, str],
        redactor: Redactor | None = None,
        summary_limit: int = 2_000,
    ) -> None:
        self._workspace = workspace.resolve()
        self._profiler = profiler
        self._sandbox = sandbox
        self._image_digests = dict(image_digests)
        self._redactor = redactor or Redactor()
        self._summary_limit = summary_limit

    async def verify(self, task: RepoTaskState) -> VerificationResult:
        return await self.verify_task(task.task_id)

    async def verify_task(self, task_id: str) -> VerificationResult:
        profile = self._profiler.inspect(self._workspace)
        image = self._image_digests.get(profile.image_key)
        if image is None:
            return VerificationResult(
                passed=False,
                error_kind=ErrorKind.ENVIRONMENT,
                summary=f"no immutable sandbox image configured for {profile.image_key}",
            )
        rendered: list[str] = []
        for command in profile.setup_commands:
            rendered.append(json.dumps(command))
            try:
                result = await self._sandbox.execute(
                    SandboxSpec(
                        task_id=task_id,
                        workspace=self._workspace,
                        image=image,
                        command=command,
                        network_enabled=_setup_needs_network(command),
                    )
                )
            except (OSError, ToolExecutionError) as error:
                return VerificationResult(
                    passed=False,
                    error_kind=ErrorKind.INFRASTRUCTURE,
                    commands=tuple(rendered),
                    summary=self._safe_summary(str(error)),
                )
            if result.returncode != 0:
                summary = self._safe_summary(result.stderr or result.stdout)
                failures = parse_failures(summary).failures
                return VerificationResult(
                    passed=False,
                    error_kind=ErrorKind.ENVIRONMENT,
                    commands=tuple(rendered),
                    summary=summary,
                    failures=tuple(
                        {
                            "name": failure.name,
                            "message": failure.message,
                            "location": failure.location,
                        }
                        for failure in failures
                    ),
                )
        commands = profile.test_commands + profile.lint_commands
        for configured_command in commands:
            command = _runtime_command(profile.language, configured_command)
            rendered.append(json.dumps(command))
            try:
                result = await self._sandbox.execute(
                    SandboxSpec(
                        task_id=task_id,
                        workspace=self._workspace,
                        image=image,
                        command=command,
                    )
                )
            except (OSError, ToolExecutionError) as error:
                return VerificationResult(
                    passed=False,
                    error_kind=ErrorKind.INFRASTRUCTURE,
                    commands=tuple(rendered),
                    summary=self._safe_summary(str(error)),
                )
            if result.returncode != 0:
                summary = self._safe_summary(result.stderr or result.stdout)
                failures = parse_failures(summary).failures
                return VerificationResult(
                    passed=False,
                    error_kind=ErrorKind.ENVIRONMENT,
                    commands=tuple(rendered),
                    summary=summary,
                    failures=tuple(
                        {
                            "name": failure.name,
                            "message": failure.message,
                            "location": failure.location,
                        }
                        for failure in failures
                    ),
                )
        return VerificationResult(
            passed=True,
            commands=tuple(rendered),
            summary=f"{len(profile.setup_commands)} setup and {len(commands)} checks passed",
        )

    def _safe_summary(self, value: str) -> str:
        redacted = self._redactor.redact({"message": value})
        return str(redacted["message"])[-self._summary_limit :]


def _setup_needs_network(command: tuple[str, ...]) -> bool:
    return any(token in {"pip", "npm", "mvn", "gradle", "go", "cargo"} for token in command)


def _runtime_command(
    language: Language,
    command: tuple[str, ...],
) -> tuple[str, ...]:
    if language is not Language.PYTHON:
        return command
    executable = command[0]
    if executable == "python":
        return (".repo-agent/venv/bin/python", *command[1:])
    if executable in {"pytest", "ruff", "mypy"}:
        return (f".repo-agent/venv/bin/{executable}", *command[1:])
    return command
