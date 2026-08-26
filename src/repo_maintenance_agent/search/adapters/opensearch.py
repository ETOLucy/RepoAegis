from __future__ import annotations

import asyncio
from typing import Any, Protocol

from repo_maintenance_agent.domain.models import SearchHit, SearchQuery


class OpenSearchClient(Protocol):
    def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]: ...


class OpenSearchClientImpl:
    """Real OpenSearch client backed by opensearch-py."""
    def __init__(self, hosts, *, port=9200, http_auth=None, use_ssl=False, verify_certs=False, **kwargs):
        from opensearchpy import OpenSearch
        self._client = OpenSearch(
            hosts=[{"host": h, "port": port} for h in hosts],
            http_auth=http_auth, use_ssl=use_ssl, verify_certs=verify_certs, **kwargs)
    def search(self, *, index, body):
        return self._client.search(index=index, body=body)
    def ping(self):
        try:
            return self._client.ping()
        except Exception:
            return False


class OpenSearchHybridAdapter:
    def __init__(self, client: OpenSearchClient, index_alias: str) -> None:
        self._client = client
        self._index_alias = index_alias

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        body = self.build_query(query)
        response = await asyncio.to_thread(
            self._client.search,
            index=self._index_alias,
            body=body,
        )
        return [self._to_hit(raw) for raw in response.get("hits", {}).get("hits", [])]

    @staticmethod
    def build_query(query: SearchQuery) -> dict[str, Any]:
        filters: list[dict[str, Any]] = [
            {"term": {"tenant_id": query.tenant_id}},
            {"term": {"repo_id": query.repo_id}},
            {"term": {"commit_sha": query.commit_sha}},
        ]
        if query.allowed_paths:
            filters.append(
                {
                    "bool": {
                        "should": [
                            clause
                            for path in query.allowed_paths
                            for clause in (
                                {"term": {"path": path.rstrip("/")}},
                                {"prefix": {"path": path.rstrip("/") + "/"}},
                            )
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        return {
            "size": query.top_k,
            "_source": [
                "chunk_id",
                "path",
                "content",
                "symbol",
                "line_start",
                "line_end",
            ],
            "query": {
                "bool": {
                    "filter": filters,
                    "must": [{"match": {"content": {"query": query.text}}}],
                }
            },
        }

    @staticmethod
    def _to_hit(raw: dict[str, Any]) -> SearchHit:
        source = raw["_source"]
        return SearchHit(
            hit_id=source.get("chunk_id", raw["_id"]),
            path=source["path"],
            content=source["content"],
            score=float(raw.get("_score") or 0.0),
            source="opensearch",
            symbol=source.get("symbol"),
            line_start=source.get("line_start"),
            line_end=source.get("line_end"),
        )
