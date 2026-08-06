from pathlib import Path

import pytest

from repo_maintenance_agent.domain.errors import ToolExecutionError
from repo_maintenance_agent.tools.patch import GitPatchApplier
from repo_maintenance_agent.tools.process import ProcessRunner


async def git(runner: ProcessRunner, workspace: Path, *args: str) -> None:
    await runner.run(["git", *args], cwd=workspace)


@pytest.mark.asyncio
async def test_patch_applier_preflights_and_applies_only_declared_files(
    tmp_path: Path,
) -> None:
    runner = ProcessRunner(allowed_executables={"git"})
    await git(runner, tmp_path, "init")
    await git(runner, tmp_path, "config", "user.email", "test@example.invalid")
    await git(runner, tmp_path, "config", "user.name", "Test")
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    await git(runner, tmp_path, "add", "app.py")
    await git(runner, tmp_path, "commit", "-m", "base")
    patch = (
        b"diff --git a/app.py b/app.py\n"
        b"--- a/app.py\n"
        b"+++ b/app.py\n"
        b"@@ -1 +1 @@\n"
        b"-value = 1\n"
        b"+value = 2\n"
    )

    changed = await GitPatchApplier(runner).apply(
        workspace=tmp_path,
        patch=patch,
        declared_files=("app.py",),
    )

    assert changed == ("app.py",)
    assert target.read_text(encoding="utf-8") == "value = 2\n"


@pytest.mark.asyncio
async def test_patch_applier_rejects_files_outside_model_declaration(tmp_path: Path) -> None:
    runner = ProcessRunner(allowed_executables={"git"})
    await git(runner, tmp_path, "init")
    patch = (
        b"diff --git a/secret.txt b/secret.txt\n"
        b"new file mode 100644\n"
        b"--- /dev/null\n"
        b"+++ b/secret.txt\n"
        b"@@ -0,0 +1 @@\n"
        b"+unexpected\n"
    )

    with pytest.raises(ToolExecutionError, match="undeclared"):
        await GitPatchApplier(runner).apply(
            workspace=tmp_path,
            patch=patch,
            declared_files=("app.py",),
        )

    assert not (tmp_path / "secret.txt").exists()



@pytest.mark.asyncio
async def test_patch_applier_tolerates_missing_trailing_newline(tmp_path: Path) -> None:
    runner = ProcessRunner(allowed_executables={"git"})
    await git(runner, tmp_path, "init")
    await git(runner, tmp_path, "config", "user.email", "test@example.invalid")
    await git(runner, tmp_path, "config", "user.name", "Test")
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    await git(runner, tmp_path, "add", "app.py")
    await git(runner, tmp_path, "commit", "-m", "base")
    patch = (
        b"diff --git a/app.py b/app.py\n"
        b"--- a/app.py\n"
        b"+++ b/app.py\n"
        b"@@ -1 +1 @@\n"
        b"-value = 1\n"
        b"+value = 2"
    )

    changed = await GitPatchApplier(runner).apply(
        workspace=tmp_path, patch=patch, declared_files=("app.py",)
    )

    assert changed == ("app.py",)
    assert target.read_text(encoding="utf-8") == "value = 2\n"


@pytest.mark.asyncio
async def test_patch_applier_recounts_incorrect_model_hunk_lengths(tmp_path: Path) -> None:
    runner = ProcessRunner(allowed_executables={"git"})
    await git(runner, tmp_path, "init")
    target = tmp_path / "app.py"
    target.write_text("first\nvalue = 1\nlast\n", encoding="utf-8")
    patch = (
        b"--- a/app.py\n"
        b"+++ b/app.py\n"
        b"@@ -1,1 +1,1 @@\n"
        b" first\n"
        b"-value = 1\n"
        b"+value = 2\n"
        b" last\n"
    )

    changed = await GitPatchApplier(runner).apply(
        workspace=tmp_path, patch=patch, declared_files=("app.py",)
    )

    assert changed == ("app.py",)
    assert target.read_text(encoding="utf-8") == "first\nvalue = 2\nlast\n"

