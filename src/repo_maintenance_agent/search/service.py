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
    """Multi-strategy hybrid search service.

    For each search query, runs **primary** and **secondary** searches in
    parallel and fuses results via Reciprocal Rank Fusion (RRF).

    Primary search:   Based on the Rewriter's ``kind``, selects the most
                      specific retriever(s) for the query type.
    Secondary search: Always runs BM25 (+ VECTOR if available) as a safety
                      net so the search never returns empty-handed.
    """

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
        """Execute a multi-strategy search.

        Args:
            query: The search query with text, scoping, and top_k.
            kind:  Optional Rewriter kind hint (e.g. "exact", "symbol").

        Returns:
            Ranked search hits fused from primary and secondary results.
        """
        primary_kinds = self._resolve_primary_kinds(query.text, kind=kind)
        secondary_kinds = self._resolve_secondary_kinds(kind)

        # Run primary and secondary searches in parallel
        primary_task = self._search_multi(query, primary_kinds)
        secondary_task = self._search_multi(query, secondary_kinds)

        primary_results, secondary_results = await asyncio.gather(
            primary_task, secondary_task,
)

        # Fuse: primary results are priority, secondary fills gaps
        all_sets = []
        if primary_results:
            all_sets.append(primary_results)
        if secondary_results:
            all_sets.append(secondary_results)

        if not all_sets:
            return []

        fused = reciprocal_rank_fusion(all_sets, limit=query.top_k)

        # 方案 C:搜索后轻量校验结果相关性
        retry_count = 0
        max_retries = 3
        current_kind = kind

        while len(fused) < 3 and retry_count < max_retries and current_kind != "general":
            retry_count += 1
            current_kind = "general"  # 回退到 GENERAL 策略
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
        """Resolve the primary search QueryKind set."""
        if kind is not None:
            return get_primary_kinds(kind)
        # Fall back to the router's regex heuristics
        return self._router.route(text)

    def _resolve_secondary_kinds(self, kind: str | None) -> frozenset[QueryKind]:
        """Resolve the secondary search QueryKind set."""
        if kind is not None:
            return get_secondary_kinds(kind)
        # Default secondary: BM25 only
        return frozenset({QueryKind.BM25})

    async def _search_multi(
        self,
        query: SearchQuery,
        kinds: frozenset[QueryKind],
    ) -> list[SearchHit]:
        """Run multiple retrievers in parallel for the given QueryKind set."""
        selected = [self._retrievers[k] for k in kinds if k in self._retrievers]
        if not selected:
            return []
        result_sets = await asyncio.gather(
            *(retriever.search(query) for retriever in selected),
)
        # If only one retriever, return its results directly
        if len(result_sets) == 1:
            return result_sets[0]
        # Fuse multiple retrievers within the same strategy
        return reciprocal_rank_fusion(result_sets, limit=query.top_k)
