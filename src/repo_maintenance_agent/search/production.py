from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery
from repo_maintenance_agent.domain.ports import SearchPort
from repo_maintenance_agent.search.adapters.graph import GraphSearch
from repo_maintenance_agent.search.adapters.local import LocalLexicalSearch
from repo_maintenance_agent.search.codegraph import RepoGraph, build_repo_graph
from repo_maintenance_agent.search.index import BM25Search, CodeChunk, SymbolSearch, ingest_workspace
from repo_maintenance_agent.search.router import QueryKind
from repo_maintenance_agent.search.service import HybridSearchService

_MAX_CACHED_COMMITS = 3


@dataclass(frozen=True, slots=True)
class _IndexBundle:
    chunks: tuple[CodeChunk, ...]
    bm25: BM25Search
    symbol: SymbolSearch
    graph: GraphSearch


class WorkspaceIndex:
    """Production-ready code index over a workspace checkout.
    Default channels: BM25 + Symbol (search/index.py) + Graph (the
    dependency-aware call/import graph, search/codegraph.py) — the precise
    channel for "where is X defined"/"who uses X" — fused with reciprocal
    rank fusion. Pass ``lexical`` (e.g. RipgrepSearch) to add a fast exact
    substring channel for error-string / quoted-identifier queries.
    The index is built lazily on first search and cached per commit SHA (the
    chunk identity and scoping depend on the commit), with a small LRU so a
    long-lived worker serving multiple tasks does not grow without bound.
    When ``tenant_id`` / ``repo_id`` are provided the query scope is validated
    against them; the production factory does not know the tenant/repo up
    front, so it constructs the index without them and scoping is enforced by
    the per-query fields inside ``ingest_workspace``.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        tenant_id: str | None = None,
        repo_id: str | None = None,
        lexical: SearchPort | None = None,
        history: SearchPort | None = None,
        graph: RepoGraph | None = None,
    ) -> None:
        self._workspace = workspace.resolve()
        self._tenant_id = tenant_id
        self._repo_id = repo_id
        self._lexical = lexical or LocalLexicalSearch(workspace)
        self._history = history
        # Pass a pre-built graph when the caller already built one (e.g. the
        # Localizer's goto_definition/find_references tool needs its own copy
        # anyway) so the workspace isn't parsed twice.
        self._graph = graph
        self._bundles: OrderedDict[str, _IndexBundle] = OrderedDict()
        self._lock = asyncio.Lock()

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        bundle = await self._bundle_for(query)
        retrievers: dict[QueryKind, SearchPort] = {
            QueryKind.BM25: bundle.bm25,
            QueryKind.SYMBOL: bundle.symbol,
            QueryKind.GRAPH: bundle.graph,
        }
        if self._lexical is not None:
            retrievers[QueryKind.LEXICAL] = self._lexical
        if self._history is not None:
            retrievers[QueryKind.HISTORY] = self._history
        service = HybridSearchService(retrievers)
        fused = await service.search(query, kind=query.kind)
        return _dedupe_by_location(fused, limit=query.top_k)

    async def _bundle_for(self, query: SearchQuery) -> _IndexBundle:
        if self._tenant_id is not None and query.tenant_id != self._tenant_id:
            raise ValueError("search scope does not match the indexed workspace")
        if self._repo_id is not None and query.repo_id != self._repo_id:
            raise ValueError("search scope does not match the indexed workspace")
        async with self._lock:
            cached = self._bundles.get(query.commit_sha)
            if cached is not None:
                return cached
            chunks = ingest_workspace(
                self._workspace,
                tenant_id=query.tenant_id,
                repo_id=query.repo_id,
                commit_sha=query.commit_sha,
            )
            # The call/import graph only depends on the checked-out files, not
            # on chunking, so it's built straight from the workspace (unless
            # the caller already built one for us).
            graph = self._graph if self._graph is not None else build_repo_graph(self._workspace)
            bundle = _IndexBundle(
                chunks=chunks,
                bm25=BM25Search(chunks),
                symbol=SymbolSearch(chunks),
                graph=GraphSearch(self._workspace, graph),
            )
            self._bundles[query.commit_sha] = bundle
            self._bundles.move_to_end(query.commit_sha)
            while len(self._bundles) > _MAX_CACHED_COMMITS:
                self._bundles.popitem(last=False)
            return bundle


def _dedupe_by_location(hits: list[SearchHit], *, limit: int) -> list[SearchHit]:
    """Collapse hits that point at the same (path, line_start) location.
    Symbol chunks and line chunks are separate index entries with different
    chunk_ids, so reciprocal rank fusion alone does not collapse them. For
    research evidence the location is what matters, so keep the highest-ranked
    hit per location and merge its source labels.
    """
    seen: dict[tuple[str, int | None], SearchHit] = {}
    for hit in hits:
        key = (hit.path, hit.line_start)
        previous = seen.get(key)
        if previous is None:
            seen[key] = hit
            continue
        merged_source = "+".join(
            sorted({source for source in [previous.source, hit.source] if source})
        )
        seen[key] = previous.model_copy(update={"source": merged_source})
    return list(seen.values())[:limit]
