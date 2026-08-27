from repo_maintenance_agent.domain.models import SearchHit
from repo_maintenance_agent.search.fusion import reciprocal_rank_fusion
from repo_maintenance_agent.search.router import QueryKind, SearchRouter


def hit(hit_id: str, source: str, score: float = 1.0) -> SearchHit:
    return SearchHit(
        hit_id=hit_id,
        path=f"src/{hit_id}.py",
        content=hit_id,
        score=score,
        source=source,
    )


def test_rrf_promotes_hits_returned_by_multiple_retrievers() -> None:
    fused = reciprocal_rank_fusion(
        [
            [hit("shared", "bm25"), hit("lexical-only", "bm25")],
            [hit("vector-only", "vector"), hit("shared", "vector")],
        ],
        limit=3,
    )

    assert fused[0].hit_id == "shared"
    assert len({item.hit_id for item in fused}) == 3
    assert fused[0].source == "bm25+vector"


def test_query_router_uses_symbol_path_for_reference_question() -> None:
    routes = SearchRouter().route("find callers of ConfigLoader.load")

    assert QueryKind.SYMBOL in routes
    assert QueryKind.VECTOR not in routes


def test_query_router_uses_hybrid_for_natural_language_intent() -> None:
    routes = SearchRouter().route("where does the application load empty configuration defaults")

    assert routes == frozenset({QueryKind.BM25, QueryKind.VECTOR, QueryKind.OPENSEARCH})
