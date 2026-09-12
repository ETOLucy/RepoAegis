import pytest

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery
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
            QueryKind.LEXICAL: FixedRetriever("lexical", ("exact", "shared")),
        }
    )
    query = SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text="where is the configuration default loaded",
        top_k=3,
    )

    # kind="exact" -> primary={LEXICAL,BM25}, secondary={BM25}, exercising both channels
    # (WorkspaceIndex.search() always passes kind=query.kind this way in production).
    hits = await service.search(query, kind="exact")

    assert hits[0].hit_id == "shared"
    assert hits[0].source == "bm25+bm25+lexical"
    assert len(hits) == 3
