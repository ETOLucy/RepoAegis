import subprocess
from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import SearchQuery
from repo_maintenance_agent.search.history import GitHistorySearch
from repo_maintenance_agent.tools.process import ProcessRunner


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_history_search_uses_pinned_commit_and_path_scope(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "auth.py").write_text("TOKEN = None\n", encoding="utf-8")
    git(tmp_path, "add", "auth.py")
    git(tmp_path, "commit", "-m", "introduce bearer token parser")
    pinned = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "unrelated.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(tmp_path, "add", "unrelated.py")
    git(tmp_path, "commit", "-m", "add arithmetic helper")

    search = GitHistorySearch(
        tmp_path, ProcessRunner(allowed_executables={"git"})
    )
    query = SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha=pinned,
        text="why introduced bearer token",
        allowed_paths=("auth.py",),
        top_k=5,
    )

    hits = await search.search(query)

    assert len(hits) == 1
    assert hits[0].hit_id == pinned
    assert hits[0].path == "auth.py"
    assert hits[0].source == "history"
    assert "bearer token" in hits[0].content
