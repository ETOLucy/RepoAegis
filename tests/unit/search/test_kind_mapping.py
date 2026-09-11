"""Tests for kind_mapping: all 18 SearchKinds have valid strategies."""

from __future__ import annotations

from repo_maintenance_agent.search.kind_mapping import (
    FALLBACK_STRATEGY,
    KIND_TO_STRATEGY,
    SearchKind,
    get_all_kinds,
    get_strategy,
)
from repo_maintenance_agent.search.router import QueryKind


def test_all_18_search_kinds_have_strategies() -> None:
    """All 18 SearchKind enum values must have a strategy entry."""
    expected_count = 18
    got = len(SearchKind)
    assert got == expected_count, f"Expected {expected_count} kinds, got {got}"
    assert len(KIND_TO_STRATEGY) == expected_count


def test_every_kind_has_primary_kinds() -> None:
    """Every SearchKind must have at least one primary QueryKind."""
    for kind, strategy in KIND_TO_STRATEGY.items():
        assert len(strategy.primary_kinds) >= 1, f"{kind} has no primary kinds"


def test_every_kind_has_secondary_kinds() -> None:
    """Every SearchKind must have at least one secondary QueryKind."""
    for kind, strategy in KIND_TO_STRATEGY.items():
        assert len(strategy.secondary_kinds) >= 1, f"{kind} has no secondary kinds"


def test_get_strategy_returns_valid_strategy() -> None:
    """get_strategy() returns a valid strategy for every kind."""
    for kind in SearchKind:
        strategy = get_strategy(kind.value)
        assert strategy is not None
        assert len(strategy.primary_kinds) >= 1


def test_get_strategy_fallback_for_unknown_kind() -> None:
    """Unknown kind strings should fall back to FALLBACK_STRATEGY."""
    strategy = get_strategy("unknown_kind_xyz")
    assert strategy == FALLBACK_STRATEGY


def test_all_kinds_include_bm25() -> None:
    """BM25 should be present in every SearchKind strategy (primary or secondary)."""
    for kind, strategy in KIND_TO_STRATEGY.items():
        all_kinds = strategy.primary_kinds | strategy.secondary_kinds
        assert QueryKind.BM25 in all_kinds, f"{kind} does not include BM25"


def test_opensearch_only_in_general() -> None:
    """OPENSEARCH QueryKind should only appear in the GENERAL strategy."""
    for kind, strategy in KIND_TO_STRATEGY.items():
        all_kinds = strategy.primary_kinds | strategy.secondary_kinds
        if QueryKind.OPENSEARCH in all_kinds:
            assert kind == SearchKind.GENERAL, f"OPENSEARCH found in {kind}, should be GENERAL only"


def test_get_all_kinds_returns_all() -> None:
    """get_all_kinds() returns all 18 SearchKinds."""
    kinds = get_all_kinds()
    assert len(kinds) == 18
    assert SearchKind.GENERAL in kinds
    assert SearchKind.PERFORMANCE in kinds
    assert SearchKind.SECURITY in kinds
    assert SearchKind.API in kinds
    assert SearchKind.UI in kinds
    assert SearchKind.CI_CD in kinds


def test_reranker_enabled_for_semantic_kinds() -> None:
    """Kinds that benefit from semantic search should have reranker enabled."""
    reranker_kinds = {
        SearchKind.SYMBOL,
        SearchKind.DEFINITION,
        SearchKind.GENERAL,
        SearchKind.EXPLORE,
        SearchKind.SCHEMA,
        SearchKind.PERFORMANCE,
        SearchKind.API,
    }
    for kind in reranker_kinds:
        assert KIND_TO_STRATEGY[kind].enable_reranker, f"{kind} should have reranker enabled"


def test_graph_query_kind_used_for_symbol_definition_dependency() -> None:
    """symbol/definition/dependency ask "where is X defined / who uses X" —
    the call graph/import graph (search/codegraph.py) answers that directly,
    so GRAPH should be one of their primary (not just secondary) retrievers."""
    for kind in (SearchKind.SYMBOL, SearchKind.DEFINITION, SearchKind.DEPENDENCY):
        assert QueryKind.GRAPH in KIND_TO_STRATEGY[kind].primary_kinds, f"{kind} missing GRAPH"


def test_reranker_disabled_for_exact_kinds() -> None:
    """Kinds that rely on exact matching should not have reranker."""
    no_reranker_kinds = {
        SearchKind.EXACT,
        SearchKind.PATH,
        SearchKind.ERROR,
        SearchKind.HISTORY,
        SearchKind.TEST,
        SearchKind.CONFIG,
        SearchKind.DEPENDENCY,
        SearchKind.REGEX,
        SearchKind.SECURITY,
        SearchKind.UI,
        SearchKind.CI_CD,
    }
    for kind in no_reranker_kinds:
        assert not KIND_TO_STRATEGY[kind].enable_reranker, f"{kind} should not have reranker"
