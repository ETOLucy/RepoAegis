import pytest

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery
from repo_maintenance_agent.search.adapters.opensearch import OpenSearchHybridAdapter
from repo_maintenance_agent.search.router import QueryKind
from repo_maintenance_agent.search.service import HybridSearchService


class FixedRetriever:
    def __init__(self, source: str, ids: tuple[str, ...]) -> None:
        self._source = source
        self._ids = ids

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        return [
            SearchHit(
                hit_id=hit_id,
                path=f"src/{hit_id}.py",
                content=query.text,
                score=1.0,
                source=self._source,
            )
            for hit_id in self._ids
        ]


@pytest.mark.asyncio
async def test_hybrid_service_fuses_selected_retrievers_and_collapses_duplicates() -> None:
    service = HybridSearchService(
        {
            QueryKind.BM25: FixedRetriever("bm25", ("shared", "lexical")),
            QueryKind.VECTOR: FixedRetriever("vector", ("semantic", "shared")),
        }
    )
    query = SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text="where is the configuration default loaded",
        top_k=3,
    )

    hits = await service.search(query)

    assert hits[0].hit_id == "shared"
    assert hits[0].source == "bm25+bm25+vector"
    assert len(hits) == 3


def test_opensearch_query_enforces_scope_and_supports_exact_allowed_file() -> None:
    query = SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text="ConfigLoader",
        allowed_paths=("src/app.py",),
    )

    body = OpenSearchHybridAdapter.build_query(query)
    filters = body["query"]["bool"]["filter"]
    path_filter = filters[-1]["bool"]["should"]

    assert {"term": {"tenant_id": "tenant-a"}} in filters
    assert {"term": {"repo_id": "owner/repo"}} in filters
    assert {"term": {"commit_sha": "a" * 40}} in filters
    assert {"term": {"path": "src/app.py"}} in path_filter
    assert {"prefix": {"path": "src/app.py/"}} in path_filter
