from pathlib import Path

from repo_maintenance_agent.sandbox.profiles import EnvironmentProfiler, Language


def test_profiler_prefers_lockfile_and_declared_python_commands(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n", encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    profile = EnvironmentProfiler().inspect(tmp_path)

    assert profile.language is Language.PYTHON
    assert profile.dependency_fingerprint_files == ("pyproject.toml", "uv.lock")
    assert profile.test_commands[0] == ("python", "-m", "pytest")


def test_profiler_detects_typescript_repository(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    profile = EnvironmentProfiler().inspect(tmp_path)

    assert profile.language is Language.TYPESCRIPT
    assert ("npm", "test", "--", "--run") in profile.test_commands

