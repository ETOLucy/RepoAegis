from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from repo_maintenance_agent.domain.errors import ToolExecutionError

_SAFE_ENV_KEYS = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "HOME",
        "USERPROFILE",
    }
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int


class ProcessRunner:
    def __init__(
        self,
        *,
        allowed_executables: set[str],
        timeout_seconds: float = 300,
        max_output_bytes: int = 1_000_000,
    ) -> None:
        self._allowed_executables = {name.casefold() for name in allowed_executables}
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes

    async def run(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        extra_env: dict[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        check: bool = True,
        timeout_seconds: float | None = None,
    ) -> ProcessResult:
        if not arguments:
            raise ToolExecutionError("process argument list cannot be empty")
        executable = Path(arguments[0]).name.casefold()
        if executable not in self._allowed_executables:
            raise ToolExecutionError(f"executable is not allowlisted: {executable}")
        resolved_cwd = cwd.resolve()
        if not resolved_cwd.is_dir():
            raise ToolExecutionError("working directory does not exist")

        env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV_KEYS}
        if extra_env:
            forbidden = [key for key in extra_env if _looks_sensitive(key)]
            if forbidden:
                raise ToolExecutionError("sensitive environment variables require a secret broker")
            env.update(extra_env)
        if secret_env:
            env.update(secret_env)

        started = monotonic()
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=resolved_cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds or self._timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            effective_timeout = timeout_seconds or self._timeout_seconds
            raise TimeoutError(f"process exceeded {effective_timeout} seconds") from None

        if len(stdout_bytes) + len(stderr_bytes) > self._max_output_bytes:
            raise ToolExecutionError("process output exceeded configured limit")
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        result = ProcessResult(
            returncode=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((monotonic() - started) * 1000),
        )
        if check and result.returncode != 0:
            raise ToolExecutionError(
                f"process exited with code {result.returncode}: {stderr[-2_000:].strip()}"
            )
        return result


def _looks_sensitive(key: str) -> bool:
    normalized = key.upper()
    return any(part in normalized for part in ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE"))
