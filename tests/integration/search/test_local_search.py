from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import SearchQuery
from repo_maintenance_agent.search.adapters.local import LocalLexicalSearch


@pytest.mark.asyncio
async def test_local_search_returns_scoped_source_matches(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text(
        "def load_config():\n    return 'default value'\n",
        encoding="utf-8",
    )
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "secret.py").write_text("default value", encoding="utf-8")
    search = LocalLexicalSearch(tmp_path)
    query = SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text="default value",
        allowed_paths=("src",),
        top_k=5,
    )

    results = await search.search(query)

    assert [result.path for result in results] == ["src/config.py"]
    assert results[0].line_start == 2


@pytest.mark.asyncio
async def test_local_search_rejects_allowed_path_outside_workspace(tmp_path: Path) -> None:
    search = LocalLexicalSearch(tmp_path)
    query = SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text="anything",
        allowed_paths=("..",),
    )

    with pytest.raises(ValueError, match="outside"):
        await search.search(query)

