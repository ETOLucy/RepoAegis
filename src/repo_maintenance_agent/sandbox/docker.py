from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from repo_maintenance_agent.tools.process import ProcessResult, ProcessRunner

_DIGEST_IMAGE = re.compile(r"^[A-Za-z0-9._/-]+@sha256:[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    task_id: str
    workspace: Path
    image: str
    command: tuple[str, ...]
    cpu_limit: str = "2"
    memory_limit: str = "4g"
    pids_limit: int = 256
    timeout_seconds: int = 900
    network_enabled: bool = False


class DockerSandbox:
    def __init__(
        self,
        runner: ProcessRunner | None = None,
        *,
        seccomp_profile: Path | None = None,
        docker_host: str | None = None,
    ) -> None:
        self._runner = runner
        if seccomp_profile is not None and not seccomp_profile.resolve().is_file():
            raise ValueError("seccomp profile must be an existing file")
        self._seccomp_profile = seccomp_profile.resolve() if seccomp_profile else None
        self._docker_host = docker_host

    def build_command(self, spec: SandboxSpec) -> list[str]:
        if not _DIGEST_IMAGE.fullmatch(spec.image):
            raise ValueError("sandbox image must be pinned by sha256 digest")
        workspace = spec.workspace.resolve()
        if not workspace.is_dir():
            raise ValueError("sandbox workspace must exist")
        if not spec.command:
            raise ValueError("sandbox command cannot be empty")
        network = "bridge" if spec.network_enabled else "none"
        security_options = ["--security-opt=no-new-privileges"]
        if self._seccomp_profile is not None:
            security_options.append(
                f"--security-opt=seccomp={self._seccomp_profile}"
            )
        return [
            "docker",
            "run",
            "--rm",
            "--init",
            f"--name=repo-agent-{_safe_task_name(spec.task_id)}",
            f"--network={network}",
            "--read-only",
            "--user=10001:10001",
            "--cap-drop=ALL",
            *security_options,
            f"--cpus={spec.cpu_limit}",
            f"--memory={spec.memory_limit}",
            f"--pids-limit={spec.pids_limit}",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=512m",
            f"--mount=type=bind,src={workspace},dst=/workspace",
            "--workdir=/workspace",
            "-e",
            "HOME=/tmp",
            "-e",
            "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple",
            spec.image,
            *spec.command,
        ]

    async def execute(self, spec: SandboxSpec) -> ProcessResult:
        if self._runner is None:
            raise RuntimeError("DockerSandbox requires a ProcessRunner for execution")
        return await self._runner.run(
            self.build_command(spec),
            cwd=spec.workspace,
            extra_env={"DOCKER_HOST": self._docker_host} if self._docker_host else None,
            check=False,
            timeout_seconds=spec.timeout_seconds,
        )


def _safe_task_name(task_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]", "-", task_id)[:50].strip(".-")
    if not value:
        raise ValueError("task_id cannot produce a safe container name")
    return value
