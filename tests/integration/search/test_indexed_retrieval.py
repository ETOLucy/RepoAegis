from pathlib import Path

import pytest

from repo_maintenance_agent.domain.models import SearchQuery
from repo_maintenance_agent.search.index import BM25Search, SymbolSearch, ingest_workspace


def query(text: str) -> SearchQuery:
    return SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text=text,
        top_k=5,
    )


@pytest.mark.asyncio
async def test_bm25_ingestion_ranks_relevant_chunk_and_preserves_scope(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(
        "def validate_token(token):\n    # authenticate bearer token\n    return bool(token)\n",
        encoding="utf-8",
    )
    (tmp_path / "math.py").write_text(
        "def add(left, right):\n    return left + right\n",
        encoding="utf-8",
    )
    chunks = ingest_workspace(
        tmp_path, tenant_id="tenant-a", repo_id="owner/repo", commit_sha="a" * 40
    )

    hits = await BM25Search(chunks).search(query("authenticate bearer token"))

    assert hits[0].path == "auth.py"
    assert hits[0].source == "bm25"
    assert hits[0].line_start == 1
    assert hits[0].hit_id.startswith("a" * 12)


@pytest.mark.asyncio
async def test_symbol_index_returns_definition_and_honors_allowed_paths(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(
        "class TokenValidator:\n    def validate_token(self, token):\n        return bool(token)\n",
        encoding="utf-8",
    )
    (tmp_path / "other.py").write_text(
        "def validate_token(token):\n    return token is not None\n",
        encoding="utf-8",
    )
    chunks = ingest_workspace(
        tmp_path, tenant_id="tenant-a", repo_id="owner/repo", commit_sha="a" * 40
    )
    scoped = query("definition validate_token").model_copy(update={"allowed_paths": ("auth.py",)})

    hits = await SymbolSearch(chunks).search(scoped)

    assert hits
    assert {hit.path for hit in hits} == {"auth.py"}
    assert hits[0].symbol == "TokenValidator.validate_token"
    assert hits[0].source == "symbol"
