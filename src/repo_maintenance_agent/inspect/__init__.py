# ruff: noqa: RUF002
"""Inspect AI 集成包（UK AISI Inspect 评测框架桥接）。

把 RepoAegis agent 接入 Inspect 的 Task / Dataset / Solver / Scorer 四元组，
目标：官方 SWE-bench 评测、baseline 对照、AegisEvo 统计门控可消费输出。

公开 API：

* :func:`load_repoaegis_holdout` —— holdout JSONL -> Inspect MemoryDataset
* :func:`repoaegis_swe_progress_scorer` —— 官方 SWE-bench 评分之上的连续进度分
* :func:`parse_inspect_log` —— .eval 日志 -> ``EvalResult`` 统一行
* :func:`repoaegis_agent` —— RepoAegis agent 的 Inspect Agent 桥接骨架
* :func:`repoaegis_verified` —— 可运行的权威判分 task（官方 swe_bench_scorer + Docker）

replay / generate solver 函数在
:mod:`repo_maintenance_agent.inspect.repoaegis_solver` 模块里，从该模块导入：

    from repo_maintenance_agent.inspect.repoaegis_solver import repoaegis_solver

注意：``repoaegis_solver`` 既是子模块名又是函数名，Python 从包顶层导入该名字
时会优先绑定子模块，因此不从包顶层导出该函数。

``repoaegis_verified`` 依赖可选的 ``inspect-evals`` / ``swebench`` 包（不在
默认安装里），因此用模块级 ``__getattr__`` 惰性导出：未装依赖时导入本包不会
失败，只有真正使用判分 API 时才加载。
"""

from __future__ import annotations

from typing import Any

from repo_maintenance_agent.inspect.bridge import repoaegis_agent
from repo_maintenance_agent.inspect.dataset import load_repoaegis_holdout
from repo_maintenance_agent.inspect.parser import EvalResult, parse_inspect_log
from repo_maintenance_agent.inspect.scorers import repoaegis_swe_progress_scorer

__all__ = [
    "EvalResult",
    "load_repoaegis_holdout",
    "parse_inspect_log",
    "repoaegis_agent",
    "repoaegis_swe_progress_scorer",
    "repoaegis_verified",
]


def __getattr__(name: str) -> Any:
    """Lazily expose the runnable grading entry points.

    ``pilot_task`` imports ``inspect_evals`` and ``swebench``, which are
    optional dependencies (not in the default RepoAegis install or CI).
    Importing this package stays lightweight; the heavier module loads only
    when the grading API is actually used.
    """
    if name == "repoaegis_verified":
        import importlib

        module = importlib.import_module("repo_maintenance_agent.inspect.pilot_task")
        return module.repoaegis_verified
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")