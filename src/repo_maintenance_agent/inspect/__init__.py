# ruff: noqa: RUF002
"""Inspect AI 集成包（UK AISI Inspect 评测框架桥接）。"""

from __future__ import annotations

from typing import Any

__all__ = [
    "EvalResult",
    "load_repoaegis_holdout",
    "parse_inspect_log",
    "repoaegis_agent",
    "repoaegis_swe_progress_scorer",
    "repoaegis_verified",
]


def __getattr__(name: str) -> Any:
    """Lazily expose all public symbols to avoid requiring inspect_ai at import time."""
    _lazy: dict[str, tuple[str, str]] = {
        "repoaegis_agent": (
            "repo_maintenance_agent.inspect.bridge",
            "repoaegis_agent",
        ),
        "load_repoaegis_holdout": (
            "repo_maintenance_agent.inspect.dataset",
            "load_repoaegis_holdout",
        ),
        "EvalResult": ("repo_maintenance_agent.inspect.parser", "EvalResult"),
        "parse_inspect_log": (
            "repo_maintenance_agent.inspect.parser",
            "parse_inspect_log",
        ),
        "repoaegis_swe_progress_scorer": (
            "repo_maintenance_agent.inspect.scorers",
            "repoaegis_swe_progress_scorer",
        ),
        "repoaegis_verified": (
            "repo_maintenance_agent.inspect.pilot_task",
            "repoaegis_verified",
        ),
    }
    if name in _lazy:
        import importlib

        module = importlib.import_module(_lazy[name][0])
        return getattr(module, _lazy[name][1])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
