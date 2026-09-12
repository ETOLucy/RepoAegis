from __future__ import annotations

from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import SearchQuery
from repo_maintenance_agent.search.production import WorkspaceIndex


def _make_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "config.py").write_text(
        "def load_config():\n    return {'env': 'demo'}\n\n\ndef save_config(data):\n    pass\n",
        encoding="utf-8",
    )
    (src / "service.py").write_text(
        "class RepoService:\n    def search(self, query):\n        return []\n",
        encoding="utf-8",
    )
    return tmp_path


def _query(text: str, *, top_k: int = 5) -> SearchQuery:
    return SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text=text,
        top_k=top_k,
    )


@pytest.mark.asyncio
async def test_hybrid_index_runs_with_default_channels(tmp_path: Path) -> None:
    """BM25 + Symbol + Graph — no embeddings/OpenSearch credentials required."""
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo)
    hits = await index.search(_query("load_config", top_k=5))
    assert hits


@pytest.mark.asyncio
async def test_hybrid_index_deduplicates_locations(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo)
    hits = await index.search(_query("RepoService search", top_k=10))
    locations = [(hit.path, hit.line_start) for hit in hits]
    assert len(locations) == len(set(locations)), "hybrid hits must be deduplicated by location"
