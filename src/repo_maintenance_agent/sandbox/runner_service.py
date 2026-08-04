from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.sandbox.docker import DockerSandbox
from repo_maintenance_agent.sandbox.runner_api import create_sandbox_runner_app
from repo_maintenance_agent.tools.process import ProcessRunner


def build_application() -> FastAPI:
    settings = Settings()
    if settings.sandbox_runner_token is None:
        raise RuntimeError("sandbox runner token is required")
    sandbox = DockerSandbox(
        ProcessRunner(allowed_executables={"docker"}, timeout_seconds=1_810),
        seccomp_profile=settings.sandbox_seccomp_profile,
        docker_host=settings.sandbox_docker_host,
    )
    return create_sandbox_runner_app(
        sandbox=sandbox,
        token=settings.sandbox_runner_token,
        workspace_root=Path(settings.workspace_root),
    )
