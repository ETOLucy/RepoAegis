from pathlib import Path

import pytest

from repo_maintenance_agent.sandbox.docker import DockerSandbox, SandboxSpec
from repo_maintenance_agent.tools.process import ProcessResult


class RecordingRunner:
    def __init__(self) -> None:
        self.arguments = []
        self.extra_env = None
        self.timeout_seconds = None

    async def run(
        self, arguments, *, cwd, extra_env=None, check=True, timeout_seconds=None
    ):
        del cwd, check
        self.arguments = arguments
        self.extra_env = extra_env
        self.timeout_seconds = timeout_seconds
        return ProcessResult(returncode=0, stdout="", stderr="", duration_ms=1)


def test_docker_command_applies_hardened_defaults(tmp_path: Path) -> None:
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text('{"defaultAction":"SCMP_ACT_ERRNO"}', encoding="utf-8")
    spec = SandboxSpec(
        task_id="task-1",
        workspace=tmp_path,
        image="repo-agent-python@sha256:" + "a" * 64,
        command=("python", "-m", "pytest"),
    )

    command = DockerSandbox(seccomp_profile=seccomp).build_command(spec)
    rendered = " ".join(command)

    assert command[:2] == ["docker", "run"]
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--user=10001:10001" in command
    assert "--pids-limit=256" in command
    assert "--memory=4g" in command
    assert f"--security-opt=seccomp={seccomp.resolve()}" in command
    assert "-e" in command
    assert "HOME=/tmp" in command
    assert any("PIP_INDEX_URL" in item for item in command)
    assert "/var/run/docker.sock" not in rendered
    assert str(tmp_path.resolve()) in rendered


def test_docker_command_rejects_mutable_image_tag(tmp_path: Path) -> None:
    spec = SandboxSpec(
        task_id="task-1",
        workspace=tmp_path,
        image="python:latest",
        command=("python", "-m", "pytest"),
    )

    try:
        DockerSandbox().build_command(spec)
    except ValueError as error:
        assert "digest" in str(error)
    else:
        raise AssertionError("mutable image tag was accepted")


@pytest.mark.asyncio
async def test_docker_execution_uses_request_timeout_and_private_daemon_env(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    sandbox = DockerSandbox(runner, docker_host="tcp://sandbox-daemon:2375")

    await sandbox.execute(
        SandboxSpec(
            task_id="task-1",
            workspace=tmp_path,
            image="python@sha256:" + "a" * 64,
            command=("pytest",),
            timeout_seconds=47,
        )
    )

    assert runner.timeout_seconds == 47
    assert runner.extra_env == {"DOCKER_HOST": "tcp://sandbox-daemon:2375"}
    assert "sandbox-daemon" not in " ".join(runner.arguments)


def test_docker_command_uses_valid_bind_mount_syntax(tmp_path: Path) -> None:
    spec = SandboxSpec(
        task_id="task-1",
        workspace=tmp_path,
        image="repo-agent-python@sha256:" + "a" * 64,
        command=("python", "-m", "pytest"),
    )

    command = DockerSandbox().build_command(spec)
    mount = next(arg for arg in command if arg.startswith("--mount="))

    assert mount.startswith("--mount=type=bind,src=")
    assert ",dst=/workspace" in mount
    assert ",rw" not in mount  # --mount 不接受裸 rw 字段