from pathlib import Path

from repo_maintenance_agent.sandbox.docker import DockerSandbox, SandboxSpec


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
