from __future__ import annotations

import subprocess
from pathlib import Path

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.target_pack import build_target_pack


def _make_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True, capture_output=True
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"x\"\nversion = \"0.1.0\"\n", encoding="utf-8"
    )
    src = tmp_path / "src"
    (src / "app.py").parent.mkdir(parents=True, exist_ok=True)
    (src / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    policy = src / "repo_maintenance_agent" / "policies"
    policy.mkdir(parents=True, exist_ok=True)
    (policy / "permissions.py").write_text(
        "class PermissionPolicy:\n    pass\n", encoding="utf-8"
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"], check=True, capture_output=True
    )
    return tmp_path


def test_target_pack_builds_canonical_manifest(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    pack = build_target_pack(Settings(), repo_root=repo)

    assert pack.manifest["schema_version"] == "repoaegis-target-pack/v2"
    assert pack.manifest["target_id"] == "repoaegis-v2"
    assert pack.manifest["digest"] == pack.digest
    assert len(pack.digest) == 64
    assert "commit_sha" in pack.manifest["runtime"]
    assert "source_digest" in pack.manifest["runtime"]


def test_target_pack_digest_is_stable(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    first = build_target_pack(Settings(), repo_root=repo)
    second = build_target_pack(Settings(), repo_root=repo)

    assert first.digest == second.digest


def test_target_pack_digest_changes_with_source(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    before = build_target_pack(Settings(), repo_root=repo)
    (repo / "src" / "app.py").write_text(
        "def f():\n    return 2\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "change"], check=True, capture_output=True
    )
    after = build_target_pack(Settings(), repo_root=repo)

    assert before.digest != after.digest


def test_target_pack_excludes_credentials(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    settings = Settings(OPENAI_API_KEY="sk-test")

    pack = build_target_pack(settings, repo_root=repo)

    serialized = str(pack.manifest)
    assert "sk-test" not in serialized
    assert "OPENAI_API_KEY" not in serialized