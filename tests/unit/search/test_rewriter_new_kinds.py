"""Tests for the new SearchKind detections in rewriter.py.

Covers: PERFORMANCE, SECURITY, API, UI, CI_CD
"""

from __future__ import annotations

from repo_maintenance_agent.search.rewriter import rewrite_queries


def test_rewrite_detects_performance_kind() -> None:
    plan = rewrite_queries("The system is slow due to cache miss in the query layer.")
    texts = [q.text for q in plan.queries]
    assert any(q.kind == "performance" for q in plan.queries)


def test_rewrite_detects_security_kind() -> None:
    plan = rewrite_queries("SQL injection vulnerability in the login endpoint.")
    texts = [q.text for q in plan.queries]
    assert any(q.kind == "security" for q in plan.queries)


def test_rewrite_detects_api_kind() -> None:
    plan = rewrite_queries("The REST API endpoint /users returns 500 on empty body.")
    texts = [q.text for q in plan.queries]
    assert any(q.kind == "api" for q in plan.queries)


def test_rewrite_detects_ui_kind() -> None:
    plan = rewrite_queries("The button component does not render correctly on mobile.")
    texts = [q.text for q in plan.queries]
    assert any(q.kind == "ui" for q in plan.queries)


def test_rewrite_detects_ci_cd_kind() -> None:
    plan = rewrite_queries("The GitHub Actions workflow fails during deploy stage.")
    texts = [q.text for q in plan.queries]
    assert any(q.kind == "ci_cd" for q in plan.queries)
