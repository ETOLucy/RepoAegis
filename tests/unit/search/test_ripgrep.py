from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import SearchQuery
from repo_maintenance_agent.search.adapters.ripgrep import (
    RipgrepSearch,
    default_lexical_search,
)

_HAVE_RG = shutil.which("rg") is not None
pytestmark = pytest.mark.skipif(not _HAVE_RG, reason="ripgrep binary not installed")
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
def _query(text: str, *, top_k: int = 5, allowed_paths: tuple[str, ...] = ()) -> SearchQuery:
    return SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text=text,
        top_k=top_k,
        allowed_paths=allowed_paths,
    )
@pytest.mark.asyncio
async def test_ripgrep_finds_exact_substring(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    search = RipgrepSearch(repo)
    hits = await search.search(_query("load_config"))
    assert hits
    assert hits[0].path == "src/config.py"
    assert hits[0].line_start == 1
@pytest.mark.asyncio
async def test_ripgrep_respects_allowed_paths(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    search = RipgrepSearch(repo)
    hits = await search.search(_query("load_config", allowed_paths=("src/service.py",)))
    assert hits == []
@pytest.mark.asyncio
async def test_ripgrep_no_match_returns_empty(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    search = RipgrepSearch(repo)
    hits = await search.search(_query("no_such_symbol_anywhere"))
    assert hits == []
def test_default_lexical_search_factory_returns_search_port(tmp_path: Path) -> None:
    search = default_lexical_search(tmp_path)
    assert hasattr(search, "search")  # SearchPort protocol: async search method