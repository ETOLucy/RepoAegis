import subprocess
from pathlib import Path

from repo_maintenance_agent.security.scanner import (
    repository_files,
    scan_history,
    scan_paths,
)


def test_scanner_detects_secret_and_personal_absolute_path(tmp_path: Path) -> None:
    source = tmp_path / "bad.txt"
    source.write_text(
        "OPENAI_API_KEY=" + "sk-" + "a" * 32 + "\n"
        "workspace=C:\\Users\\alice\\private-project\n",
        encoding="utf-8",
    )

    findings = scan_paths([source], root=tmp_path)

    assert {finding.rule_id for finding in findings} == {
        "credential.openai",
        "privacy.windows-user-path",
    }
    assert all("a" * 32 not in finding.preview for finding in findings)


def test_scanner_allows_documented_environment_variable_name(tmp_path: Path) -> None:
    source = tmp_path / "safe.txt"
    source.write_text(
        "Read OPENAI_API_KEY from the environment. Example: OPENAI_API_KEY=\n",
        encoding="utf-8",
    )

    assert scan_paths([source], root=tmp_path) == []


def test_repository_files_include_untracked_non_ignored_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / ".env").write_text("ignored\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore", "tracked.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    relative = {path.relative_to(tmp_path).as_posix() for path in repository_files(tmp_path)}

    assert relative == {".gitignore", "new.txt", "tracked.txt"}


def test_history_scan_detects_secret_removed_from_worktree(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    secret = "sk-" + "a" * 32
    source = tmp_path / "removed.txt"
    source.write_text(secret + "\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "removed.txt"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "contains removed fixture"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    source.unlink()

    findings = scan_history(tmp_path)

    assert {finding.rule_id for finding in findings} == {"credential.openai"}
    assert all(secret not in finding.preview for finding in findings)
