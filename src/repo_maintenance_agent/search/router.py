from __future__ import annotations

import re
from enum import StrEnum


class QueryKind(StrEnum):
    """Search adapter category — the "supply-side" classification.

    Each value corresponds to a concrete SearchPort implementation
    registered in WorkspaceIndex / HybridSearchService.
    """
    LEXICAL = "lexical"       # 精确子串匹配（LocalLexicalSearch）
    BM25 = "bm25"             # BM25 全文检索（BM25Search）
    VECTOR = "vector"         # 向量嵌入检索（VectorSearch）
    SYMBOL = "symbol"         # AST 符号检索（SymbolSearch）
    HISTORY = "history"       # Git 历史检索（未完整实现）
    OPENSEARCH = "opensearch" # OpenSearch 混合检索（OpenSearchHybridAdapter）


_SYMBOL = re.compile(
    r"\b(callers?|callees?|references?|definition|implements?|inherits?|symbol)\b",
    re.IGNORECASE,
)
_HISTORY = re.compile(r"\b(why|history|commit|changed|introduced|blame)\b", re.IGNORECASE)
_EXACT = re.compile(
    r"(Traceback|Exception|Error:|[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*|"
    r"[\"'`][^\"'`]{3,}[\"'`])"
)


class SearchRouter:
    """Routes a search query text to the appropriate QueryKind set.

    When a ``kind`` hint is provided (from the Rewriter), it is used as the
    primary routing signal.  Without a hint, the router falls back to regex
    heuristics on the query text for backward compatibility.
    """

    def route(self, text: str, *, kind: str | None = None) -> frozenset[QueryKind]:
        """Route a search query to QueryKind(s).

        Args:
            text: The raw search query text.
            kind: Optional hint from the Rewriter (e.g. "exact", "symbol").

        Returns:
            A frozenset of QueryKind values to use for this query.
        """
        if kind is not None:
            from repo_maintenance_agent.search.kind_mapping import get_primary_kinds
            return get_primary_kinds(kind)

        # Fallback: regex heuristics (backward compatibility)
        if _SYMBOL.search(text):
            return frozenset({QueryKind.SYMBOL, QueryKind.BM25})
        if _HISTORY.search(text):
            return frozenset({QueryKind.HISTORY, QueryKind.BM25})
        if _EXACT.search(text):
            return frozenset({QueryKind.LEXICAL, QueryKind.BM25})
        return frozenset({QueryKind.BM25, QueryKind.VECTOR, QueryKind.OPENSEARCH})
