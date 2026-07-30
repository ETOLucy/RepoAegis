from __future__ import annotations

import asyncio
from collections.abc import Mapping

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery
from repo_maintenance_agent.domain.ports import SearchPort
from repo_maintenance_agent.search.fusion import reciprocal_rank_fusion
from repo_maintenance_agent.search.router import QueryKind, SearchRouter


class HybridSearchService:
    def __init__(
        self,
        retrievers: Mapping[QueryKind, SearchPort],
        *,
        router: SearchRouter | None = None,
    ) -> None:
        self._retrievers = dict(retrievers)
        self._router = router or SearchRouter()

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        routes = self._router.route(query.text)
        selected = [self._retrievers[kind] for kind in routes if kind in self._retrievers]
        if not selected:
            return []
        result_sets = await asyncio.gather(*(retriever.search(query) for retriever in selected))
        return reciprocal_rank_fusion(result_sets, limit=query.top_k)

