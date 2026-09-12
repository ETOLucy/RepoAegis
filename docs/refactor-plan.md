# RepoAegis 改造计划（已被取代）

> **状态：路线部分已失效。** 本文是"在现有 9 节点流水线上做增量改造"的思路，已被 [`rebuild-plan.md`](rebuild-plan.md)（从零设计的完全重写计划）取代。
> 本文保留的价值在于：「一、现状评估」「二、说法核实」「十、代码质量待办」记录了对旧实现的真实核查，「十一、变更日志」记录了已完成工作的历史。这些作为历史记录仍然有效，但不要再按本文的优先级执行。

## 概述

**目的**：诊断当前系统与其对外描述之间的真实差距，给出有优先级的改造方案，供开发时参照。

**依据**：本文基于对源码和 `docs/evidence/` 评测数据的实际核查得出，不是空想设计；每一条结论都标注了对应的代码位置或数据来源。

**关联文档**：`docs/interview-prep-complete.md` 是本计划的前置素材（面试问答形式记录了对系统的逐项拷问）；`docs/` 下的架构文档（从 [`README.md`](README.md) 开始）描述系统当前实现的现状，是本计划改造的基线。

**最后更新**：2026-09-11。历史变更见文末「十一、变更日志」。

---

## 一、现状评估

### 1.1 评测数据可信度

`docs/evidence/` 目录当前为空。此前的 `swebench-holdout-v2.json`（`instance_id` 形如 `swebench__test-0`，占位数据而非真实 SWE-bench instance）与自称 placeholder 的 `l2-generation-summary-20260811.json` 已删除，断言这份数据的 `tests/unit/evaluation/test_public_swebench_evidence.py` 一并删除（`pytest tests/unit/evaluation` 确认无遗留引用，97 通过）。删除前的历史诊断保留供参考：8 个任务里 4 个直接生成失败，剩下 4 个里 3 个 resolved，`strict_resolved_rate = 0.375`。

**结论**：所有后续改进的证据都建立在评测数据之上，必须先换成真实数据，金标准数据集的仓库选型尚未确定（方案见「六、评测方法论」，优先级见「五、优先级」第 1 项）。

### 1.2 已具备、不需要重做的部分

LangGraph StateGraph 9 节点编排、审批信封（SHA-256 防篡改）、Docker 沙箱四层加固、`Redactor` 递归脱敏、乐观锁并发控制、`EvaluationHarness` + Bootstrap 显著性检验。这些模块的设计细节见 [`architecture.md`](architecture.md) 与 [`approval-and-safety.md`](approval-and-safety.md)。

## 二、说法核实：哪些描述与实现不符

| 说法 | 核实结论 | 证据 |
|---|---|---|
| "多 agent 协作，9 个 agent 各管一段" | ❌ 名不副实。只有一个 `AgentRuntime`（一个 model + 一个 gateway），`build_agent_nodes(runtime)` 把它传给全部 9 个节点函数共用 | `agents/nodes.py:41-63` |
| "memory 存储层" | ❌ 名不副实。`InMemoryTaskRepository` 只是任务状态的乐观锁 CRUD 仓库，无跨任务学习 | `storage/memory.py` |
| "18 种 SearchKind 精准检索" | ⚠️ 过度设计但未解决核心问题。检索机制很重，但（改造前）没有调用图/依赖图，本质是"加检索策略"而非"建代码结构关系" | 改造前 `grep call_graph/dependency_graph` 全仓库零匹配 |
| "Localizer 定位循环" | ✅ 真实存在但（改造前）工具集不完整。4 种动作 search/read/blame/finish，没有 goto_definition/find_references | `docs/interview-prep-complete.md` 1.1.5 |
| "Intake 结构化理解 Issue" | ⚠️ 信息源单一。只吃 issue title+body+number，不读 CI 日志/测试失败 stack trace | `domain/models.py` → `IssueSpec` |
| "推理范式比 ReAct 新" | ✅ 真实。Localizer 借鉴 LocAgent（图引导定位），StateGraph 的有界重试 + evidence-driven fallback 接近 Reflexion 式验证-重试 | `docs/interview-prep-complete.md` Q17-Q18 |

## 三、核心改造项

### 1. Issue 理解：复现优先（reproduce-first）—— 部分完成

**目标**：Research 之前先尝试跑现有测试拿到真实 stack trace，用 trace 里的文件/行号/函数名直接作为定位起点，而不是纯靠语义检索去猜。CalibrationJudge 规则版可直接用 trace 信号做校准。

**已实现**：
- `tools/agent_actions.py` 新增 `ReproductionAdapter`，复用已有的 `TaskVerifier`/`SandboxVerifier`（`sandbox/verifier.py`）——"复现"和"验证"机械上是同一个动作（跑测试），只是流水线上的位置不同，因此没有另写一套沙箱代码。
- `production_graph.py` 注册新工具 `run_repro`，与 `run_verification` 共用同一个 `verifier` 实例。
- `policies/permissions.py` 的 `_AGENT_PERMISSIONS["research"]` 加上 `SANDBOX_EXECUTE`。
- `agents/nodes.py` 的 `research()` 节点开头先调用 `_reproduce()` 跑现有测试；`_reproduction_hits()` 把失败测试的 `name`/`message`/`location`（来自 `sandbox/failure_parser.py`）转成 `score=1.0` 的 `SearchHit`，塞进 `initial_hits` 交给 Localizer；复现结果同时存入 `repo_profile["reproduction"]`。
- `agents/calibration.py` 的 `_rule_calibrate` 新增规则：看到 `source="reproduction"` 的证据（真实 stack trace）直接判定 `bugfix`，优先级高于原有的字符串模糊匹配规则。
- 工具调用失败（沙箱未配置、未检测到测试命令等）时 `_reproduce()` 返回 `None` 而不抛异常——复现优先是锦上添花，不能因为这一步失败拖垮整个 research 节点，语义检索仍是兜底路径。
- 测试：`test_nodes.py` / `test_calibration.py` / `test_agent_actions.py` 新增或更新用例，`tests/unit` 全量 382 passed（4 skipped，无 ripgrep 环境）。

**未实现**：仅当现有测试失败时才有 trace；如果仓库测试全绿（例如这是一个 feature 请求），"写最小复现脚本"（让模型现写一个脚本来复现）尚未实现——需要让模型生成脚本并在沙箱内执行任意生成代码，安全和工作量都明显更大，列为后续可选项。

### 2. 依赖感知的代码导航：调用图 + import 图 —— 已完成

**目标**：完整 Code Property Graph（含数据流分析）工作量过大，收窄为调用图 + import 图（tree-sitter 提取，跳过数据流），覆盖"谁调用了这个函数""改这个文件影响哪些下游"两个最高频需求。

**已实现**：
- `search/codegraph.py`：`build_repo_graph(root)` 用 `tree-sitter-python` 解析出 `RepoGraph`（definitions / references / imports 三张表：函数/类/方法定义 + 调用点 + import 边），`definitions_for` / `references_for` / `importers_of` 做精确名 + 叶子名兜底查找。
- `search/adapters/graph.py`：`GraphSearch`（`SearchPort` 实现，接入 `QueryKind.GRAPH`）+ 共享的 `make_hit` / `read_snippet` 辅助函数。
- `tools/agent_actions.py`：`GraphAdapter` 把 `goto_definition` / `find_references` 两个工具调用接到同一个 `RepoGraph`（与 `WorkspaceIndex` 共用同一份图，不重复解析）。
- `agents/localizer.py`：`LocalizerAction.action` 新增 `goto_definition` / `find_references`，复用 `query` 字段传符号名（Localizer 动作从 4 种扩展到 6 种）。
- `search/kind_mapping.py`：`symbol` / `definition` / `dependency` 三种 SearchKind 的主搜升级为包含 `GRAPH`。
- 依赖：新增 `tree-sitter` / `tree-sitter-python`（有预编译 wheel，无需本地编译）——选择 tree-sitter 而非标准库 `ast`，因为同一套 walker 之后能直接扩展到其他语言。
- 测试：`test_codegraph.py` / `test_graph_search.py` / `test_agent_actions.py` / `test_localizer.py` / `test_kind_mapping.py` 新增 18 个用例，全量 446 passed（4 skipped，无 ripgrep 环境）。

**未做**：合并精简低频触发的 SearchKind（18 种里有些触发率很低，见「二」表格），本次只加了 GRAPH，精简留给后续。

**明确决定不做（2026-09-11）**：调用图/import 图目前仅解析 Python，其他语言（TypeScript/JavaScript/Java/Go/Rust）不支持，且**不打算现在补**——SWE-bench（含 Lite/Verified）的全部 12 个源仓库都是 Python 项目，与「六、评测方法论」里"最终在 SWE-bench Lite/Verified 上抽样验证"的目标完全对齐，扩展多语言对当前评测目标没有直接价值，属于给用不上的场景提前铺路。若以后评测/生产目标扩展到非 Python 仓库，再重新评估；届时建议按文件扩展名逐文件选择解析器（`.py`→Python、`.ts/.tsx`→TypeScript……），而不是复用 `EnvironmentProfiler` 那套"猜整个仓库是什么语言"的启发式——那套是为了决定"跑什么测试命令"设计的，把它复用到调用图上会把"仓库整体语言判断错误"和"调用图为空"两种问题混在一起，且天然无法处理多语言混合仓库。

### 3. 多 Agent 编排：先做消融实验 —— 待开始

拆成 Locator/Coder/Critic 会引入"传话丢信息"和延迟成本，不一定优于"一个更长上下文的单 agent"。先在同一批任务上跑单 agent vs 拆分角色两个版本，比较 resolved rate/成本/延迟，用数据决定要不要拆。若数据支持拆分：Locator（只有代码导航工具，输出结构化定位假设）→ Coder（只接收定位结果，产出 patch）→ Critic（判断退回 Locator 还是 Coder），子 agent 间用结构化消息通信。

### 4. 记忆层：case-based 案例库 —— 待开始

每次修复成功后存一条"bug 模式 + 定位路径 + 修复模式"，下次遇到相似模式优先复用。必须按 `tenant_id` 隔离——项目到处是租户隔离设计，这层不能破坏这个边界。现有 `InMemoryTaskRepository` 保留（职责是任务状态持久化，不改名不改动）。

### 5. Hooks / 事件系统：可观测性与人工干预的统一入口 —— 待开始

**动机**：当前系统里"发生了什么"完全不可查——全 `src/` 没有一处使用 `logging` 模块（见「十、代码质量待办」第 5 项），"真 bug"和"正常降级"在日志里长得一模一样；9 个节点、每一次工具调用、每一次审批的请求与决定，都没有统一的可观测出口，调试和事后审计只能靠临时加 print。分散地在 `nodes.py`/`query_rewriter.py`/`calibration.py` 等文件里各自补 `logging.getLogger(__name__)` 只是把同一个问题在多处重复解决——应该先有一个统一的事件模型，再把日志、追踪、告警、前端进度展示都接到它上面。

**核心思路**（参考[《Hooks 与事件系统》](https://waylandz.com/ai-agent-book/%E7%AC%AC06%E7%AB%A0-Hooks%E4%B8%8E%E4%BA%8B%E4%BB%B6%E7%B3%BB%E7%BB%9F/)一章的设计，按 RepoAegis 已有技术栈落地——不引入原文里 Temporal Signal / Redis Streams 这类新基础设施，能复用的已有机制都不重建）：

- **事件模型**：新增 `AgentEvent`（Strict Pydantic 模型，风格与 `domain/models.py` 一致），至少含 `task_id` / `tenant_id` / `event_type` / `payload` / `timestamp`。事件类型分三类：
  1. 节点生命周期——`NODE_STARTED` / `NODE_COMPLETED` / `NODE_FAILED`（覆盖 9 个节点 + `failure` / `finalize`）。
  2. 工具调用生命周期——`TOOL_INVOKED` / `TOOL_RESULT`，直接复用已有的 `ToolCall` / `ToolResult` 字段，不重新发明。
  3. 控制类事件——`APPROVAL_REQUESTED` / `APPROVAL_DECIDED`，以及 `iteration` 接近 `max_iterations`、`max_patch_attempts` 快用尽时的预算告警。
- **发射点收敛到两个已有的收口**，不散落进业务逻辑：
  1. `graph/builder.py::_graph_action()`——这里已经是全部 9 个节点回调的唯一适配点，包一层薄装饰，进出各发一个事件即可。
  2. `policies/permissions.py::PermissionPolicy.authorize()`——这里已经是全部工具调用的唯一强制检查点，前后各发一个事件，不需要在每个 Agent 节点里手动插桩。
- **脱敏复用现有实现**：事件 payload 落盘/外发前统一过一遍 `policies/redaction.py::Redactor`，不新写一套敏感信息处理逻辑。
- **传输与持久化**：进程内 `asyncio.Queue` 承载实时订阅，配合 FastAPI 的 SSE/WebSocket 端点给 `web/` 前端做实时进度展示，不需要 Redis Streams；高价值事件（节点终态、工具调用、审批请求/决定、验证结果、复审决定、错误）额外写入 `storage/` 的新表做持久化审计，高频低价值事件（未来若接入 LLM 流式 token 输出）不落库。发射用非阻塞方式（队列满即丢），不能让埋点拖慢主流程。
- **不重做审批机制**：现有的 `interrupt()` 审批中断已经能"暂停等待人工输入并从 Checkpoint 恢复"，Hooks 只是在这个既有机制前后各挂一个事件（`APPROVAL_REQUESTED` / `APPROVAL_DECIDED`），不需要引入 Temporal Signal 那一套——LangGraph 的 checkpointer 已经解决了暂停/恢复的持久化问题。

**能直接复用这套基建的下游需求**：
- 「十、代码质量待办」第 5 项（引入 `logging`）——有了统一事件模型后，`logging` 只是这套事件的一个消费者(把事件格式化成日志行)，不用继续在 5 个文件里分别写降级日志。
- 「四、4 前端"改造前后对比"视图」——这个视图需要的正是"两次定位过程"的结构化事件流，有了 Hooks 才有数据源，不用另外埋点。

**暂不做**：跨进程事件总线（Redis/Kafka）、Temporal 风格的工作流信号、LLM 级别的流式 token 事件——这些是规模化到多租户并发生产环境时才需要的复杂度，当前单进程 + LangGraph checkpointer 的组合还没到这个瓶颈。

## 四、其他改进机会（待评估）

1. **Verification 节点**：补充回归检测，复用「改造项 2」的调用图做 test impact analysis——patch 改了哪些函数，调用图告诉你哪些测试间接依赖，额外跑一遍。
2. **Planning 的 `deterministic_risk`**：用调用图堵上已知盲区——"改 `src/auth.py` 不触发规则但改了密码验证逻辑"，升级为"改动函数是否能从认证/安全相关文件 N 跳可达"。
3. **Review 节点前置静态检查**：LLM review 之前先跑 linter/type checker 做便宜的确定性预筛，复用 Planning 节点"确定性规则 + LLM 判断取更严格值"的既有设计哲学。
4. **前端加"改造前后对比"视图**：挑一个旧版本处理错、新版本处理对的真实 case，两次定位过程并排可视化。

## 五、优先级

1. **换真实评测数据**（见「一、1.1」「六」）——最先做，否则后面改进的证据都立不住。
2. **依赖感知代码导航**（三、2，已完成）+ **Planning 风险判定升级**（四、2）——一套基建两处复用。
3. **Hooks / 事件系统**（三、5）——越早做越省事：「十、代码质量待办」第 5 项的日志缺口、「四、4」的前端对比视图都直接依赖它，晚做会导致这两处各自先造一遍临时方案再返工。
4. **复现优先**（三、1，部分完成）。
5. **多 Agent 编排消融实验**（三、3）——先只做单/双两版对比。
6. **记忆层**（三、4）、**Verification 回归检测**（四、1）、**Review 前置检查**（四、3）、**前端对比视图**（四、4，依赖第 3 项）作为时间富余时的加分项。

## 六、评测方法论：跑真实 SWE-bench 之前

直接冲官方 SWE-bench 的问题：慢（每任务要起 Docker 环境）、贵、样本量大跑不起，且只看最终 resolved/not 看不出问题出在搜索还是编码环节。

**分层指标**（每个任务同时记录）：

1. **定位准确率**：agent 定位的文件/函数与真实修复 commit 改动范围是否重合，不需要跑测试/生成 patch，最快最便宜。
2. **生成成功率**：有没有产出语法合法、能应用的 patch。
3. **验证通过率（resolved rate）**：最终指标，单独看诊断不出问题在哪一层。

**四步流程**：

1. 组件级单元测试（调用图构建、reproduce-first），用手写的小型合成仓库测试，不涉及 LLM。
2. 自建真实但小的金标准集：从 1-2 个熟悉的开源仓库挑 10-15 个真实历史 bug（有修复 commit + 测试），替换 `docs/evidence/` 里此前的占位数据。
3. 在金标准集上做消融对比（改造前 vs 改造后），三层指标都记录，用 `significance.py` 的 `paired_bootstrap_delta` 做显著性检验。
4. 确认小规模有效后，从 SWE-bench Lite/Verified 抽样 50-100 个真实 instance 做规模化验证——这是流程的最后一步，不是第一步。

## 七、风险与应对：如果修复不了真实仓库

真实公开数据参照：Devin 在 SWE-bench 约 14% resolved，早期 SWE-agent 类系统普遍 10-20%，顶级模型 + 精心 scaffolding 也就 50-70%（大团队长期打磨）。个人在有限时间内不需要、也不太可能做到"稳定修复真实仓库"。

对冲手段：

- **分层指标兜底**——就算最终 resolved rate 没提升，定位准确率提升本身就是独立可交付成果。
- **合成 bug 数据集保底**——自己往代码里注入难度可控的 bug，保证能看到"从全错到部分修对"的进步曲线，与真实历史 bug 分层汇报。
- **提前缩小范围**——只处理单文件、有明确失败测试、错误类型明确（逻辑/边界条件）的 bug，不追求任意 issue 的通用修复。
- **结果不理想时的汇报框架**：假设 → 怎么验证的（分层指标 + 显著性检验）→ 结果和下一步，而不是回避结果。

## 八、明确排除的范围

- 完整重写 18 种 SearchKind 体系——精简 + 接入调用图即可。
- 完整 Code Property Graph（含数据流分析）——收窄为调用图 + import 图。
- AegisEvo（Rust 进化优化引擎）——配套项目，不纳入本次改造。
- Inspect Bridge 完整接线——`inspect/README.md` 已说明是骨架状态。
- 完整强化学习训练管线。

## 九、文档维护方式

每完成一项改造，在「三、核心改造项」或「四、其他改进机会」对应小节更新状态标记，并在「十一、变更日志」追加一条记录，包含日期、改动内容和影响到的文件/测试数。任务运行时状态查看 `output/` 目录，不属于本文档范围。

## 十、代码质量待办

自动化 code-smell 扫描发现（另一轮扫描已确认全仓库无死代码、无孤儿脚本），按发现时的影响排序：

| # | 问题 | 状态 |
|---|---|---|
| 1 | `agents/nodes.py` 的 `coding()` 曾是约 300 行的巨函数（上下文收集循环 + patch 生成重试循环 + 未声明路径治理检查 + 产物写入 + 内联校准，全挤在一个闭包里） | ✅ 已修复——拆分为 `_gather_context()` / `_propose_and_apply_patch()` |
| 2 | `domain/ports.py` 的 `ModelPort.structured()` 协议签名过时：真实实现和所有调用方都传了 `max_attempts`，port 定义里没有 | 待处理——补上 `max_attempts: int = 3` |
| 3 | `agents/nodes.py` 里 `ToolCall(task_id=..., tenant_id=..., repo_id=..., commit_sha=...)` 这段样板在 research/coding/review/pr 里重复了 14 次 | ✅ 已修复——抽成 `_tool_call()` 工厂函数 |
| 4 | `agents/nodes.py` 的 `CalibrationJudge` 调用块（本地 import + 实例化 + calibrate + 合并进 task_spec）在 research/planning/coding 三处逐字复制 | ✅ 已修复——抽成 `_apply_calibration(runtime, task, stage)` |
| 5 | 全 `src/` 没有一处用 `logging` 模块；`worker.py` 生产失败路径用 `traceback.print_exc()` 打到 stdout，没有结构化记录和 tenant/task 关联；`query_rewriter.py` / `localizer.py` / `calibration.py` / `search/reranker.py` / `search/adapters/opensearch.py` 里"降级为规则版"的 `except Exception` 分支也都没记录真正捕获到的异常，导致"真 bug"和"正常降级"在生产日志里长得一模一样 | 待处理——引入 `logging.getLogger(__name__)`，降级前先 `warning` 记录原始异常 |
| 6 | `evaluation/swebench_runner.py` 的 evidence-driven fallback 逻辑手写重复了一遍 `graph/routes.py` 的 `route_after_review`，两份实现已经出现分歧（SWE-bench 版跳过了 `verification.passed` 检查）——可能是有意为之（官方 harness 本身就是 verifier），但重复的业务规则是漂移风险 | 待复核——考虑抽到 `policies/risk.py` 共用 |
| 7 | `cli.py` 的 `_decimal` / `_nonnegative_decimal` 应合并成一个带 `allow_zero` 参数的函数 | 待处理（次要） |
| 8 | `storage/sql.py`（471 行）一个文件装了 task repo / queue / operation log / task completion 四类职责，各个类本身还算内聚 | 待评估——值不值得拆看这个文件后续还长不长 |

## 十一、变更日志

- **2026-09-11**：从对源码和评测数据的实际核查得出本计划初稿，识别出「二、说法核实」中与实现不符的描述。
- **2026-09-11**：删除评测数据中的占位 SWE-bench 数据（`swebench-holdout-v2.json`、`l2-generation-summary-20260811.json`）及依赖它的断言测试 `test_public_swebench_evidence.py`，`docs/evidence/` 清空，等待「六、评测方法论」的金标准集建好后回填。仓库选型待定。
- **2026-09-11**：发现并修复 Localizer 权限缺口——`agents/localizer.py` 以 `agent="localizer"` 发起 `ToolCall`，但 `policies/permissions.py` 的 `_AGENT_PERMISSIONS` 未登记该 agent 名，导致真实运行中 Localizer 一旦执行 search/read/blame 等非 finish 动作就被 `AuthorizationDenied` 拒绝、research 节点直接失败；原有测试因使用假 Gateway 桩从未发现。已修复（commit `08b46c1`），补充权限单测，顺带注册此前遗漏的 `git_blame` 工具。
- **2026-09-11**：完成「三、2 依赖感知的代码导航」——新增调用图/import 图（tree-sitter）、`GraphSearch`、Localizer 的 `goto_definition`/`find_references` 两个新动作、`GRAPH` QueryKind；新增/更新测试 18 个，全量 446 passed（4 skipped）。
- **2026-09-11**：完成「三、1 复现优先」的部分实现——`ReproductionAdapter` 复用现有验证器，research 节点开头跑现有测试拿真实 trace 喂给 Localizer，`CalibrationJudge` 规则版新增识别 reproduction 证据的规则；写最小复现脚本（无失败测试时）未实现，留作后续。全量 382 passed（4 skipped）。
- **2026-09-11**：完成一轮自动化 code-smell 扫描（见「十、代码质量待办」），并修复其中第 1、3、4 项：`coding()` 巨函数拆分、`ToolCall` 样板抽成 `_tool_call` 工厂、`CalibrationJudge` 重复调用抽成 `_apply_calibration`。
- **2026-09-11**：排查 `tree-sitter` 依赖版本问题——`0.26.0` 在 Windows 上解析真实大小文件时崩溃（native access violation），本仓库自身的 `agents/nodes.py` 即可复现；经二分确认 `0.25.2` 无此问题，`pyproject.toml` 锁定为 `tree-sitter>=0.25,<0.26`。
- **2026-09-11**：讨论调用图的多语言支持，确认 SWE-bench（含 Lite/Verified）全部 12 个源仓库都是 Python，与本项目评测目标完全对齐，决定暂不扩展调用图到其他语言（用户决策：先只做 Python，当前目标就是 SWE-bench 仓库）。仅补充文档说明，无代码改动。
- **2026-09-11**：新增「三、5 Hooks / 事件系统」改造项——统一节点生命周期、工具调用、审批请求/决定的可观测出口，回应现有系统完全没有结构化日志/追踪的问题（呼应「十、代码质量待办」第 5 项）；设计参考[《Hooks 与事件系统》](https://waylandz.com/ai-agent-book/%E7%AC%AC06%E7%AB%A0-Hooks%E4%B8%8E%E4%BA%8B%E4%BB%B6%E7%B3%BB%E7%BB%9F/)一章，但落地方式改为复用 RepoAegis 已有机制（LangGraph checkpointer、`PermissionPolicy.authorize()` 单一收口、`Redactor`），不引入 Temporal/Redis Streams。
