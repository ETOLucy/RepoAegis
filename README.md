<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/repo-aegis-mark-reversed.svg">
    <img src="docs/assets/repo-aegis-mark.svg" width="112" alt="RepoAegis 标志">
  </picture>
</p>

<h1 align="center">RepoAegis</h1>

<p align="center">
  Policy-controlled, evidence-backed 的 Issue 修复流水线：从 GitHub Issue 出发 → 定位代码 → 生成 patch → 沙箱验证 → 人工审批 → 提交 PR。
</p>

<p align="center">
  <a href="https://github.com/ETOLucy/RepoAegis/actions/workflows/ci.yml"><img src="https://github.com/ETOLucy/RepoAegis/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="https://github.com/ETOLucy/RepoAegis/actions/workflows/eval-smoke.yml"><img src="https://github.com/ETOLucy/RepoAegis/actions/workflows/eval-smoke.yml/badge.svg" alt="eval-smoke"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12-245dcc.svg" alt="Python 3.12"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-177245.svg" alt="License: Apache-2.0"></a>
</p>

<p align="center">
  <a href="README-EN.md">English</a>
</p>

---

## 项目定位

RepoAegis 是一个 **policy-controlled、evidence-backed** 的 Issue 修复流水线。它的核心职责是：给定一个 GitHub Issue，自动完成从理解问题到提交 PR 的全流程，并在每一步确保安全性和可追溯性。

与其他自动修复工具不同，RepoAegis 不追求"全自动通过"——它优先保证**每一次远程写入都经过人工确认**，**每一次代码变更都有证据支撑**，**每一次执行都在隔离环境中完成**。

## 核心设计原则

- **任务边界不可变**：租户、仓库、目标 commit 在任务开始时固定，之后不可更改。防止 Agent 在推理过程中意外偏离范围。
- **最小权限**：工具默认拒绝，仅按阶段授予必要能力。Agent 在 coding 阶段拿不到审批工具，在 review 阶段拿不到写入工具。
- **人工审批**：远程写入绑定审批信封。人在回路中确认计划、目标 commit、验证命令与工具范围后才执行。
- **隔离执行**：Docker 沙箱中运行测试与命令，digest-pinned 镜像、非 root 用户、只读根文件系统、丢弃所有 capabilities。命令不接触宿主环境。
- **可追溯**：每步操作都有并发控制与可重放副作用。Git diff、变更记录、验证证据完整留存，支持事后审计。

## 架构总览

核心编排基于 LangGraph 的 StateGraph，是一个 **10 节点条件路由图 + 有界重试 + 证据驱动兜底**：

```
START ──[route_entry]──→ intake ──→ research ──→ planning
                                │
                                └──→ code (恢复挂起任务)

planning ──[route_after_planning]──→ approval (高风险)
         │                           └──→ code (低风险跳过审批)
         │                           └──→ failure

approval ──[route_after_approval]──→ code (通过)
         │                           └──→ failure (拒绝)

code ──→ verification ──[route_after_verification]──→ review (通过)
                                                │   └──→ code (重试, CODE错误+iter<max)
                                                │   └──→ failure (其他错误)

review ──[route_after_review]──→ pr (approve)
       │                         └──→ code (重试, request_changes+iter<max)
       │                         └──→ pr (证据驱动兜底)
       │                         └──→ failure

pr ──→ finalize ──→ END
failure ──→ END
```

- **条件路由图**：不是线性流水线——每个节点后是条件分支，由路由函数基于状态 + 迭代次数 + 验证结果动态决定下一步。从 START 到 END 有 5 个路由决策点（route_entry、route_after_planning、route_after_approval、route_after_verification、route_after_review）。
- **有界重试**：verification 失败（CODE 错误且 iteration < max）→ 回到 code 重试；review request_changes 且 iteration < max → 回到 code 重试。最多重试 max_iterations 次。
- **证据驱动兜底**：review 阶段 LLM 反复 request_changes 但验证通过、改动在声明文件内、风险低时仍放行（保留警告记录）。
- **人工审批**：approval 节点使用 LangGraph 的 interrupt（human-in-the-loop），审批信封带 plan_hash 摘要，防止审批后计划被篡改。
## 模块详解

### Intake（任务理解）

**做了什么**：接收 GitHub Issue，提取结构化元数据——task_type（bugfix/feature/test/documentation/dependency/refactor）、summary、acceptance_criteria、constraints、unknowns。

**为什么这么做**：Issue 是自然语言文本，需要转化为机器可处理的规范格式。但 Intake 只负责"理解任务是什么"，不负责"找到代码在哪里"——搜索相关的工作全部交给下游 Rewriter 模块，确保职责单一。

### CalibrationJudge（标准校准）

**做了什么**：独立裁判模块，各阶段（research/planning/coding）可调用 calibrate() 检查 Intake 生成的标准是否需要调整。生成 calibration diff 写入 task_spec.calibration，下游阶段读取校准后的标准。

**为什么这么做**：Intake 的初步分析可能不准确。Research 阶段收集到证据后，可能发现 task_type 判断错误（比如 Intake 说是 feature，但证据显示是 bugfix）。CalibrationJudge 作为独立模块，不修改 Intake 输出，而是生成 diff 供下游读取，保持数据流的单向性。



### Rewriter（查询改写）

**做了什么**：将 Issue 文本改写为多条独立搜索查询，每条查询带 SearchKind 分类和 key_paths 提示。双轨实现：LLM Rewriter 调用模型生成结构化 QueryRewritePlan；规则版 Rewriter 以正则表达式检测作为降级方案。

**为什么这么做**：单一搜索查询往往不够精准。通过生成多条不同角度的查询（精确标识符、文件路径、错误消息、符号名等），提高召回覆盖率。双轨设计确保 LLM 失败时系统仍能工作。

**SearchKind 18 种**：exact / path / symbol / error / history / general / explore / definition / test / config / dependency / regex / schema / performance / security / api / ui / ci_cd

### Research（证据收集）

**做了什么**：对 Rewriter 生成的每条查询执行搜索，每个查询携带 kind 和 key_paths。搜索时通过 ToolCall 传递 kind 到搜索链路。搜索后进行 Localizer 局部化循环，精确定位需要修改的代码行。

**为什么这么做**：搜索不是盲目的——每条查询都知道自己要找什么（kind 决定搜索策略，key_paths 缩小搜索范围）。主搜 + 副搜并行执行，通过 RRF 融合（rank_constant=60）排序结果。方案 C 重试机制确保搜索结果不足时自动回退到 GENERAL 策略，最多 3 次。

**供给侧 QueryKind 6 种**：

| QueryKind | 实现 | 说明 |
|---|---|---|
| LEXICAL | LocalLexicalSearch | 精确子串匹配（ripgrep） |
| BM25 | BM25Search | 通用全文检索，基于词频+逆文档频率 |
| VECTOR | VectorSearch | 向量嵌入检索，语义相似度 |
| SYMBOL | SymbolSearch | AST 符号检索，类名/函数名匹配 |
| HISTORY | GitHistorySearch | Git 历史检索（blame/commit log） |
| OPENSEARCH | OpenSearchHybridAdapter | OpenSearch 混合检索适配器（可选） |

**SearchKind → 搜索策略映射表**：

| SearchKind | 主搜 | 副搜 | 适用场景 |
|---|---|---|---|
| exact | LEXICAL + BM25 | BM25 | 精确标识符 |
| path | LEXICAL + BM25 | BM25 | 文件路径 |
| symbol | SYMBOL + BM25 | BM25 + VECTOR | 符号/类名/函数名 |
| error | LEXICAL + BM25 | BM25 | 错误消息 |
| history | HISTORY + BM25 | BM25 | Git 历史 |
| general | BM25 + VECTOR + OPENSEARCH | BM25 + VECTOR | 通用 fallback |
| explore | VECTOR + BM25 | BM25 + VECTOR | 探索性搜索 |
| definition | SYMBOL + BM25 | BM25 + VECTOR | 定义查找 |
| test | LEXICAL + BM25 | BM25 + VECTOR | 测试相关 |
| config | LEXICAL + BM25 | BM25 | 配置相关 |
| dependency | LEXICAL + BM25 + SYMBOL | BM25 | 依赖/导入 |
| regex | LEXICAL + BM25 | BM25 | 正则模式 |
| schema | SYMBOL + BM25 + VECTOR | BM25 + VECTOR | 数据库 schema |
| performance | BM25 + VECTOR | BM25 + VECTOR | 性能优化 |
| security | LEXICAL + BM25 | BM25 | 安全漏洞 |
| api | SYMBOL + BM25 | BM25 + VECTOR | API 接口 |
| ui | LEXICAL + BM25 | BM25 | 前端 UI |
| ci_cd | LEXICAL + BM25 + HISTORY | BM25 | CI/CD 配置 |

### Planning（计划生成）

**做了什么**：基于 Research 收集的证据生成实现计划，包含步骤列表、涉及文件、验证方案。同时评估风险等级（low/medium/high/critical）。

**为什么这么做**：让 Agent 在动手之前先想清楚要做什么，而不是直接开始写代码。风险等级决定是否需要人工审批——高风险任务强制进入 approval 环节。

### Approval（人工审批）

**做了什么**：高风险任务进入人工审批环节。审批信封 ApprovalEnvelope 包含 plan_hash（SHA-256 防篡改摘要）、declared_files、allowed_tools、verification_plan。plan_hash 使用 canonical JSON + SHA-256 生成不可逆摘要。

**为什么这么做**：远程写入可能直接影响生产仓库。审批信封确保人在回路中确认计划后再执行，且 plan_hash 保证审批通过后计划不会被篡改——任何修改都会导致 hash 不匹配，触发拒绝。

### Coding（代码生成）

**做了什么**：根据计划生成 patch，使用精确文本替换（old_text → new_text），确保不修改未声明文件。

**为什么这么做**：精确文本替换比行号补丁更可靠，不会因为代码行变动而失效。声明的文件列表确保 Agent 不会修改计划外的文件，与最小权限原则一致。

### Verification（沙箱验证）

**做了什么**：在 Docker 沙箱中运行测试，验证 patch 的正确性。

**为什么这么做**：隔离执行防止恶意代码影响宿主环境。沙箱使用 digest-pinned 镜像确保不可变性、非 root 用户降低权限、只读根防止持久化修改、丢弃所有 capabilities 减少攻击面。

### Review（代码审查）

**做了什么**：LLM 审查生成的 patch，检查是否满足 acceptance_criteria。

**为什么这么做**：增加一层自动化质量保障，确保 patch 不引入新问题、满足原始需求。review 与 verification 互为补充——验证保证"不坏"，审查保证"做对"。

### Localizer（定位循环）

**做了什么**：Planner + Explorer 循环，最多 3 轮，支持 4 种动作（search / read / blame / finish）。

**为什么这么做**：搜索返回的代码片段可能不够精确。Localizer 通过多轮交互逐步缩小范围，从文件级定位到函数级再到行级，最终给出精确的待修改位置。

## 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 语言 | Python 3.12+ | pyproject.toml 要求 >=3.12 |
| 编排 | LangGraph（StateGraph） | interrupt + conditional edges |
| 模型接入 | openai SDK（Responses API） | structured() 统一入口，Pydantic schema 校验 |
| Web | FastAPI + uvicorn | API 服务 |
| 搜索 | 自研：BM25 / AST 符号 / 向量 / LEXICAL / History / OpenSearch | 18 种 SearchKind 映射，RRF 融合 |
| 存储 | SQLAlchemy + Postgres（可选）/ 内存 | artifacts / memory / queue |
| 沙箱 | Docker（digest-pinned 镜像、非 root、只读根） | 隔离执行 |
| 前端 | React + Vite 控制台 | web/ |
| 评测 | 自研 harness + UK AISI Inspect 桥接 | 双轨评测 |

## 安全设计

- **deny-by-default 工具授权**：工具默认拒绝，仅按阶段授予必要能力。Agent 无法越权调用工具。
- **审批信封**：远程写入绑定审批信封，plan_hash（SHA-256）防篡改。审批通过后任何计划变更都会导致 hash 不匹配。
- **递归脱敏**：Redactor 递归检测并替换密钥、token、密码、API key 等敏感信息，防止泄漏。
- **路径穿越防护**：检查所有文件路径是否在 workspace root 内，拒绝任何包含 `..` 或绝对路径穿越的尝试。
- **沙箱隔离**：Docker 隔离运行测试，digest-pinned 镜像确保不可变性、非 root 用户降低权限、只读根文件系统防止持久化修改、丢弃所有 capabilities 减少攻击面。

## 相关项目

- [AegisEvo](https://github.com/ETOLucy/AegisEvo) — Agent 配置基因组进化优化，配套 RepoAegis 使用。
- [UK AISI Inspect](https://github.com/UKGovernmentBEIS/inspect_ai) — 行业标准 Agent 评测框架，RepoAegis 提供桥接。
