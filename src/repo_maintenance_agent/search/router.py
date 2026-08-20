from __future__ import annotations

import re
from enum import StrEnum


class QueryKind(StrEnum):
    LEXICAL = "lexical"
    BM25 = "bm25"
    VECTOR = "vector"
    SYMBOL = "symbol"
    HISTORY = "history"


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
    def route(self, text: str) -> frozenset[QueryKind]:
        if _SYMBOL.search(text):
            return frozenset({QueryKind.SYMBOL, QueryKind.BM25})
        if _HISTORY.search(text):
            return frozenset({QueryKind.HISTORY, QueryKind.BM25})
        if _EXACT.search(text):
            return frozenset({QueryKind.LEXICAL, QueryKind.BM25})
        return frozenset({QueryKind.BM25, QueryKind.VECTOR})
