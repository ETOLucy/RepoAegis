from __future__ import annotations

import tempfile
from pathlib import Path, PurePosixPath

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.tools.process import ProcessRunner


class GitPatchApplier:
    def __init__(self, runner: ProcessRunner, *, max_patch_bytes: int = 500_000) -> None:
        self._runner = runner
        self._max_patch_bytes = max_patch_bytes

    async def apply(
        self,
        *,
        workspace: Path,
        patch: bytes,
        declared_files: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not patch or len(patch) > self._max_patch_bytes:
            raise ToolExecutionError("patch size is outside the allowed range")
        allowed = {_safe_relative_path(path) for path in declared_files}
        if not patch.endswith(b"\n"):
            patch = patch + b"\n"
        with tempfile.TemporaryDirectory(prefix="repo-agent-patch-") as temp_dir:
            patch_path = Path(temp_dir) / "proposal.patch"
            patch_path.write_bytes(patch)
            numstat = await self._runner.run(
                ["git", "apply", "--recount", "--numstat", "--", str(patch_path)],
                cwd=workspace,
            )
            actual = _parse_numstat(numstat.stdout)
            undeclared = actual - allowed
            if undeclared:
                raise ToolExecutionError(
                    "patch contains undeclared files: " + ", ".join(sorted(undeclared))
                )
            await self._runner.run(
                [
                    "git",
                    "apply",
                    "--recount",
                    "--check",
                    "--whitespace=nowarn",
                    "--",
                    str(patch_path),
                ],
                cwd=workspace,
            )
            await self._runner.run(
                [
                    "git",
                    "apply",
                    "--recount",
                    "--whitespace=nowarn",
                    "--",
                    str(patch_path),
                ],
                cwd=workspace,
            )
        return tuple(sorted(actual))


def _parse_numstat(output: str) -> set[str]:
    paths: set[str] = set()
    for line in output.splitlines():
        columns = line.split("\t")
        if len(columns) != 3:
            raise ToolExecutionError("git apply returned invalid numstat output")
        paths.add(_safe_relative_path(columns[2]))
    if not paths:
        raise ToolExecutionError("patch does not modify any files")
    return paths


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or ".." in path.parts
        or normalized.startswith("-")
        or "\x00" in normalized
    ):
        raise ToolExecutionError("patch path is unsafe")
    return path.as_posix()
