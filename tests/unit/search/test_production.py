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
    (tmp_path / "README.md").write_text("# Demo\nloads configuration defaults\n", encoding="utf-8")
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
async def test_workspace_index_returns_symbol_and_bm25_hits(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo)
    hits = await index.search(_query("load_config", top_k=5))
    assert hits, "expected at least one hit"
    assert hits[0].path == "src/config.py"
    assert hits[0].source in {"symbol", "bm25", "bm25+symbol", "symbol+bm25"}
    assert hits[0].line_start is not None
@pytest.mark.asyncio
async def test_workspace_index_merges_symbol_and_bm25_without_duplicates(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo)
    hits = await index.search(_query("RepoService search", top_k=5))
    paths = [(hit.path, hit.line_start) for hit in hits]
    assert len(paths) == len(set(paths)), "duplicate hit locations must be merged"
@pytest.mark.asyncio
async def test_workspace_index_respects_allowed_paths(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo)
    query = _query("load_config")
    query = query.model_copy(update={"allowed_paths": ("src/config.py",)})
    hits = await index.search(query)
    assert hits and all(hit.path == "src/config.py" for hit in hits)
@pytest.mark.asyncio
async def test_workspace_index_caches_index_per_commit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo)
    first = await index.search(_query("load_config"))
    second = await index.search(_query("load_config"))
    assert first == second
    assert len(index._bundles) == 1  # noqa: SLF001 - test asserts internal cache size
@pytest.mark.asyncio
async def test_workspace_index_uses_lexical_channel_when_provided(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    index = WorkspaceIndex(repo, lexical=None)
    hits = await index.search(_query("configuration defaults", top_k=3))
    assert hits