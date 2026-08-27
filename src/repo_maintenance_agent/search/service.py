from __future__ import annotations

import asyncio
from collections.abc import Mapping

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery
from repo_maintenance_agent.domain.ports import SearchPort
from repo_maintenance_agent.search.fusion import reciprocal_rank_fusion
from repo_maintenance_agent.search.kind_mapping import (
    get_primary_kinds,
    get_secondary_kinds,
)
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

    async def search(
        self,
        query: SearchQuery,
        *,
        kind: str | None = None,
    ) -> list[SearchHit]:
        primary_kinds = self._resolve_primary_kinds(query.text, kind=kind)
        secondary_kinds = self._resolve_secondary_kinds(kind)

        primary_task = self._search_multi(query, primary_kinds)
        secondary_task = self._search_multi(query, secondary_kinds)

        primary_results, secondary_results = await asyncio.gather(
            primary_task, secondary_task,
)

        all_sets = []
        if primary_results:
            all_sets.append(primary_results)
        if secondary_results:
            all_sets.append(secondary_results)

        if not all_sets:
            return []

        fused = reciprocal_rank_fusion(all_sets, limit=query.top_k)

        # Only retry with GENERAL when results are empty, to avoid overwriting valid results
        retry_count = 0
        max_retries = 2
        current_kind = kind

        while len(fused) == 0 and retry_count < max_retries and current_kind != "general":
            retry_count += 1
            current_kind = "general"
            primary_kinds = self._resolve_primary_kinds(query.text, kind=current_kind)
            secondary_kinds = self._resolve_secondary_kinds(current_kind)
            primary_task = self._search_multi(query, primary_kinds)
            secondary_task = self._search_multi(query, secondary_kinds)
            primary_results, secondary_results = await asyncio.gather(
                primary_task, secondary_task,
)
            all_sets = []
            if primary_results:
                all_sets.append(primary_results)
            if secondary_results:
                all_sets.append(secondary_results)
            if all_sets:
                fused = reciprocal_rank_fusion(all_sets, limit=query.top_k)

        return fused

    def _resolve_primary_kinds(self, text: str, *, kind: str | None) -> frozenset[QueryKind]:
        if kind is not None:
            return get_primary_kinds(kind)
        return self._router.route(text)

    def _resolve_secondary_kinds(self, kind: str | None) -> frozenset[QueryKind]:
        if kind is not None:
            return get_secondary_kinds(kind)
        return frozenset({QueryKind.BM25})

    async def _search_multi(
        self,
        query: SearchQuery,
        kinds: frozenset[QueryKind],
    ) -> list[SearchHit]:
        selected = [self._retrievers[k] for k in kinds if k in self._retrievers]
        if not selected:
            return []
        result_sets = await asyncio.gather(
            *(retriever.search(query) for retriever in selected),
)
        if len(result_sets) == 1:
            return result_sets[0]
        return reciprocal_rank_fusion(result_sets, limit=query.top_k)
