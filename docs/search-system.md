# 混合检索系统

> 本文描述 Research 阶段用来把一个 Issue 定位到具体代码的检索子系统。
> 内容对照 `src/repo_maintenance_agent/search/` 源码核实（核实日期见文末）；涉及尚未接线或存在已知偏差的地方会明确标出，不做美化。

## 1. 为什么分两层分类

检索请求先后经过两层分类，职责分开：

- **SearchKind（需求侧）**——由 Rewriter（LLM 版或规则版）从 Issue/查询文本里判断出来的**语义意图**，一共 18 种，定义在 `search/kind_mapping.py`。
- **QueryKind（供给侧）**——实际可执行的检索器种类，一共 7 种，定义在 `search/router.py`。

一个 SearchKind 会映射到一组 QueryKind（主搜 + 副搜），这样"改一种查询意图的检索策略"不需要碰检索器实现，"新增一种检索器"也不需要改 Rewriter。

```text
Issue 文本
  → Rewriter（LLM 或规则版）
  → RewrittenQuery { text, kind: SearchKind, key_paths }
  → kind_mapping.get_strategy(kind)
  → SearchStrategy { primary_kinds, secondary_kinds, enable_reranker }
  → HybridSearchService 按 QueryKind 分发给具体 SearchPort 实现
```

## 2. QueryKind：7 种检索器

| QueryKind | 实现 | 说明 |
|---|---|---|
| `LEXICAL` | `LocalLexicalSearch`（ripgrep） | 精确子串匹配，零索引延迟 |
| `BM25` | `BM25Search` | 词频-逆文档频率的通用全文检索，几乎所有策略的副搜兜底 |
| `VECTOR` | `VectorSearch` | 基于 embedding 的语义检索 |
| `SYMBOL` | `SymbolSearch` | AST 解析出的类名/函数名/方法名匹配 |
| `HISTORY` | `GitHistorySearch` | git blame/log 分析——**源码注释里标注为"未完整实现"**（`router.py` 中 `HISTORY` 的说明），当前主要给 `history`/`ci_cd` 两个 SearchKind 用 |
| `OPENSEARCH` | `OpenSearchHybridAdapter` | 仅 `GENERAL` 策略的主搜之一 |
| `GRAPH` | `GraphSearch` | 调用图/import 图检索，见第 5 节 |

## 3. SearchKind → SearchStrategy 映射表

以下表格逐条对照 `search/kind_mapping.py::KIND_TO_STRATEGY` 核实，如有修改以源码为准：

| SearchKind | 主搜 QueryKind | 副搜 QueryKind | Reranker |
|---|---|---|---|
| `exact` | LEXICAL, BM25 | BM25 | 否 |
| `path` | LEXICAL, BM25 | BM25 | 否 |
| `error` | LEXICAL, BM25 | BM25 | 否 |
| `regex` | LEXICAL, BM25 | BM25 | 否 |
| `config` | LEXICAL, BM25 | BM25 | 否 |
| `ui` | LEXICAL, BM25 | BM25 | 否 |
| `security` | LEXICAL, BM25 | BM25 | 否 |
| `test` | LEXICAL, BM25 | BM25, VECTOR | 否 |
| `symbol` | SYMBOL, GRAPH, BM25 | BM25, VECTOR | 是 |
| `definition` | SYMBOL, GRAPH, BM25 | BM25, VECTOR | 是 |
| `api` | SYMBOL, BM25 | BM25, VECTOR | 是 |
| `dependency` | LEXICAL, BM25, GRAPH | BM25 | 否 |
| `schema` | SYMBOL, BM25, VECTOR | BM25, VECTOR | 是 |
| `performance` | BM25, VECTOR | BM25, VECTOR | 是 |
| `general` | BM25, VECTOR, OPENSEARCH | BM25, VECTOR | 是 |
| `explore` | VECTOR, BM25 | BM25, VECTOR | 是 |
| `history` | HISTORY, BM25 | BM25 | 否 |
| `ci_cd` | LEXICAL, BM25, HISTORY | BM25 | 否 |

设计原则（源码注释原文）：主搜按语义选最精准的检索器，副搜始终是 BM25（+VECTOR）作为安全网；主副搜并行执行，结果用 RRF 融合；映射表显式、可维护，改一个 kind 不影响其他 kind。

`symbol` / `definition` / `dependency` 三种的主搜都带上了 `GRAPH`——这三种问的本质是"这个符号在哪/谁用了它"，调用图/import 图能比纯文本或语义检索给出更精确的答案。

## 4. 规则版 Rewriter：kind 检测优先级

没有 LLM 或 LLM 调用失败时，`search/rewriter.py::_detect_kind()` 用固定优先级的正则依次匹配（越靠前越优先）：

1. `error` — 错误消息、Traceback、异常类型关键字
2. `exact` — 引号内文本、`a.b` 形式的点分路径
3. `history` — why/history/commit/blame/who 等
4. `symbol` — CamelCase 或点后小写标识符
5. `path` — 文件扩展名路径提示
6. `test` / 7. `config` / 8. `dependency` / 9. `regex` / 10. `schema` / 11. `performance` / 12. `security` / 13. `api` / 14. `ui` / 15. `ci_cd` — 各自的关键词正则
16. `explore` — how/what/explain/overview/architecture 等探索性问法
17. `general` — 兜底，`rewrite_queries()` 总会额外把整段原文作为一条 `general` 查询加进去

`rewrite_queries()` 按以上顺序把一段 Issue 文本拆成多条 `RewrittenQuery`，去重后最多保留 `max_queries`（默认 4）条。

## 5. GRAPH 检索：调用图 + import 图

范围刻意收窄：只做**调用图 + import 图**，不做包含数据流分析的完整 Code Property Graph（`search/codegraph.py` 顶部注释明确写了这一取舍）。它解决的是 Localizer 原有四个动作（search/read/blame/finish）回答不了的两个高频问题："这个符号在哪定义的" 和 "谁调用/导入了这个符号"。

- `codegraph.py::build_repo_graph(root)` 用 `tree-sitter`（`tree_sitter_python`）遍历仓库下所有 `*.py` 文件（**当前只解析 Python**，其他语言的调用/import 图暂不覆盖），产出 `RepoGraph`，含三张表：
  - `definitions`：函数/方法/类定义，方法名带上外层类名做出 `Foo.bar` 这样的限定名
  - `references`：函数调用点（`helper(x)` 或 `obj.bar(x)`）
  - `imports`：`import` / `from ... import` 边，记录模块名和本地绑定名
  - 查询方法 `definitions_for` / `references_for` / `importers_of` 都是先精确限定名匹配，找不到再退化到叶子名匹配（`bar` 也能找到 `Foo.bar`）
- `search/adapters/graph.py::GraphSearch` 是 `SearchPort` 实现，把查询文本切出标识符 token，逐个查 `RepoGraph` 的三张表，定义类命中权重最高（3.0）> 引用（2.0）> import（1.0），按分数排序截断到 `top_k`。
- 同一套 `make_hit` / `read_snippet` 辅助函数被 `agents/localizer.py` 的 `goto_definition` / `find_references` 两个动作复用，保证"作为检索命中"和"作为定位动作结果"用同一种方式生成代码片段。

## 6. 主副搜并行 + RRF 融合 + 空结果回退

`search/service.py::HybridSearchService.search()` 的实际执行顺序：

1. 用 `kind`（若无 Rewriter 提供的 kind，退化到 `SearchRouter` 的正则路由）解析出主搜、副搜各自的 `QueryKind` 集合。
2. 主搜、副搜各自内部如果对应多个 `QueryKind`，先用 `reciprocal_rank_fusion` 融合成一组结果，再和另一路一起进入外层融合——即"检索器内部融合"和"主副搜融合"用的是同一个 RRF 实现（`search/fusion.py`，`rank_constant=60`，按 `1/(rank_constant+rank)` 累加分数，同分再按 `hit_id` 排序取前 `limit` 条）。
3. **回退触发条件是融合结果为空（`len(fused) == 0`），不是"少于某个阈值"**——最多重试 2 次（`service.py` 里硬编码 `max_retries = 2`），每次都把 `current_kind` 切换为 `"general"` 重新做一遍主副搜+融合；如果已经是 `general` 则不再回退。

> **已知差异**：`kind_mapping.SearchStrategy` 上定义了逐 kind 的 `max_retries` 字段（大多数 kind 是 1，`general`/`explore`/`performance` 是 2），但 `HybridSearchService.search()` 目前并没有读取这个字段，回退次数是服务里硬编码的固定值 2。这个字段目前是"声明了但未接线"的配置，如果你在改这部分代码，留意别假设它已经生效。

## 7. 索引与文档模型

代码不按固定字符数切块，优先按函数/类/方法边界切（见 `search/index.py::ingest_workspace` 等实现）。检索命中（`SearchHit`）统一带 `path` / `line_start` / `line_end` / `symbol` / `score` / `source`，`source` 在多检索器命中同一结果时会合并成 `"a+b"` 这样的字符串，方便追溯一条结果究竟是被哪些检索器同时命中的。

## 延伸阅读

- [`architecture.md`](architecture.md) — Research 节点在整条流水线里的位置。
- [`architecture-flow.md`](architecture-flow.md) — 本文核心内容的 Mermaid 图版本。
- [`refactor-plan.md`](refactor-plan.md) 三、2 — GRAPH 检索的引入背景与后续计划（多语言支持、SearchKind 精简等）。
