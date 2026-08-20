from __future__ import annotations

import pytest

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery
from repo_maintenance_agent.search.reranker import LLMReranker, _RankedIds


class FakeModel:
    def __init__(self, order: list[str] | None = None, *, fail: bool = False) -> None:
        self.order = order
        self.fail = fail
        self.calls = 0

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model unavailable")
        assert schema is _RankedIds
        return _RankedIds(ranked_ids=self.order or [])


def _hit(hit_id: str) -> SearchHit:
    return SearchHit(
        hit_id=hit_id,
        path=f"src/{hit_id}.py",
        content=f"content of {hit_id}",
        score=1.0,
        source="bm25",
    )


def _query(top_k: int = 5) -> SearchQuery:
    return SearchQuery(
        tenant_id="tenant-a",
        repo_id="owner/repo",
        commit_sha="a" * 40,
        text="find load_config",
        top_k=top_k,
    )


@pytest.mark.asyncio
async def test_reranker_reorders_fused_hits() -> None:
    hits = [_hit("a"), _hit("b"), _hit("c")]
    reranker = LLMReranker(model=FakeModel(order=["c", "a", "b"]), final_k=2)
    result = await reranker.rerank(_query(), hits)
    assert [hit.hit_id for hit in result] == ["c", "a"]


@pytest.mark.asyncio
async def test_reranker_appends_missing_ids_and_respects_final_k() -> None:
    hits = [_hit("a"), _hit("b"), _hit("c")]
    reranker = LLMReranker(model=FakeModel(order=["b"]), final_k=3)
    result = await reranker.rerank(_query(), hits)
    assert [hit.hit_id for hit in result] == ["b", "a", "c"]


@pytest.mark.asyncio
async def test_reranker_falls_back_to_fused_order_on_model_failure() -> None:
    hits = [_hit("a"), _hit("b"), _hit("c")]
    reranker = LLMReranker(model=FakeModel(fail=True), final_k=2)
    result = await reranker.rerank(_query(), hits)
    assert [hit.hit_id for hit in result] == ["a", "b"]
    assert reranker._model.calls == 1  # type: ignore[attr-defined]


def test_reranker_validates_pool_and_k() -> None:
    with pytest.raises(ValueError):
        LLMReranker(model=FakeModel(), candidate_pool=0)
    with pytest.raises(ValueError):
        LLMReranker(model=FakeModel(), final_k=21, candidate_pool=20)
