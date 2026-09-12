# RepoAegis 重写计划（从零设计版）

**日期**：2026-09-12
**性质**：本文件是当前唯一有效的重构执行计划，**取代** [`refactor-plan.md`](refactor-plan.md) 的路线部分（该文档保留作为"增量改造思路"的历史记录，不再执行）。
**依据**：[`industry-survey.md`](industry-survey.md) 的调研结论与逐环节选型。

## 〇、这份计划和上一版的根本区别

`refactor-plan.md` 的出发点是"现有代码里哪些能留、哪些该改"——一种增量迁移思路。这次明确否决了这个出发点：**先按业界标准设计目标系统该长什么样，再决定旧代码里有没有东西配得上留下来**，而不是反过来。旧代码不享有"已经存在"这个默认优势；一段代码只有在独立评估下仍然是业界最佳实践时才被保留，其余的（哪怕能跑、哪怕刚测试通过）直接推翻重写。

这不代表推翻技术栈本身。Python + FastAPI + LangGraph 的后端、React + Vite 的前端，是既定选择，也是 [`industry-survey.md`](industry-survey.md) 里对"个人开发者 2026 年该选什么"给出的独立推荐结果——继续用它们不是"沿用旧代码"，是重新做了一遍选择、恰好选中同一个栈。

**宗旨**（贯穿整份计划）：这是个人项目，目的之一是完整体现三块能力——**agent 开发功底**（解题循环、工具设计、上下文管理、验证与选择、评测）、**后端开发功底**（FastAPI、队列、持久化、事件系统、多租户、成本记账）、**轻量前端功底**（React + Vite 控制台，类型安全、实时事件流、diff 查看、审批交互）。每个阶段的设计都要能明确指出它服务于哪一块能力，避免只在其中一块上使劲。

---

## 一、目标系统总览

```
GitHub Issue
  → Intake（结构化任务）
  → Plan（风险判定 + 审批信封）
  → [人工审批]
  → Solve ←── 这里是全新的 agentic 单循环，取代原来的 Localizer + Coding 两段式
  → Verify（harness 侧独立验证，不信任模型自报）
  → Review（静态门 + LLM 复审）
  → PR
```

外层仍是 LangGraph 状态机（治理、审批、可审计性的载体），内层 Solve 节点内部是一个类 mini-SWE-agent 的通用循环。这是 [`industry-survey.md`](industry-survey.md) 〇、3 的结论，本文把它落成可执行计划。

---

## 二、三条能力线的具体设计

### 2.1 Agent 开发线

**新模块 `agents/solver/`（全新目录，取代 `agents/localizer.py` + `nodes.py` 里的 `_gather_context`/`_propose_and_apply_patch`）**

| 文件 | 职责 |
|---|---|
| `loop.py` | `SolverAgent`：ReAct 主循环，原生 tool-calling（Responses API），双预算（步数上限 + token/成本上限），复现优先系统提示 |
| `tools.py` | 工具注册表：`bash`（长生命周期容器内执行）、`read`（带行窗口）、`edit`（精确 old/new 字符串替换，失败立即返回"未找到/多处匹配"反馈）、`grep`（ripgrep）、`glob`、`goto_definition`/`find_references`（接现有调用图）、`finish` |
| `context.py` | 三级上下文管理：工具输出截断 → 旧观察折叠为一行摘要 → 阈值触发的 LLM 摘要压缩 |
| `repo_map.py` | 开场上下文：tree-sitter 符号 + PageRank（Aider 思路），预算约 1k token |
| `patch_selection.py` | best-of-N 执行式选择器：复现测试是否由失败变通过 → 调用图驱动的回归测试精选是否全过 → 规范化 diff 多数投票 → LLM 只做平局裁决；N 默认 1，可配置 |

每个工具的 description 按 Anthropic《Writing tools for agents》的原则写：明确输出长度上限、失败时给出可操作的下一步提示（"没找到，试试更窄的正则"这类），不是简单地把参数列出来。

**体现的能力**：ReAct/CodeAct 范式的实际工程化、工具接口设计（Agent-Computer Interface）、上下文工程（compaction/折叠）、test-time compute（best-of-N + 执行式验证）——这些都是 `industry-survey.md` 二、1-2-3-4-6 节列出的、有实证支持的技术，不是随手拍的架构。

**明确不做**：Locator/Coder/Critic 角色拆分（业界已经证明这条路对强模型没有增益，见 `industry-survey.md` 二、8）；`inspect/` 骨架要么在这次接通、要么删除，不允许继续挂空壳。

### 2.2 后端开发线

**新模块 `events/`（全新）**

- `AgentEvent`（Strict Pydantic），字段对齐 OTel GenAI 语义约定（`gen_ai.operation.name`、`gen_ai.usage.input_tokens` 等），事件类型覆盖节点生命周期、工具调用、审批请求/决定、Solve 循环每一步。
- 两个发射点：`graph/builder.py::_graph_action()`、`policies/permissions.py::authorize()`（沿用现有单一收口设计，重写实现）；Solve 循环内部每一步也发一个事件。
- 三个消费者：`structlog` JSON 日志、`storage` 里新增的 `events` 表（持久化审计）、进程内 `asyncio.Queue` → FastAPI SSE 端点 `GET /v1/tasks/{id}/events`（支持 `Last-Event-ID` 断线续传）。

**新模块 `runtime/`（全新，取代 `sandbox/docker.py` 里"每条命令起一个容器"的调用方式）**

- `Runtime` 协议：`run(cmd, timeout) -> (exit_code, stdout, stderr)`。
- `DockerRuntime`：长生命周期容器，`docker exec` 逐条命令，无状态 shell（mini-SWE-agent 的做法）；沿用现有加固参数（digest 锁定镜像、非 root、只读根、cap-drop、seccomp、`--network none` + 允许列表代理）——这套加固本身独立成立，重写实现时原样保留。
- `Solve` 节点的 bash 工具和 `Verify` 节点的验证器共用同一个 `Runtime` 实例，单任务只起一次容器。

**`graph/`（重写，不是修补）**

- `build_graph()` 编译时接 `AsyncPostgresSaver`（或开发期 `SqliteSaver`），approval 节点的 `interrupt()` 真正挂起；`/approve` 端点改为 `Command(resume=decision)` 续跑，而不是现在这种"改状态 + 队列重入"的绕行方式。这是本次重写要修正的一个具体历史债务（见 `industry-survey.md` 2.10 节对现状的核对）。

**`storage/`（在现有 `SqlTaskQueue`/`SKIP LOCKED` 租约基础上扩表，不是推翻）**

- 新增 `events`、`costs`、`tool_calls` 表；写操作（push、PR）加幂等键 `task_id + step + args_hash`；租户级 token 桶。
- `SqlTaskQueue` 的 `FOR UPDATE SKIP LOCKED` 租约设计本身就是 2026 年推荐做法（见 `industry-survey.md` 2.10 引用的 Hatchet 文章），独立保留。

**体现的能力**：事件驱动架构、durable execution 与 checkpoint 的正确使用、队列与并发控制、成本与多租户记账、幂等性设计——这些是后端工程里具体可考核的点，不是"用了 FastAPI"这种泛泛而谈。

### 2.3 轻量前端线

- React + Vite 升级到 TypeScript；用 `openapi-typescript` 从 FastAPI 的 OpenAPI schema 生成类型，前后端契约共享——这是这条线最值得展示的具体技术点。
- 数据层：TanStack Query 做任务列表/详情，原生 `EventSource` 订阅 SSE 事件流，不引入额外流媒体库。
- 四个视图：
  1. 任务列表/详情（现有 `TasksView`/`PipelineView` 清理重写）。
  2. **运行时间线**：把 `AgentEvent` 流渲染成可折叠的 step/tool-call 树，替代现在的 5 秒轮询。
  3. **Diff 查看器**：`@git-diff-view/react`，渲染后端已经生成好的 unified diff。
  4. **审批弹窗**：展示 plan hash、declared files、allowed tools、风险原因，批准即调 `/approve`。
- 不引组件库，延续现有手写 CSS 体系；不做终端面板（需要 WebSocket + xterm.js，投入产出比不够）。

**体现的能力**：类型安全的前后端契约、实时数据流消费、diff/审批这类"agent 产品"特有的交互设计——这些具体交互点比"用了 React"更能说明前端功底。

---

## 三、全部推翻的清单

以下模块整个删除，不做迁移，替换为上面的新模块：

- `search/rewriter.py`、`search/kind_mapping.py`（18 种 SearchKind）、`search/router.py`（`SearchRouter`）、`search/service.py`（`HybridSearchService` 主副搜 + RRF 融合）——替换为 Solver 直接从工具集里选检索方式。
- `agents/localizer.py`（6 动作、3 轮预算的定位循环）——并入 Solver 通用循环，定位不再是独立概念，只是循环的前几步。
- `agents/nodes.py` 里 `_gather_context()`/`_propose_and_apply_patch()` 的整批 search/replace 流程——替换为 Solver 逐步调用 `edit` 工具、每次拿即时反馈。
- `agents/calibration.py` 的 LLM 版 `CalibrationJudge`——如果保留，只保留规则版做 `task_type` 兜底校验，不再单独发起 LLM 调用。
- `inspect/` 骨架——本次接通或删除，二选一，不允许继续空挂。

## 四、独立重建的清单（重写实现，但设计意图不变，因为它们各自经得起评估）

- `domain/models.py` 的 `ApprovalEnvelope.digest()` + `PermissionPolicy.authorize()` 每次写操作重算哈希——这正是 2026 年业界正在收敛的"审批绑定参数哈希的签名信封"模式（见 `industry-survey.md` 2.12），重写进新的 `authorize()` 收口。
- `sandbox/docker.py` 的加固参数集合——独立成立，原样携带进新的 `DockerRuntime`。
- `search/codegraph.py` 的 `RepoGraph`（tree-sitter 调用图/import 图）——LocAgent、Codebase-Memory 等论文独立支持这类结构，重写/整理后作为 `goto_definition`/`find_references` 工具和 repo map 的数据源。
- `storage/sql.py` 的 `SqlTaskQueue`（`SKIP LOCKED` 租约）——独立成立，保留设计，代码可视重写规模决定是否原样搬迁。
- `sandbox/verifier.py` + `sandbox/failure_parser.py` 的 `ErrorKind` 分类——与 SWE-bench 的 FAIL_TO_PASS/PASS_TO_PASS 语义对齐，重写进新的 Verify 节点。

## 五、明确排除的范围（沿用 `refactor-plan.md` 八的判断，仍然成立）

- 完整 Code Property Graph（含数据流分析）——仍然只做调用图 + import 图。
- 调用图的多语言支持——SWE-bench 全部源仓库是 Python，仍不做。
- AegisEvo（配套项目）、完整强化学习训练管线——不纳入本次重写。

---

## 六、执行阶段（每阶段独立可展示）

| 阶段 | 内容 | 主要体现的能力 | 完成标志 |
|---|---|---|---|
| P0 | 评测地基：金标准集（10–15 个真实历史 bug）+ SWE-bench Lite 抽样 + 同模型跑 mini-SWE-agent 对照 + 分层指标/成本记录 | agent（评测方法论） | 一条命令跑出对比报告 |
| P1 | 后端骨架：`events/`、`runtime/`、重写 `graph/builder.py`（真正的 checkpointer）、`storage/` 扩表 | 后端 | 能起一个任务、发出事件、走到审批中断并真正挂起 |
| P2 | Agent 核心：`agents/solver/` 全部模块，接入 `Runtime`，替换 Solve 节点 | agent | 同一批 P0 金标准集上，新 Solver 的分层指标可与 mini-SWE-agent 对照 |
| P3 | 验证与选择：Verify 节点接调用图回归精选，`patch_selection.py` 的 best-of-N | agent + 后端 | 有"精选前后节省的沙箱秒数"和"N=1 vs N=3 的增益/成本曲线"两组数据 |
| P4 | 前端：TS 化、OpenAPI 生成类型、时间线/diff/审批三个视图接 SSE | 前端 | 浏览器里能看完一次运行的完整时间线并完成审批 |
| P5 | 记忆：读取目标仓库 `AGENTS.md`/`CLAUDE.md`；`(tenant, repo)` 运行笔记表 | agent + 后端 | 有无笔记的对照数据 |

每个阶段结束都有一个能独立讲清楚的成果，不依赖后续阶段才能验证价值。旧模块在对应新模块落地的同一个阶段里删除，不保留"以防万一"的旧代码分支。

---

## 七、变更日志

- **2026-09-12**：创建本文件，取代 `refactor-plan.md` 的路线部分。原因：用户明确要求完全重构，甚至接受把之前的代码全部推翻的激进方式，而不是增量迁移。
