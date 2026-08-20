"""Production-path regression test: HISTORY routing must reach GitHistorySearch."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import SearchQuery
from repo_maintenance_agent.search.history import GitHistorySearch
from repo_maintenance_agent.search.production import WorkspaceIndex
from repo_maintenance_agent.tools.process import ProcessRunner


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_workspace_index_routes_history_query_to_git_history(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    (tmp_path / "auth.py").write_text("TOKEN = None\n", encoding="utf-8")
    _git(tmp_path, "add", "auth.py")
    _git(tmp_path, "commit", "-m", "introduce bearer token parser")
    pinned = _git(tmp_path, "rev-parse", "HEAD")
    index = WorkspaceIndex(
        tmp_path,
        history=GitHistorySearch(tmp_path, ProcessRunner(allowed_executables={"git"})),
    )
    query = SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha=pinned,
        text="why introduced bearer token",
        allowed_paths=("auth.py",),
        top_k=5,
    )
    hits = await index.search(query)
    assert hits
    assert any(hit.source == "history" for hit in hits)
    history_hit = next(hit for hit in hits if hit.source == "history")
    assert history_hit.path == "auth.py"
    assert history_hit.hit_id == pinned
