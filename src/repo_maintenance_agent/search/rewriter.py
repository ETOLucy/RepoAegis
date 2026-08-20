from __future__ import annotations

import re
from dataclasses import dataclass, field

_QUOTE = re.compile(r"[\"'`]([^\"'`]{3,})[\"'`]")
_PATH_HINT = re.compile(
    r"\b([\w./-]+\.(?:py|ts|js|tsx|jsx|go|rs|java|md|toml|yml|yaml|json))\b",
    re.IGNORECASE,
)
_SYMBOL_HINT = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b|(?<=\.)\b([a-z_][A-Za-z0-9_]*)\b")


@dataclass(frozen=True, slots=True)
class RewrittenQuery:
    text: str
    kind: str = "general"
    key_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryRewritePlan:
    queries: tuple[RewrittenQuery, ...]
    raw: str = field(default="", compare=False)


def rewrite_queries(issue_text: str, *, max_queries: int = 4) -> QueryRewritePlan:
    """Rule-based issue -> search queries splitter.
    Mirrors the CGM 'Rewriter' idea (issue -> multiple search queries) without
    any extra dependency or LLM call: exact quoted strings, file-path hints and
    CamelCase symbols are extracted into targeted queries; the full text is
    always kept as a fallback query. The LLM-based rewriter (S2b) can produce
    richer queries, but this function guarantees a working zero-cost baseline.
    """
    if not issue_text or not issue_text.strip():
        return QueryRewritePlan(queries=(RewrittenQuery(text=""),))
    queries: list[RewrittenQuery] = []
    for match in _QUOTE.finditer(issue_text):
        quoted = match.group(1).strip()
        if len(quoted) >= 3:
            queries.append(RewrittenQuery(text=quoted, kind="exact"))
    for match in _PATH_HINT.finditer(issue_text):
        path = match.group(1).strip()
        queries.append(
            RewrittenQuery(
                text=path,
                kind="path",
                key_paths=(path,),
            )
        )
    for match in _SYMBOL_HINT.finditer(issue_text):
        symbol = (match.group(1) or match.group(2) or "").strip()
        if len(symbol) >= 3:
            queries.append(RewrittenQuery(text=symbol, kind="symbol"))
    queries.append(RewrittenQuery(text=issue_text.strip(), kind="general"))
    seen: set[str] = set()
    unique: list[RewrittenQuery] = []
    for item in queries:
        key = (item.text.casefold(), item.kind)
        if key in seen:
            continue
        seen.add(key)  # type: ignore
        unique.append(item)
    return QueryRewritePlan(queries=tuple(unique[:max_queries]))
