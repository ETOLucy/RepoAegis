from __future__ import annotations

from repo_maintenance_agent.search.rewriter import rewrite_queries


def test_rewrite_queries_extracts_quoted_exact_strings() -> None:
    plan = rewrite_queries(
        'Crash with "NoSuchKey" when load_config returns an empty dict in `src/config.py`.',
    )
    texts = [q.text for q in plan.queries]
    assert "NoSuchKey" in texts
    assert "src/config.py" in texts
    assert any(q.kind == "exact" for q in plan.queries)
    assert any(q.kind == "path" for q in plan.queries)
def test_rewrite_queries_extracts_camelcase_symbols() -> None:
    plan = rewrite_queries("RepoService.search fails after the ConfigLoader refactor.")
    texts = [q.text for q in plan.queries]
    assert any("RepoService" in text or "ConfigLoader" in text for text in texts)
    assert any(q.kind == "symbol" for q in plan.queries)
def test_rewrite_queries_always_keeps_full_issue_as_fallback() -> None:
    plan = rewrite_queries("Something broke in the pipeline.")
    assert plan.queries[-1].kind == "general"
    assert plan.queries[-1].text == "Something broke in the pipeline."
def test_rewrite_queries_deduplicates_and_respects_max_queries() -> None:
    plan = rewrite_queries(
        "Fix `load_config` in src/config.py. `load_config` also used by service.py.",
        max_queries=3,
    )
    assert len(plan.queries) <= 3
    texts = [q.text.casefold() for q in plan.queries]
    assert len(texts) == len(set(texts))
def test_rewrite_queries_empty_input_returns_single_empty_query() -> None:
    plan = rewrite_queries("   ")
    assert len(plan.queries) == 1
    assert plan.queries[0].text == ""