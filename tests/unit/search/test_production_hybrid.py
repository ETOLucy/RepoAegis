from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import SearchQuery
from repo_maintenance_agent.search.index import EmbeddingBatch
from repo_maintenance_agent.search.production import WorkspaceIndex


def _fake_vector(text: str, dim: int = 8) -> tuple[float, ...]:
    digest = hashlib.sha256(text.encode()).digest()
    values = [float(byte / 255.0) for byte in digest[:dim]]
    return tuple(values)
class FakeEmbeddingPort:
    def __init__(self) -> None:
        self.calls = 0
    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        self.calls += 1
        return EmbeddingBatch(
            vectors=tuple(_fake_vector(text) for text in texts),
            input_tokens=sum(len(text.split()) for text in texts),
        )
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
async def test_hybrid_index_runs_with_fake_embeddings(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    embeddings = FakeEmbeddingPort()
    index = WorkspaceIndex(repo, embeddings=embeddings)
    hits = await index.search(_query("load_config", top_k=5))
    assert hits, "hybrid index should return hits"
    assert embeddings.calls >= 1, "embedding provider should have been called"
@pytest.mark.asyncio
async def test_hybrid_index_falls_back_without_embeddings(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo)  # no embeddings -> S1 BM25+Symbol only
    hits = await index.search(_query("load_config", top_k=5))
    assert hits
@pytest.mark.asyncio
async def test_hybrid_index_deduplicates_locations(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo, embeddings=FakeEmbeddingPort())
    hits = await index.search(_query("RepoService search", top_k=10))
    locations = [(hit.path, hit.line_start) for hit in hits]
    assert len(locations) == len(set(locations)), "hybrid hits must be deduplicated by location"