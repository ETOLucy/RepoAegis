import sys
from pathlib import Path

import pytest

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.tools.process import ProcessRunner


@pytest.mark.asyncio
async def test_process_runner_passes_arguments_without_shell_interpolation(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    runner = ProcessRunner(allowed_executables={Path(sys.executable).name}, timeout_seconds=5)

    result = await runner.run(
        [
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            f"hello; touch {marker}",
        ],
        cwd=tmp_path,
    )

    assert result.stdout.strip() == f"hello; touch {marker}"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_process_runner_rejects_non_allowlisted_executable(tmp_path: Path) -> None:
    runner = ProcessRunner(allowed_executables={"git"})

    with pytest.raises(ToolExecutionError, match="not allowlisted"):
        await runner.run([sys.executable, "-c", "print('no')"], cwd=tmp_path)


@pytest.mark.asyncio
async def test_process_runner_does_not_inherit_api_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    runner = ProcessRunner(allowed_executables={Path(sys.executable).name})

    result = await runner.run(
        [
            sys.executable,
            "-c",
            "import os; print(os.getenv('OPENAI_API_KEY', 'missing'))",
        ],
        cwd=tmp_path,
    )

    assert result.stdout.strip() == "missing"
