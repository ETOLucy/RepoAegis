# RepoAegis Search 模块完全解读（中文版）

> 本文档解释 `src/repo_maintenance_agent/search/` 模块：有哪些检索通道、各自干什么、它们怎么协作、有没有分层选用机制，以及"所有方法全用上会不会反而更差"的取舍结论。
> 基于仓库 HEAD `a0cbbf9` 实际代码。

---

## 1. 整体架构：一个"检索通道工厂 + 路由器 + 融合器"的三段式管道

搜索模块解决的问题是：**给定一句自然语言问题（issue），从仓库里找出最相关的代码位置**。它不是单一搜索，而是"多条通道并行搜 → 按规则选通道 → 分数融合 → （可选）重排"。

```
用户 issue 文本
      │
      ▼
┌──────────────┐     ┌──────────────────┐
│ SearchRouter │ ──► │ HybridSearchService │
│ (路由分层)    │     │ (并行调用选中通道) │
└──────────────┘     └──────────────────┘
      │                      │
      │        ┌─────────────┼──────────────┐
      │        ▼             ▼              ▼
      │   BM25Search     SymbolSearch   VectorSearch
      │   (词频)         (符号定义/引用)  (语义向量)
      │        │             │              │
      │        └──────┬──────┘              │
      │               ▼                     │
      │      RipgrepSearch              GitHistorySearch
      │      (精确子串)                   (git 历史)
      │               │                     │
      │               ▼                     ▼
      │        ┌──────────────────┐
      │        │ reciprocal_rank_fusion │  ← RRF 融合
      │        └──────────────────┘
      │               │
      │               ▼
      │        ┌──────────────┐
      │        │ LLMReranker  │  ← 可选：LLM 二段精排
      │        └──────────────┘
      │               │
      │               ▼
      │        去重 (dedupe by location)
      │               │
      │               ▼
      │       最终 SearchHit 列表（供 research 节点做证据）
```

### 文件地图

| 文件 | 角色 |
|---|---|
| `search/router.py` | **路由分层**：根据问题文本关键词决定本次用哪些通道 |
| `search/service.py` | 编排：选中通道 → `asyncio.gather` 并行 → RRF 融合 |
| `search/fusion.py` | **RRF 融合**：把多通道结果合并成一个排序 |
| `search/index.py` | 索引构建：BM25 + Symbol（+ Vector）三通道的数据基础 |
| `search/adapters/ripgrep.py` | 精确子串通道（依赖 `rg` 二进制） |
| `search/adapters/local.py` | 本地词法通道（无 rg 时的回退） |
| `search/adapters/opensearch.py` | 可选 OpenSearch 通道（企业级，默认不用） |
| `search/history.py` | git 历史通道（`git log` 找"为什么引入"） |
| `search/embeddings.py` | 向量嵌入客户端 |
| `search/reranker.py` | LLM 二段精排（可选） |
| `search/rewriter.py` | 规则式查询改写（在 `agents/query_rewriter.py` 有模型版） |
| `search/production.py` | **生产入口** `WorkspaceIndex`：组装所有通道 + 懒加载索引 + 缓存 |

---

## 2. 各检索通道：各自解决什么问题

### 2.1 BM25（词频统计）—— 最朴素的"关键词匹配"
- 文件：`search/index.py` → `BM25Search`
- 原理：把仓库文件切成代码块（chunk），对每个 chunk 统计查询词出现频率（TF-IDF 类算法）。
- 优点：快、零依赖、无需训练、对代码中的普通标识符效果好。
- 缺点：**不懂语义**。搜 "handle payment" 找不到 "process_transaction"，因为词面不重叠。
- 适用：大部分常规问题（函数名、变量名、常见词）。

### 2.2 Symbol（符号索引）—— 精确的"定义/引用/继承"
- 文件：`search/index.py` → `SymbolSearch`
- 原理：轻量解析代码里的**符号**（类名、函数名、变量名），记录"这个符号在哪定义、在哪被引用、继承谁"。
- 优点：对"我要改 `UserService` 的定义"这种问题，命中率极高，且能给出符号语义（如 `symbol="UserService"`）。
- 缺点：依赖简单的符号抽取，对动态语言（Python 的猴子补丁、装饰器）覆盖有限。
- 适用：明确的类名/函数名/变量名查询。

### 2.3 Vector（语义向量）—— 理解"意思"而非"字面"
- 文件：`search/embeddings.py` + `search/index.py` → `VectorSearch`
- 原理：把查询和代码块分别编码成向量（embedding），用余弦相似度找语义相近的块。
- 优点：能跨词面找语义（"payment processing" ↔ "billing flow"）。
- 缺点：**需要 API key**（`OPENAI_EMBEDDING_API_KEY`）；对代码这种结构化文本，向量区分度有时不如词频；有额外成本。
- 适用：查询与代码用词差异大的场景（用户用自然语言描述 bug，代码用特定术语）。

### 2.4 Lexical / Ripgrep（精确子串）—— 报错字符串专用
- 文件：`search/adapters/ripgrep.py`（`RipgrepSearch`）+ `search/adapters/local.py`（`LocalLexicalSearch` 回退）
- 原理：直接 `rg` 在仓库里搜**精确子串**。
- 优点：对 `Traceback: ValueError: ...`、`"some error message"`、`Foo.bar()` 这种**必须逐字符匹配**的内容，BM25 和向量都容易漏，精确搜索一抓一个准。
- 缺点：只认字面，不认同义词；对常见词会命中一大堆。
- 适用：报错信息、异常类型、带引号的字符串、完全限定符号名。

### 2.5 History（git 历史）—— 回答"为什么有这行代码"
- 文件：`search/history.py` → `GitHistorySearch`
- 原理：跑 `git log` 找提交记录，把提交 message（subject+body）和改动文件路径拼起来，和查询词做词频匹配。
- 优点：能回答"为什么引入/谁改过/哪个 commit 改了这"这类**时间维度**问题，这是其他通道做不到的。
- 缺点：只能找到"最近 100 条提交"（`--max-count=100`）；commit message 写得不清楚时效果差。
- 适用：`why introduced X`、`when did Y change`、`which commit touched Z`。

---

## 3. 它们怎么协作：Router 决定用谁，RRF 合并，Reranker 精排

### 3.1 路由分层（SearchRouter）
`search/router.py` 用三个正则决定本次查询走哪些通道：

```python
_SYMBOL  = re.compile(r"\b(callers?|callees?|references?|definition|implements?|inherits?|symbol)\b")
_HISTORY = re.compile(r"\b(why|history|commit|changed|introduced|blame)\b")
_EXACT   = re.compile(r"(Traceback|Exception|Error:|[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*|[\"\'`][^\"\'`]{3,}[\"\'`])")
```

路由规则（优先级从上到下）：
1. 含 "callers/references/definition/symbol" → `{SYMBOL, BM25}`
2. 含 "why/history/commit/introduced/blame" → `{HISTORY, BM25}`
3. 含 Traceback/Error:/`Foo.bar`/带引号字符串 → `{LEXICAL, BM25}`
4. 默认 → `{BM25, VECTOR}`

**这就是"分层选用"机制**：不是所有查询都跑全部通道。BM25 几乎总是带上（保底），其余通道按查询特征动态加。这样既保证召回率，又控制成本（Vector 只在默认路径才跑）。

### 3.2 RRF（Reciprocal Rank Fusion）融合
`search/fusion.py` 的 `reciprocal_rank_fusion`：

```python
score(hit) = Σ 每个通道中 1 / (60 + rank)
```

- 每个通道给出自己的排序，RRF 不看原始分数（不同通道分数不可比），只看**名次**。
- 名次越靠前，贡献越大；`60` 是平滑常数（经典 RRF 取值）。
- 多个通道都命中的结果，名次叠加，自然排到前面。
- 融合后 `source` 字段记录来源（如 `"bm25+history"`）。

> 为什么要 RRF 而不是把分数加权平均？因为 BM25 的 0.8 和向量的 0.8 不是一个量纲，直接相加没意义；名次是通用的。RRF 是业界最稳健的多路召回融合方法（无需调参、对异常分数鲁棒）。

### 3.3 LLM-as-Reranker 二段精排
`search/reranker.py` 的 `LLMReranker`：

- 输入：RRF 融合后的 top-20 候选（`candidate_pool=20`）+ 查询文本。
- 动作：一次 LLM 结构化调用，让模型返回 `ranked_ids`（按相关性排序）。
- 输出：取前 10（`final_k=10`）。
- **容错**：模型调用失败时回退到 RRF 原序（检索不能因为重排失败而崩）。
- 作用：LLM 能理解"哪个文件改动了才能解决这个 issue"，比纯统计信号更贴合任务目标。

### 3.4 Query Rewrite（查询改写）—— 在检索之前
- 规则版：`search/rewriter.py`
- 模型版：`agents/query_rewriter.py` 的 `rewrite_queries_with_model`
- 作用：把一条长 issue 拆成多条针对性查询（如路径查询、符号查询、问题描述查询），再分别进检索管道。
- 失败策略：模型版失败时**回退到规则版**，规则版失败也只会得到空查询——改写失败不阻塞整个 research 流程（我们这次修复了它从"抛异常"改成"回退"）。

### 3.5 Planner + Explorer（定位循环）—— 检索之上的决策层
- 文件：`agents/localizer.py` → `Localizer`
- 作用：不是又一个检索通道，而是**指挥检索**的智能体：根据已有证据决定下一步（再搜 / 读文件 / git blame / 结束），最多 3 轮。
- 与通道的关系：Localizer 调用 `search_code`（即上面的整条检索管道）和 `read_files` 工具，把结果累积为证据。
- 容错：模型无法决策时优雅降级为 `finish`（保留已收集证据，不阻塞）。

---

## 4. 有没有分层选用机制？—— 有，而且分了三层

**第一层：Router 按查询类型选通道**（`router.py`，见 3.1）。这是"不同场景用不同检索策略"的直接实现。

**第二层：WorkspaceIndex 按配置决定通道有无**（`production.py`）：
- S1（默认）：BM25 + Symbol（零依赖、零 API）
- M1（可选）：+ Vector（需 embedding key）
- M2（可选）：+ Lexical（需 rg）
- 生产工厂 `ProductionGraphFactory` 会按 settings 组装：`lexical=default_lexical_search(workspace)`、`history=GitHistorySearch(...)`、`reranker=LLMReranker(...)`。
- `VectorSearch` 只有在传了 `embeddings` 时才启用（`vector=... if self._embeddings is not None else None`）。

**第三层：research 节点的 Localizer 决定"还要不要再搜"**（`agents/nodes.py` 调用 `Localizer.localize`）。这是行为层面的分层：不是每次都把仓库翻个底朝天，而是证据够了就停。

---

## 5. "全部方法都用上会不会反而更差？"—— 取舍分析

### 5.1 会，而且确实有真实代价。逐项拆：

| 方法 | 增加的成本 | 什么时候反而有害 |
|---|---|---|
| Vector | embedding API 调用（每次检索 ×N 块）、延迟 | 查询词与代码词面重叠度高时，BM25 已够；Vector 结果常与 BM25 重复，RRF 里只是陪跑，还拖慢 |
| Ripgrep 精确 | 无 API，但会命中大量噪音 | 对常见词（`get`、`set`、`error`）命中几百处，把 RRF 排名稀释 |
| History | 一次 `git log` 子进程 | 对"改 bug"类问题（非"为什么引入"）没帮助，纯噪音 |
| Reranker | 每检索一次 LLM 调用（token 成本 + 延迟） | 候选本来就少/质量高时，重排收益边际递减；模型不稳定时可能把对的排后 |
| Localizer | 最多 3 轮 × (LLM 决策 + 检索) | 对简单问题，3 轮纯属浪费 token；对证据充分的问题，第一轮就 finish 了 |

### 5.2 关键结论：**"全用"不等于"更好"，正确姿势是"分层 + 条件启用"**

1. **Router 已经是分层防线**：普通查询根本不会触发 History/Lexical，Vector 只在默认路径跑。所以"全用"在当前架构里**不是字面意义的全部通道都跑**——Router 会挡掉大部分。
2. **真正的取舍点在 Reranker 和 Localizer**：
   - 这两个是**每次查询都加成本**的（一个 LLM 调用 / 最多 3 轮 LLM+检索）。
   - 收益只体现在"查询足够复杂、候选足够多"时。
3. **推荐的启用策略**（与代码现状一致）：
   - **默认线上**：BM25 + Symbol + Ripgrep + History + Reranker（Reranker 有 fallback，失败不阻塞）。Vector **只在 embedding key 已配置时**才启用（代码已实现）。
   - **成本敏感 / 低延迟场景**：关 Reranker 和 Localizer（`reranker=None`），只留 BM25 + Symbol + 按需 Lexical。
   - **复杂问题**（SWE-bench 类）：全开，因为定位质量直接决定最终解决率。
4. **测量优先**：不要凭感觉决定开关。在固定测试集（如 `docs/evaluation-results/` 的 SWE-bench 任务）上 A/B：开/关 Reranker、开/关 Vector，对比 `recall@10` 与端到端解决率，再决定默认配置。

### 5.3 我们这次修复中体现的取舍原则
- `query_rewriter` 模型失败 → **回退规则版**（不阻塞）。
- `localizer` 模型失败 → **降级 finish**（不阻塞）。
- `reranker` 模型失败 → **回退 RRF 原序**（不阻塞）。
- `fusion` 单通道命中 → **保留原始 source**（不强行打合并标签）。

这些都不是"全用"的胜利，而是"**每层都有降级路径**"的设计——**可用的方法都留着，但任何一个失败都不让整条检索管道崩掉**。

---

## 6. 一份快速速查表

| 你想回答的问题 | 走哪个通道 | 触发关键词 |
|---|---|---|
| "`UserService` 的定义/引用/继承" | Symbol + BM25 | `definition`, `references`, `symbol` |
| "这个报错 `ValueError: bad thing` 在哪" | Ripgrep + BM25 | `Traceback`, `Error:`, 带引号串 |
| "为什么引入 bearer token" | History + BM25 | `why`, `introduced`, `blame`, `history` |
| "处理支付逻辑的代码在哪" | BM25 + Vector | 默认（无关键词） |
| "先搜一轮，再看要不要深挖" | Localizer 循环 | research 节点自动触发 |

---

## 7. 结语

RepoAegis 的 search 模块不是"一个搜索函数"，而是一个**可组合的检索管道**：

```
查询改写 → 路由分层 → 多通道并行 → RRF 融合 → LLM 精排 → 去重
     ↑                                                          │
     └─────────────── Localizer 定位循环（再搜/读/查历史）────────┘
```

它的设计哲学是：**能用的召回手段都接进来，但通过路由分层控制成本，通过降级路径保证可用性，通过 A/B 测量决定默认开关。** 全部方法"同时启用"不是最优解，**按查询类型和成本预算分层启用**才是。
