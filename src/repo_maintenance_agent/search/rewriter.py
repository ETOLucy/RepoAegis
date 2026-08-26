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
_TEST_PATTERN = re.compile(r"\btest[_ ]", re.IGNORECASE)
_CONFIG_PATTERN = re.compile(
    r"\b(config|settings|configure|option|parameter)\b",
    re.IGNORECASE,
)
_DEPENDENCY_PATTERN = re.compile(
    r"\b(depend|requirements?|package|module|install|pip|npm|cargo|gem|import)\b",
    re.IGNORECASE,
)
_REGEX_PATTERN = re.compile(
    r"\b(regex|pattern|match|substitute|replace)\b",
    re.IGNORECASE,
)
_SCHEMA_PATTERN = re.compile(
    r"\b(schema|model|migration|field|column|table|database|entity|dataclass)\b",
    re.IGNORECASE,
)


_EXPLORE_PATTERN = re.compile(
    r"\b(how does|what does|explain|overview|architecture|structure|design)\b",
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
    1. error   — error messages, tracebacks, exception types
    2. exact   — exact identifiers, quoted strings, dotted paths
    3. history — git history, blame, why/who questions
    4. symbol  — CamelCase symbols, class/function names
    5. path    — file path hints
    6. test    — test-related queries
    7. config  — config-related queries
    8. dependency — dependency-related queries
    9. explore — exploratory questions
    10. general — fallback
    """
    if _ERROR_PATTERN.search(text):
        return SearchKind.ERROR.value
    if _QUOTE.search(text) or re.search(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", text):
        return SearchKind.EXACT.value
    if _HISTORY_PATTERN.search(text):
        return SearchKind.HISTORY.value
    if _SYMBOL_HINT.search(text):
        return SearchKind.SYMBOL.value
    if _PATH_HINT.search(text):
        return SearchKind.PATH.value
    if _TEST_PATTERN.search(text):
        return SearchKind.TEST.value
    if _CONFIG_PATTERN.search(text):
        return SearchKind.CONFIG.value
    if _DEPENDENCY_PATTERN.search(text):
        return SearchKind.DEPENDENCY.value
    if _REGEX_PATTERN.search(text):
        return SearchKind.REGEX.value
    if _SCHEMA_PATTERN.search(text):
        return SearchKind.SCHEMA.value
    if _EXPLORE_PATTERN.search(text):
        return SearchKind.EXPLORE.value
    return SearchKind.GENERAL.value


def rewrite_queries(issue_text: str, *, max_queries: int = 4) -> QueryRewritePlan:
    """Rule-based issue -> search queries splitter.

    Mirrors the CGM 'Rewriter' idea (issue -> multiple search queries) without
    any extra dependency or LLM call: exact quoted strings, file-path hints,
    error messages, CamelCase symbols, and other patterns are extracted into
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
        # Extract the error line (up to 200 chars)
        start = max(0, error_match.start() - 20)
        end = min(len(issue_text), error_match.end() + 80)
        error_text = issue_text[start:end].strip()
        if error_text:
            queries.append(RewrittenQuery(text=error_text, kind=SearchKind.ERROR.value))

    # 2. Quoted strings (exact identifiers)
    for match in _QUOTE.finditer(issue_text):
        quoted = match.group(1).strip()
        if len(quoted) >= 3:
            queries.append(RewrittenQuery(text=quoted, kind=SearchKind.EXACT.value))

    # 3. File path hints
    for match in _PATH_HINT.finditer(issue_text):
        path = match.group(1).strip()
        queries.append(
            RewrittenQuery(
                text=path,
                kind=SearchKind.PATH.value,
                key_paths=(path,),
            )
        )

    # 4. CamelCase symbols (class/function names)
    for match in _SYMBOL_HINT.finditer(issue_text):
        symbol = (match.group(1) or match.group(2) or "").strip()
        if len(symbol) >= 3:
            queries.append(RewrittenQuery(text=symbol, kind=SearchKind.SYMBOL.value))

    # 6. Regex pattern hints
    regex_match = _REGEX_PATTERN.search(issue_text)
    if regex_match:
        queries.append(RewrittenQuery(text=regex_match.group(0), kind=SearchKind.REGEX.value))

    # 7. Schema/model hints
    schema_match = _SCHEMA_PATTERN.search(issue_text)
    if schema_match:
        queries.append(RewrittenQuery(text=schema_match.group(0), kind=SearchKind.SCHEMA.value))

    # 8. Always add the full issue text as a General fallback
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
