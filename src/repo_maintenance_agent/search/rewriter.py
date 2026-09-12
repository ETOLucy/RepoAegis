from __future__ import annotations

import re
from dataclasses import dataclass, field

from repo_maintenance_agent.search.kind_mapping import SearchKind

_QUOTE = re.compile(r"[\"'`]([^\"'`]{3,})[\"'`]")
_PATH_HINT = re.compile(
    r"\b([\w./-]+\.(?:py|ts|js|tsx|jsx|go|rs|java|md|toml|yml|yaml|json))\b",
    re.IGNORECASE,
)
_SYMBOL_HINT = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b|(?<=\.)\b([a-z_][A-Za-z0-9_]*)\b")
_ERROR_PATTERN = re.compile(
    r"\b(Traceback|Error:|Exception|KeyError|ValueError|TypeError|AttributeError|"
    r"ImportError|IndexError|RuntimeError|OSError|FileNotFoundError)\b",
    re.IGNORECASE,
)
_HISTORY_PATTERN = re.compile(
    r"\b(why|history|commit|changed|introduced|blame|who|when|reason)\b",
    re.IGNORECASE,
)
_DEPENDENCY_PATTERN = re.compile(
    r"\b(depend|requirements?|package|module|install|pip|npm|cargo|gem|import)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RewrittenQuery:
    text: str
    kind: str = SearchKind.GENERAL.value
    key_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryRewritePlan:
    queries: tuple[RewrittenQuery, ...]
    raw: str = field(default="", compare=False)


def _detect_kind(text: str) -> str:
    """Detect the most specific SearchKind for a query text.

    Priority order (most specific first):
    1. exact   — error messages/tracebacks, exact identifiers, quoted strings, dotted paths
    2. history — git history, blame, why/who questions
    3. symbol  — CamelCase symbols, class/function names
    4. dependency — dependency/import-related queries
    5. general — fallback
    """
    if _ERROR_PATTERN.search(text):
        return SearchKind.EXACT.value
    if _QUOTE.search(text) or re.search(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", text):
        return SearchKind.EXACT.value
    if _HISTORY_PATTERN.search(text):
        return SearchKind.HISTORY.value
    if _SYMBOL_HINT.search(text):
        return SearchKind.SYMBOL.value
    if _PATH_HINT.search(text):
        return SearchKind.EXACT.value
    if _DEPENDENCY_PATTERN.search(text):
        return SearchKind.DEPENDENCY.value
    return SearchKind.GENERAL.value


def rewrite_queries(issue_text: str, *, max_queries: int = 4) -> QueryRewritePlan:
    """Rule-based issue -> search queries splitter.

    Mirrors the CGM 'Rewriter' idea (issue -> multiple search queries) without
    any extra dependency or LLM call: exact quoted strings, file-path hints,
    error messages, CamelCase symbols, and dependency hints are extracted into
    targeted queries with kind detection; the full text is always kept as a
    General fallback query. The LLM-based rewriter can produce richer queries,
    but this function guarantees a working zero-cost baseline.

    Each query carries a ``kind`` label that the SearchRouter uses to select
    the appropriate search strategy (primary search adapters).
    """
    if not issue_text or not issue_text.strip():
        return QueryRewritePlan(queries=(RewrittenQuery(text=""),))

    queries: list[RewrittenQuery] = []

    # 1. Error messages & tracebacks
    error_match = _ERROR_PATTERN.search(issue_text)
    if error_match:
        start = max(0, error_match.start() - 20)
        end = min(len(issue_text), error_match.end() + 80)
        error_text = issue_text[start:end].strip()
        if error_text:
            queries.append(RewrittenQuery(text=error_text, kind=SearchKind.EXACT.value))

    # 2. File path hints (still an exact-match query, but tracked as a key_path).
    # Extracted before quoted strings so a quoted path (e.g. `` `src/config.py` ``)
    # keeps its key_paths — the (text, kind) dedup below keeps the first match.
    for match in _PATH_HINT.finditer(issue_text):
        path = match.group(1).strip()
        queries.append(
            RewrittenQuery(
                text=path,
                kind=SearchKind.EXACT.value,
                key_paths=(path,),
            )
        )

    # 3. Quoted strings (exact identifiers)
    for match in _QUOTE.finditer(issue_text):
        quoted = match.group(1).strip()
        if len(quoted) >= 3:
            queries.append(RewrittenQuery(text=quoted, kind=SearchKind.EXACT.value))

    # 4. CamelCase symbols (class/function names)
    for match in _SYMBOL_HINT.finditer(issue_text):
        symbol = (match.group(1) or match.group(2) or "").strip()
        if len(symbol) >= 3:
            queries.append(RewrittenQuery(text=symbol, kind=SearchKind.SYMBOL.value))

    # 5. Dependency/import hints
    dependency_match = _DEPENDENCY_PATTERN.search(issue_text)
    if dependency_match:
        start = max(0, dependency_match.start() - 20)
        end = min(len(issue_text), dependency_match.end() + 60)
        queries.append(
            RewrittenQuery(text=issue_text[start:end].strip(), kind=SearchKind.DEPENDENCY.value)
        )

    # 6. Always add the full issue text as a General fallback
    queries.append(RewrittenQuery(text=issue_text.strip(), kind=SearchKind.GENERAL.value))

    # Deduplicate by (text.casefold(), kind)
    seen: set[tuple[str, str]] = set()
    unique: list[RewrittenQuery] = []
    for item in queries:
        key = (item.text.casefold(), item.kind)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return QueryRewritePlan(queries=tuple(unique[:max_queries]))
