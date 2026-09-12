"""Search kind mapping table: Rewriter kind → QueryKind search strategy.

设计原则:
- 每个 Rewriter kind 映射到一组主搜 QueryKind(精准检索)
- 副搜始终是 BM25 作为安全网
- 主搜和副搜并行执行,结果通过 RRF 融合
- 映射表是显式的,可维护的,修改一个 kind 不影响其他 kind

原有 18 种 SearchKind 里,去掉 VECTOR/OPENSEARCH(需要外部 API/集群,依赖感知重构
删掉了这两个重量级检索通道)之后,超过一半的 kind 剩下的策略两两相同——本质上
只有 5 种真正不同的检索行为,已合并:
- EXACT:  精确匹配(原 exact/path/error/test/config/regex/security/ui/ci_cd)
- SYMBOL: 符号/定义(原 symbol/definition/schema/api),GRAPH 是主搜的核心
- DEPENDENCY: 依赖/import 关系,GRAPH 直接查 import 图
- HISTORY: git 历史
- GENERAL: 其余所有语义/探索类查询(原 general/explore/performance)的兜底
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from repo_maintenance_agent.search.router import QueryKind


class SearchKind(StrEnum):
    """Rewriter 输出的查询种类(比 SearchRouter 的 QueryKind 更语义化)。"""
    EXACT = "exact"           # 精确标识符,错误字符串,引号内的文本,路径提示
    SYMBOL = "symbol"         # CamelCase 符号,类名,函数名,"X 在哪里定义的"
    HISTORY = "history"       # "为什么改","谁改的"等 git 历史查询
    DEPENDENCY = "dependency" # 依赖/import 关系相关查询
    GENERAL = "general"       # 通用自然语言描述,兜底


class SearchStrategy(NamedTuple):
    """一种 kind 的搜索策略定义。

    primary_kinds:   主搜使用的 QueryKind 集合(精准检索)
    secondary_kinds: 副搜使用的 QueryKind 集合(兜底检索)
    max_retries:     搜索失败时的最大重试次数
    """
    primary_kinds: frozenset[QueryKind]
    secondary_kinds: frozenset[QueryKind] = frozenset({QueryKind.BM25})
    max_retries: int = 1


KIND_TO_STRATEGY: dict[SearchKind, SearchStrategy] = {
    SearchKind.EXACT: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
    ),
    # symbol/definition 本质是"这个符号在哪",GRAPH(调用图/import 图, 见
    # search/codegraph.py)比纯文本/语义检索更精确,是主搜的核心。
    SearchKind.SYMBOL: SearchStrategy(
        primary_kinds=frozenset({QueryKind.SYMBOL, QueryKind.GRAPH, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
    ),
    SearchKind.HISTORY: SearchStrategy(
        primary_kinds=frozenset({QueryKind.HISTORY, QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
    ),
    # 依赖查询问的是 import 关系, GRAPH 直接查 import 图, LEXICAL 兜底裸文本匹配。
    SearchKind.DEPENDENCY: SearchStrategy(
        primary_kinds=frozenset({QueryKind.LEXICAL, QueryKind.BM25, QueryKind.GRAPH}),
        secondary_kinds=frozenset({QueryKind.BM25}),
    ),
    SearchKind.GENERAL: SearchStrategy(
        primary_kinds=frozenset({QueryKind.BM25}),
        secondary_kinds=frozenset({QueryKind.BM25}),
        max_retries=2,
    ),
}

# fallback 策略:当 kind 未知时使用
FALLBACK_STRATEGY = SearchStrategy(
    primary_kinds=frozenset({QueryKind.BM25}),
    secondary_kinds=frozenset({QueryKind.BM25}),
    max_retries=1,
)


def get_strategy(kind: str) -> SearchStrategy:
    """根据 kind 字符串获取搜索策略,未知 kind 回退到 fallback。"""
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
