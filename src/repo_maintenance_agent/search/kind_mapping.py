"""Search kind mapping table: Rewriter kind → QueryKind search strategy.

设计原则：
- 每个 Rewriter kind 映射到一组主搜 QueryKind（精准检索）
- 副搜始终是 BM25（+VECTOR 如果可用）作为安全网
- 主搜和副搜并行执行，结果通过 RRF 融合
- 映射表是显式的、可维护的，修改一个 kind 不影响其他 kind
目前支持 18 种 SearchKind。
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from repo_maintenance_agent.search.router import QueryKind


class SearchKind(StrEnum):
    """Rewriter 输出的查询种类（比 SearchRouter 的 QueryKind 更语义化）。

    这些 kind 由 Rewriter（LLM 或规则版）生成，每个 kind 对应一组
    检索策略。SearchKind 是"需求侧"分类，QueryKind 是"供给侧"分类。
    """
    EXACT = "exact"           # 精确标识符、错误字符串、引号内的文本
    PATH = "path"             # 文件路径提示
    SYMBOL = "symbol"         # CamelCase 符号、类名、函数名
    GENERAL = "general"       # 通用自然语言描述
    ERROR = "error"           # 错误消息、Traceback、异常文本
    HISTORY = "history"       # "为什么改"、"谁改的"等 git 历史查询
    EXPLORE = "explore"       # 探索性查询："这个模块做什么的"
    DEFINITION = "definition" # "X 在哪里定义的"
    TEST = "test"             # 测试相关查询
    CONFIG = "config"         # 配置相关查询
    DEPENDENCY = "dependency" # 依赖相关查询
    REGEX = "regex"           # 正则表达式模式匹配
    SCHEMA = "schema"         # 数据库 schema、模型定义、数据类
    PERFORMANCE = "performance"  # 性能优化相关查询
    SECURITY = "security"        # 安全漏洞相关查询
    API = "api"                  # API 接口相关查询
    UI = "ui"                    # 前端 UI 相关查询
    CI_CD = "ci_cd"              # CI/CD 配置相关查询


class SearchStrategy(NamedTuple):
    """一种 kind 的搜索策略定义。

    primary_kinds:   主搜使用的 QueryKind 集合（精准检索）
    secondary_kinds: 副搜使用的 QueryKind 集合（兜底检索）
    enable_reranker: 是否启用 LLM 重排序
    max_retries:     搜索失败时的最大重试次数
    """
    primary_kinds: frozenset[QueryKind]
    secondary_kinds: frozenset[QueryKind] = frozenset({QueryKind.BM25})
    enable_reranker: bool = False
    max_retries: int = 1


# =========================================================================
# 核心映射表：SearchKind → SearchStrategy
# =========================================================================
# 设计说明：
# - 主搜（primary）：根据 kind 的语义选择最精准的检索器
# - 副搜（secondary）：BM25 作为通用兜底，确保不会因为主搜失败而丢失结果
# - 主搜和副搜并行执行，结果通过 RRF 融合
# - 对于需要语义理解的 kind（general, explore），启用 VECTOR 副搜
# - 对于需要精确匹配的 kind（exact, error, path），启用 LEXICAL 主搜
# - 对于符号查询（symbol, definition），启用 SYMBOL 主搜
# =========================================================================

KIND_TO_STRATEGY: dict[SearchKind, SearchStrategy] = {
    # ---- 精确匹配类 ----
    SearchKind.EXACT: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        # 精确匹配不需要 reranker，EXACT 本身就够精准
        enable_reranker=False,
    ),
    SearchKind.PATH: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        enable_reranker=False,
    ),
    SearchKind.ERROR: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        # 错误消息常包含长字符串，LEXICAL 精确匹配比 BM25 分词更可靠
        enable_reranker=False,
    ),

    # ---- 符号/定义类 ----
    SearchKind.SYMBOL: SearchStrategy(
        primary_kinds=frozenset({QueryKind.SYMBOL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        # 符号查询可以启用 reranker 来精选最相关的定义
        enable_reranker=True,
    ),
    SearchKind.DEFINITION: SearchStrategy(
        primary_kinds=frozenset({QueryKind.SYMBOL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        enable_reranker=True,
    ),

    # ---- Git 历史类 ----
    SearchKind.HISTORY: SearchStrategy(
        primary_kinds=frozenset({QueryKind.HISTORY, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        enable_reranker=False,
    ),

    # ---- 语义/探索类 ----
    SearchKind.GENERAL: SearchStrategy(
        primary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR, QueryKind.OPENSEARCH}),
        secondary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        # 通用查询启用 reranker 提升结果质量
        enable_reranker=True,
        max_retries=2,
    ),
    SearchKind.EXPLORE: SearchStrategy(
        primary_kinds=frozenset({QueryKind.VECTOR, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        enable_reranker=True,
        max_retries=2,
    ),

    # ---- 特定文件类 ----
    SearchKind.TEST: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        enable_reranker=False,
    ),
    SearchKind.CONFIG: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        enable_reranker=False,
    ),
    SearchKind.DEPENDENCY: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25, QueryKind.SYMBOL}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        # 依赖查询可能涉及 import 语句（符号解析）
        enable_reranker=False,
    ),
    SearchKind.REGEX: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        enable_reranker=False,
    ),
    SearchKind.SCHEMA: SearchStrategy(
        primary_kinds=frozenset({QueryKind.SYMBOL, QueryKind.BM25, QueryKind.VECTOR}),
        secondary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        enable_reranker=True,
    ),

    # ---- 性能/安全/API/UI/CI/CD 类 ----
    SearchKind.PERFORMANCE: SearchStrategy(
        primary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        secondary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        enable_reranker=True,
        max_retries=2,
    ),
    SearchKind.SECURITY: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        enable_reranker=False,
    ),
    SearchKind.API: SearchStrategy(
        primary_kinds=frozenset({QueryKind.SYMBOL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
        enable_reranker=True,
    ),
    SearchKind.UI: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        enable_reranker=False,
    ),
    SearchKind.CI_CD: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25, QueryKind.HISTORY}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        enable_reranker=False,
    ),
}

# fallback 策略：当 kind 未知时使用
FALLBACK_STRATEGY = SearchStrategy(
    primary_kinds=frozenset({QueryKind.BM25, QueryKind.VECTOR}),
    secondary_kinds=frozenset({QueryKind.BM25}),
    enable_reranker=True,
    max_retries=1,
)


def get_strategy(kind: str) -> SearchStrategy:
    """根据 kind 字符串获取搜索策略，未知 kind 回退到 fallback。"""
    try:
        return KIND_TO_STRATEGY[SearchKind(kind)]
    except (ValueError, KeyError):
        return FALLBACK_STRATEGY


def get_primary_kinds(kind: str) -> frozenset[QueryKind]:
    """获取主搜 QueryKind 集合。"""
    return get_strategy(kind).primary_kinds


def get_secondary_kinds(kind: str) -> frozenset[QueryKind]:
    """获取副搜 QueryKind 集合。"""
    return get_strategy(kind).secondary_kinds


def get_all_kinds() -> tuple[SearchKind, ...]:
    """返回所有支持的 SearchKind。"""
    return tuple(KIND_TO_STRATEGY.keys())
