# 业界 Coding Agent 全流程调研与 RepoAegis 重构选型

**日期**：2026-09-12
**目的**：为 RepoAegis 的整体重构提供决策依据——业界主流方案长什么样、哪些论文和数据支撑了这些选择、RepoAegis 每一环该怎么选、为什么。
**宗旨**：RepoAegis 是个人项目，目标是在一个项目里同时体现 Agent 开发、后端开发和轻量前端三方面能力。所有选型都按"单人能维护、可被评测证明、能对外讲清楚"来取舍。
**方法**：五路并行调研（框架架构、学术论文、工程基建、检索与验证、经典 scaffold 深挖），全部对照一手来源（GitHub / arXiv / 官方博客 / swebench.com 与 tbench.ai 页面数据）；同时对 RepoAegis 源码做了核对。标 **[未核实]** 的条目只有二手来源。

**关联文档**：[`refactor-plan.md`](refactor-plan.md)（现有改造计划，本文第五节逐项评估）、[`architecture.md`](architecture.md)（当前实现基线）。

---

## 〇、结论先行

1. **业界已收敛到"极简 harness"**：一个 ReAct 循环 + bash + 精确字符串替换编辑器 + grep 检索 + 自动压缩上下文 + OS 级沙箱 + `AGENTS.md` 记忆文件。SWE-bench Verified、SWE-bench Pro、Terminal-Bench 各榜单顶部全是这个形态（mini-SWE-agent、Claude Code、Codex CLI、live-SWE-agent）。分阶段流水线（Agentless、AutoCodeRover）在 2025 年初之后停更，只作为廉价基线和 test-time scaling 的外壳存活。
2. **RepoAegis 现在是 Agentless 时代的设计**：模型没有 bash，Localizer 只有 6 个动作、3 轮预算，Coding 阶段 1 轮上下文收集 + 一次性整批 search/replace，没有"改一点→跑一下→再改"的内循环。这是 resolved rate 的天花板所在，与检索策略多寡无关。
3. **重构主张：外层治理流水线保留，内层换成 agentic 循环。** 外层（Intake → Plan → 审批信封 → 沙箱执行 → 验证 → 复审 → PR）是 RepoAegis 真正的差异化，且与 Jules（plan → approve → execute → critic）、Copilot coding agent、Devin 这些"企业级封装"的形态一致；内层 Research + Coding + Verification 三个节点的内部实现，换成一个在沙箱里跑的、带 bash / edit / grep / graph 工具的有界循环（"治理在外，mini-SWE-agent 在内"）。
4. **证据最硬的三个增益杠杆**：(a) 生成复现测试 + 用执行结果选补丁（Agentless +6.3pp，Kimi-Dev 48→60.4%）；(b) 回归测试精选（TestPrune：Trae 65.0→70.2%，且更便宜）；(c) N 个候选 + 执行式选择器（CodeMonkeys 45.8→57.4%，Trae 75.2%）。检索侧换 embedding、加 reranker、拆多 agent 角色，都没有同等级证据。
5. **要删的东西比要加的多**：18 种 SearchKind + Rewriter + RRF 融合、LLM 版 CalibrationJudge、OpenSearch / embedding / reranker（未提交 diff 已在删）、"未完整实现"的 HISTORY 检索。删掉后模型直接选工具，这正是"agentic search"。
6. **三个核对出的代码事实**（会改变计划优先级）：生产图编译时没有 checkpointer，`interrupt()` 实际不挂起，审批靠 API 改状态 + 队列重入实现；前端 5 秒轮询、没有事件流；`observability/` 目录 85 行、基本是空壳。
7. **评测方法上加一条**：在同一批实例上用同一个模型跑一遍 mini-SWE-agent 作为对照。如果 RepoAegis 低于它，说明 scaffolding 在做减法；这比任何绝对数字都有说服力。

---

## 一、业界形态：2026 年收敛到了什么

| 维度 | 2024 年主流 | 2026 年收敛结果 | 代表 |
|---|---|---|---|
| 循环结构 | 分阶段流水线（定位→修复→验证） | 单个 ReAct 循环直到模型说完成；子 agent 只读、返回摘要 | mini-SWE-agent、Claude Code、Codex、OpenHands V1 |
| 工具集 | 十几个专用检索 API | bash + 1 个编辑器 + grep/glob + read；LSP / 代码图作为可选插件 | Anthropic SWE-bench scaffold（"一个 bash 工具和一个字符串替换编辑器"）、Auggie v2（"一个 bash 三个文件工具"） |
| 检索 | embedding RAG | grep 为主，embedding 退居 IDE 层，代码图用于依赖类问题 | Claude Code 明确放弃向量库；Cursor 是唯一有对照数据证明 embedding 有增益的（+12.5% 问答准确率、大仓库 +2.6% 代码保留率） |
| 编辑格式 | 整文件重写 / unified diff | 精确 old/new 字符串替换（str_replace）或 OpenAI V4A apply_patch | Diff-XYZ 论文：search/replace 0.96 vs udiff 0.90 |
| 上下文 | 截断 | 工具输出截断 + 旧观察折叠 + 阈值触发的 LLM 摘要压缩 | Claude Code、Codex、OpenHands condenser；Amp 反其道用 handoff |
| 验证 | 跑测试 | 复现脚本优先 + 回归测试精选 + 执行式补丁选择 | Agentless、Trae、CodeMonkeys |
| 沙箱 | Docker | 本地 CLI 用 Seatbelt / bubblewrap+seccomp；托管用 Firecracker / VM；研究用 SWE-ReX 抽象 | Codex、Claude Code、SWE-agent |
| 权限 / HITL | 全自动或全手动 | 三层：静态允许/拒绝规则 → 沙箱 → 分类器/人工；审批绑定到具体调用的参数哈希 | Claude Code 权限管线、Cursor Auto-review、Vercel AI SDK v6 tool-approval |
| 记忆 | 无 | 仓库级指令文件（AGENTS.md / CLAUDE.md）+ 自动学习笔记；跨任务案例记忆增益 +2–7pp 且依赖检索质量 | AGENTS.md 已由 Agentic AI Foundation 托管 |
| 多 agent | 角色拆分（Manager / Coder / QA） | "单写者"原则：并行只读探索，串行写入 | Cognition《Don't Build Multi-Agents》；2026 年各家都加了只读子 agent |

**榜单现状（供校准预期，不要用来对标）**：swebench.com 官方 Verified 榜 2026 年零提交，顶部 live-SWE-agent + Claude 4.5 Opus 79.2%（2025-12）；厂商自报已到 88–95%，OpenAI 已发文宣布不再评 Verified，Anthropic 对 Verified / Pro 做记忆化筛查。SWE-bench Pro 公榜（SWE-Agent scaffold）Sonnet 4.5 43.7%，厂商自家 harness 报 58–81%，20 个点的差距全是 harness 造成的。Terminal-Bench 4.0（2026-09 上线）顶部 Codex + GPT-6 Astra 58.2%、Claude Code + Fable 5.1 57.9%。结论：Verified 已饱和且有污染，个人项目用它做相对对比没问题，别当绝对指标讲。

---

## 二、逐环节：业界做法 → 证据 → RepoAegis 怎么选

每一环按"业界做法 / 关键证据 / RepoAegis 的选择 / 为什么"四段写。

### 2.1 Issue 理解与复现优先

- **业界做法**：Anthropic 的 SWE-bench scaffold 提示词是"探索仓库 → 写复现脚本并执行 → 修 → 重跑 → 想边界情况"；SWE-agent、OpenHands、Trae 提示词同构。Kimi-Dev 把它做成 BugFixer + TestWriter 自博弈。
- **证据**：Agentless 消融：多数投票 25.67% → 加回归测试 27.0% → 加复现测试 32.0%（Lite，最大单项增益）；但 300 条生成的复现测试只有 94 条在真实修复上也通过，issue 本身的模糊是上限。反例：TDAD 发现对 30B 小模型强推 TDD 流程反而让回归率从 6.08% 升到 9.94%。"复现优先"本身是共识但直接因果证据弱，**用复现结果选补丁**才是硬证据。
- **RepoAegis 选择**：保留现有 `ReproductionAdapter`（先跑现有测试拿 trace）；内层循环有 bash 后，让模型自己写复现脚本；把"复现测试是否从失败变通过"作为后续补丁选择的第一信号。`TaskSpecOutput` 结构化抽取保留但只做一次。
- **为什么**：这条线的代码已经写了一半，剩下一半（模型写脚本）只有在内层循环有 bash 的前提下才能安全落地，所以顺序是先做内层循环。

### 2.2 定位与检索

- **业界做法**：Claude Code / Cline / Codex 不建索引，靠 grep / glob / read；Cursor / Augment / Copilot 建 embedding 索引但是给 IDE 场景；Sourcegraph 反向卖精确代码智能（SCIP）而不是 embedding；Aider 用 tree-sitter + PageRank 的 repo map 做开场上下文；Serena / Claude Code LSP 插件把 go-to-definition / references 暴露成工具。
- **证据**：
  - Augment 自己的 SWE-bench 工程师承认"embedding 检索不是瓶颈，agent 反复换 grep 就能找到"。
  - CORE-Bench（2606.11864）：通用代码 embedding 模型在传统代码检索上 NDCG@10 71.7，到 issue→edit 定位任务掉到 20.3；只有在 PR 数据上做 SFT 才有用。这解释了为什么 Cursor 有效（它用 agent trace 训练了自己的 embedder）而通用 embedding 没用。
  - Codebase-Memory（2603.27277）：代码图 MCP 在 31 种语言上得分 0.83 vs grep 探索 0.92，但 token 用量 1k vs 10k、工具调用 2.3 vs 4.8。代码图省 token、不提准确率。
  - LocAgent（2503.09089）：图引导定位文件级 Acc@5 87.6%（BM25 61.7%）；SweRank（2505.07849）：检索 + 重排不用 agent，函数级 Acc@5 81.4%，成本 $0.01/实例。
  - Agentless 统计：约 50% 的 SWE-bench Lite issue 直接点名了要改的文件——定位经常不是瓶颈。
  - "Is Grep All You Need?"（2605.15184）：跨 Claude Code / Codex / Gemini CLI，grep 普遍胜过向量检索，但**工具输出的投递方式（内联 vs 写文件）造成的差异和检索器本身一样大**——harness 设计比检索算法重要。
- **RepoAegis 选择**：暴露给模型 5 个检索类工具：`grep`（ripgrep，支持正则和路径过滤）、`glob`、`read`（带行窗口）、`goto_definition` / `find_references`（现有 tree-sitter 调用图）。可选保留 BM25 作为一个 `search_code` 自然语言兜底工具。**删除** Rewriter、18 种 SearchKind、`kind_mapping`、`HybridSearchService` 的主副搜 + RRF、`SearchRouter`。开场上下文加一个 Aider 式 repo map（tree-sitter 符号 + PageRank，1k token 预算），`RepoGraph` 已经有 definitions / references 两张表，PageRank 是几十行的事。
- **为什么**：18 种 kind 是在替模型做"该用哪个检索器"的决定，而业界证据说模型自己选工具做得更好、也更透明。调用图保留是因为它便宜、是差异化点、且省 token 有数据支撑。不做 embedding 是因为没有训练数据就没有增益，还多一套基础设施。

### 2.3 上下文管理

- **业界做法**：工具输出截断（Claude Code 25k token；mini-SWE-agent 超过 1 万字符保留首尾各 5 千）；旧观察折叠（SWE-agent `LastNObservations`）；到窗口 50–80% 触发 LLM 摘要压缩（Codex、Gemini CLI、Kiro、OpenHands condenser）；Amp 认为压缩有损、改用 handoff；Anthropic 建议子 agent 隔离上下文、只回传摘要，并把笔记持久化到窗口外。
- **证据**：Anthropic 的"context rot"论述；CodeMonkeys 发现让模型通读全部文件并排相关性花 14.6% 预算但能让 92.6% 的实例把所需文件塞进 128k。缺少严格对照实验，属于工程共识。
- **RepoAegis 选择**：外层节点间传结构化状态，不需要压缩（现状保留）；内层循环用三级：输出截断 → 只保留最近 N 条完整观察、更早的折叠成一行 → 到阈值再做一次 LLM 摘要。系统提示 + repo map 放在稳定前缀里吃 prompt cache。
- **为什么**：前两级是确定性的、零成本、可测试；第三级引入 LLM 调用和信息丢失，只在评测证明有实例撑爆窗口时才加。

### 2.4 编辑原语

- **业界做法**：str_replace（Claude Code、OpenHands、Trae、Gemini CLI、Augment）或 V4A apply_patch（Codex、Cline、Kilo）；Aider 保留多种格式按模型切换；fast-apply 小模型（Morph / Relace）在退潮，Devin 因小模型误解指令放弃了它。
- **证据**：Diff-XYZ（2510.12487）：大模型上 search/replace 应用精确匹配率 0.96–0.97，udiff 0.90，冗长 udiff 崩到 0.01–0.08；Aider 历史数据：udiff 只是 GPT-4 Turbo 时代治"偷懒省略"的权宜之计；OpenAI 官方原则"不用行号，同时给出被替换的精确代码和替换后的精确代码"。
- **RepoAegis 选择**：现有 `PatchProposal`（search/replace 边 + 模糊定位回退 + 渲染成 unified diff）方向正确，**保留渲染器**给审批和 PR 出最终 diff；但把编辑改成循环内的单个 `edit(path, old, new)` 工具，每次立刻返回"未找到 / 多处匹配 / 用了模糊匹配"的反馈，编辑后自动跑 ruff / pyright 诊断塞回上下文（Claude Code LSP 诊断、SWE-agent linter guard 的做法）。
- **为什么**：整批提交没有中间反馈，一处 old_text 对不上整批作废；逐个编辑 + 即时反馈是所有顶级 harness 的共同点。

### 2.5 执行与验证

- **业界做法**：模型在沙箱里自己跑测试；harness 层做基线检查（改前就失败的测试不算 agent 的锅）；回归测试选择正在从"全跑"变成"精选"。
- **证据**：TestPrune（2510.18270）：LLM 定位可疑函数 → 取相关测试文件 → 覆盖率贪心最小化到约 9 个测试（对比全套 1000 倍），Trae 65.0→70.2% Verified，成本 $0.02–0.05/实例还省 8–23% agent 开销；TDAD：静态 AST + 命名启发式生成 `test_map.txt`，回归率 6.08%→1.82%；Trae 的 pruner 用回归测试过滤候选，假阴性 3.69%。AgentLens 提醒约 10.7% 的"通过"是靠运气（有回归循环、盲目重试）。
- **RepoAegis 选择**：保留 `SandboxVerifier` + `failure_parser` + `ErrorKind` 分类（这套和 SWE-bench 的 FAIL_TO_PASS / PASS_TO_PASS 语义对齐，是优点）；用现有调用图 + import 图实现"改动了哪些函数 → 哪些测试文件 import 了这些模块"的回归精选，这正是改造计划四、1 的内容，证据支持它优先级上调。
- **为什么**：一套调用图基建同时服务定位、风险判定和回归精选，投入产出比最高；精选还直接降低沙箱时长。

### 2.6 补丁选择与 test-time compute

- **业界做法**：Trae 的生成 → 剪枝 → 选择三段；CodeMonkeys 的串行迭代 + 并行采样 + 每个候选自带测试；OpenHands 用训练过的 critic 重排 5 个 rollout。
- **证据（Verified 或 Lite）**：Agentless 25.7→32.0（oracle 42.0）；CodeMonkeys 随机 45.8→57.4（oracle 69.8，选择开销 <10%）；Trae 单次 59–66% → 75.2；OpenHands + critic 60.6→66.4@N=5；纯 LLM 判官在强候选池上只加约 2pp（76.1→78.2，oracle 84.4）。学习型 critic > 提示词判官 > 无选择；执行信号 > 一切。Augment 明说集成多模型"对真实产品不现实"。
- **RepoAegis 选择**：把 N 做成配置项（默认 1），选择器顺序：复现测试从失败变通过 → 回归精选全过 → 规范化 diff 多数投票 → LLM 判官只做平局裁决。做在单循环稳定之后。
- **为什么**：这是最能体现后端能力的功能（并行沙箱 fan-out / fan-in、成本记账），又有最硬的增益证据；但它放大成本 N 倍，必须先有能测出差异的评测集。

### 2.7 复审 / Critic

- **业界做法**：Jules 的 critic 单遍审查直到干净；Claude Code 的 review 子 agent 和 Stop hook；CodeRabbit 把 linter 输出喂给 LLM 再评。
- **证据**：CodeRabbit 现场数据 31,073 条评论：36.4% 被接受、56.3% 被拒，拒绝中 43.3% 是误报；LLM 判官有位置、长度、自我偏好偏差。静态检查前置没有对照研究，但零成本、确定性。
- **RepoAegis 选择**：Review 节点改为"ruff + pyright 静态门 → LLM 只报阻断级问题的结构化输出 → 现有证据驱动兜底"。与 Planning 节点"确定性规则 + LLM 判断取更严格值"的既有哲学一致。
- **为什么**：把便宜的确定性检查放前面，LLM 评审的误报就不会把好补丁打回去空转迭代预算。

### 2.8 多 agent 与角色拆分

- **业界做法**：Cognition 2025 年提出"共享完整轨迹、动作携带隐含决策、避免并行写者"；2026 年各家都加了子 agent，但一律是只读探索或上下文隔离、返回摘要，只有一个写者。
- **证据**：早期角色拆分系统（CodeR、MASAI、MAGIS）帮的是弱模型；Anthropic 多 agent 研究系统 +90% 但 15 倍 token，且是研究任务不是改代码。
- **RepoAegis 选择**：改造计划三、3 的 Locator / Coder / Critic 拆分**不做**。消融实验改题：(A) 现有 9 节点结构化流水线 vs (B) 外层治理 + 内层单循环 vs (C) B + 只读 explore 子 agent。先跑 A vs B。
- **为什么**：A vs B 才是这次重构的核心假设，必须用数据回答；角色拆分的方向业界已经用一年时间走了一圈回来。

### 2.9 记忆

- **业界做法**：三层——仓库级人工指令（AGENTS.md / CLAUDE.md，进 git）、仓库级自动学习笔记（Claude Code auto memory、Cursor Memories："这个测试 flaky""用 uv 别用 pip"）、用户 / 组织级偏好（Devin Knowledge、Mem0）。
- **证据**：SWE-Exp 经验库 +6pp（DeepSeek-V3 36→42）；子任务级分类记忆（2602.21611）Gemini 2.5 Pro 53.5→60.3，但**整段轨迹记忆对 Claude 接近 0 或负收益**；SWE Context Bench：自由访问历史上下文成本 +27%、准确率不变；DreamBench-SWE：无关检索率 47.4%。结论：抽象过的、按阶段分类的洞见有 +2–7pp，原始轨迹有害。
- **RepoAegis 选择**：先做零成本的——读取目标仓库的 AGENTS.md / CLAUDE.md 注入系统提示；再做 `(tenant_id, repo_id)` 命名空间的运行笔记表（测试命令、环境坑、flaky 测试），任务结束时用 LLM 抽取 1–3 条写入；改造计划三、4 的 case-based 案例库降级为"有评测基建后再验证"。
- **为什么**：前两项是业界标配且没有负面证据；案例库的收益依赖检索质量，没有评测集就分不清是帮忙还是添乱。租户隔离照旧。

### 2.10 编排框架与持久化

- **业界做法**：LangGraph 1.x（2025-10 发布，承诺 2.0 前无破坏性变更）是 Python 默认；12-factor agents 主张"框架管管道（checkpoint、流、中断），循环、提示词、工具 schema 自己写"；Diagrid 批评"checkpoint 不等于 durable execution"（没有崩溃检测、自动恢复、防重复恢复），Temporal 2026-07 出了 LangGraph 插件回应；Pydantic AI 把 Temporal / DBOS / Restate 做成可插拔。
- **RepoAegis 事实**：`production_graph.py` 编译图时没传 checkpointer；本地验证 `interrupt()` 在无 checkpointer 时直接返回带 `__interrupt__` 的状态而不挂起；审批实际由 `/approve` 端点写 `status=CODING` 后由 `route_entry` 重入实现。`SqlTaskQueue.claim` 已用 `FOR UPDATE SKIP LOCKED` 租约，Diagrid 说的"两个 worker 重复恢复"这一条其实已经防住了。
- **RepoAegis 选择**：保留 LangGraph 做外层图（成本最低、行业默认），**二选一并写进文档**：(a) 接 `SqliteSaver` / `AsyncPostgresSaver`，`/approve` 改为 `Command(resume=decision)` 续跑，让 `interrupt()` 名实相符；(b) 去掉 `interrupt()`，approval 节点直接返回 `NEEDS_APPROVAL` 结束本次运行，明确"审批靠状态机 + 队列重入"。推荐 (a)，因为内层循环以后也需要 checkpoint 做中途恢复，且 approval 节点第一条语句就是 interrupt，不受"节点从头重跑"的坑影响。不引入 Temporal / DBOS。
- **为什么**：现状是文档写的机制和实际跑的机制不一致，面试或复盘时会被问穿；两种修法都不大，但必须选一个。

### 2.11 沙箱

- **业界做法**：托管沙箱（E2B / Daytona / Modal / Cloudflare）与自托管加固 Docker 并存；社区共识：单租户自用仓库，加固的一次性 Docker（rootless、只读根、drop caps、`--network none` + 允许列表代理）是可接受下限；多租户不共享内核（Firecracker / Kata）。研究侧 SWE-ReX 把"起 shell、执行命令、拿输出"抽象成接口，后端可换 Local / Docker / Modal / Fargate / Daytona。
- **RepoAegis 选择**：现有 `DockerSandbox` 的加固项（digest 锁定镜像、非 root、只读根、cap-drop、seccomp、无网络）已经和业界对齐，保留。新增一个 `Runtime` 接口（`run(cmd, timeout) -> (exit, stdout, stderr)`），内层循环的 bash 工具和 `SandboxVerifier` 共用同一个长生命周期容器（`docker exec` 逐条命令，无状态 shell，mini-SWE-agent 的做法），而不是每条命令起一个容器。
- **为什么**：接口边界是最难事后补的东西；共用容器把单任务从"起 N 次容器"降到"起 1 次"，评测跑得起。

### 2.12 权限、审批、事件与可观测

- **业界做法**：Claude Code 权限管线是 hooks → deny 规则 → ask 规则 → 模式 → allow 规则 → 回调，模式含 `auto`（分类器模型审每个动作）；Codex 把"沙箱模式"和"审批策略"正交；审批正在从布尔值变成"绑定到（主体、工具、调用 id、参数哈希、过期时间）的签名信封"（Vercel AI SDK v6、多份 2026 RFC）；AWS 2026-08 开源 Dogwood（Cedar 扩展，支持时间窗内的计数 / 求和策略）。可观测方面 OTel GenAI 语义约定 2026-06 独立成仓，仍是 Development 状态，但 `invoke_agent` / `execute_tool` / `chat` 三种 span 与 token 属性已事实稳定；Langfuse（MIT）、Phoenix、Weave 都直接吃 OTLP。UI 协议上 AG-UI（run / step / tool / state-delta 事件，有 LangGraph 和 Pydantic AI 编码器）与 Vercel UI message stream 是两种现成选择，SSE 是默认传输。
- **RepoAegis 事实**：`ApprovalEnvelope.digest()` + `PermissionPolicy.authorize()` 每次写操作重算哈希，就是业界正在收敛的"审批绑定参数哈希"模式，是本项目最值得讲的设计；但事件出口为零。
- **RepoAegis 选择**：按改造计划三、5 做 `AgentEvent`，两个发射点（`_graph_action`、`authorize()`）不变；再加内层循环每步一个事件（通过 gateway 天然覆盖）。消费者三个：`structlog` JSON 日志（依赖已在）、`events` 表持久化、进程内队列 → FastAPI SSE 端点。事件字段命名对齐 OTel GenAI（`gen_ai.operation.name`、`gen_ai.usage.input_tokens` 等），以后接 Langfuse 只是加一个 exporter。SSE 事件形状对齐 AG-UI 的 `RUN_STARTED / STEP_* / TOOL_CALL_* / STATE_DELTA`，前端不用自造协议。不引 OPA / Cedar。
- **为什么**：一份事件模型同时喂日志、审计、前端、追踪，这是"后端能力"最集中的展示点；对齐公开约定避免以后重命名。

### 2.13 评测

- **业界做法**：官方 SWE-bench Docker harness + `sb-cli`；mini-SWE-agent 批量跑 + 边跑边云端评；Harbor 成为 Terminal-Bench 及多个基准的中立任务格式；Inspect AI 有 SWE-bench 实现和沙箱插件；SWE-bench Verified 被 OpenAI 宣布退役，Pro / Live / rebench 是当前"难"的标准；约 7.8% 的"正确"补丁其实过不了开发者测试套件（2503.15223）。
- **RepoAegis 选择**：改造计划六的四步流程保留；分层指标（定位准确率 / 补丁可应用率 / resolved）加上每实例步数、token、成本、沙箱秒数；**新增对照组**：同模型、同实例跑 mini-SWE-agent；`inspect/` 骨架要么在这次接通要么删除，不再挂着。
- **为什么**：mini-SWE-agent 对照把"模型能力"和"你的 scaffolding 贡献"分开，是最能回答"你的系统到底加了什么价值"的实验。

---

## 三、代表系统速查表

| 系统 | 循环 | 检索 | 编辑 | 上下文 | 沙箱 | 记忆 | 可查到的成绩（scaffold） |
|---|---|---|---|---|---|---|---|
| mini-SWE-agent | bash-only ReAct，每步独立 subprocess | grep / find | heredoc / sed | 输出截断，无压缩 | Docker / Podman / bwrap / Modal | 无 | Verified 76.8%（Claude 4.5 Opus，2026-02，官方榜） |
| SWE-agent 1.x | ReAct + ACI，可选 RetryAgent | 窗口查看 + 搜索命令 | str_replace_editor | 旧观察折叠 | SWE-ReX | 无 | Verified 66.6%（Claude 4 Sonnet）；已进入维护模式 |
| Agentless | 定位→修复→验证流水线 | 仓库树 + LLM + embedding | search/replace，40 个候选 | 骨架而非全文 | 仅跑测试 | 无 | Verified 50.8%（Claude 3.5）；2024-12 后停更；后继 live-SWE-agent 79.2% |
| AutoCodeRover / SpecRover | 检索 agent → 补丁 agent | 7 个 AST 搜索 API + SBFL | 片段替换 | 只积累 API 返回 | Docker | 无 | Verified 51.6%（v2.1）；被 Sonar 收购，Sonar Foundation Agent 79.2% |
| OpenHands V1 | 同步 ReAct，可选 delegate | grep / glob | str_replace / apply_patch | LLM condenser | Docker / 远程 | `.agents/skills`、AGENTS.md | Verified 72.8%（Sonnet 4.5，SDK 论文） |
| Trae Agent | ReAct + 生成 / 剪枝 / 选择 | bash + SQLite 代码知识图 | str_replace | 无 | Docker 可选 | 无 | Verified 78.8%（Doubao-Seed-Code，官方榜） |
| Claude Code | 单循环 + 子 agent | ripgrep / glob，LSP 插件 | 精确 str_replace | 自动压缩 + 重载 CLAUDE.md | Seatbelt / bwrap+seccomp | CLAUDE.md、rules、MEMORY.md | Verified ≈80.8%（Opus 4.6，自报）；TB4 57.9% |
| Codex CLI | Rust op / event 循环 + 子 agent | shell grep | V4A apply_patch | 自动压缩，模型原生训练过压缩 | Seatbelt / bwrap+seccomp / Landlock | AGENTS.md 链 | TB2 82.7%（GPT-5.5）；Pro 64.6%（GPT-5.6 Sol，自报） |
| Aider | 人机轮流，architect / editor | tree-sitter + PageRank repo map | whole / diff / udiff | repo map + prompt cache | 无 | CONVENTIONS.md | polyglot 88%（gpt-5） |
| Cursor | Agent + 云端子 agent | instant grep + 自训 embedding | fast-apply / edit | Composer 自摘要 | Seatbelt / Landlock；云端 Firecracker | `.cursor/rules`、AGENTS.md | Composer 2 TB2 61.7 |
| Devin | 单 VM 长时 agent + sub-Devin | DeepWiki | 直接编辑 | 未公开 | 云 VM 快照 | Knowledge、Playbooks | 原始 SWE-bench 13.86%（2024）后无产品级数字 |
| Jules | plan → 审批 → 执行 → critic | shell | 直接编辑 | 未公开 | 每任务新 VM | 仓库级偏好记忆 | 无 |
| Copilot coding agent | 每 issue 一个 agent，59 分钟上限 | GitHub code search RAG | commit | 未公开 | Actions runner + 出口防火墙 | copilot-instructions.md、AGENTS.md | Verified 56.0%（VS Code，2025） |

与 RepoAegis 形态最接近的是 **Jules** 和 **Copilot coding agent**：外层有计划、审批、critic、PR，内层是一个 agent。这两个是对外解释产品定位时最好的参照。

---

## 四、论文与工程文章：必读清单

按"读完能改变决策"的顺序排，前 12 篇是核心。

**核心 12 篇**

1. **SWE-bench**（Jimenez，2310.06770，ICLR 2024）——定义任务、指标（FAIL_TO_PASS + PASS_TO_PASS）和 Docker harness。
2. **SWE-agent / ACI**（Yang，2405.15793，NeurIPS 2024）——接口设计比提示词重要：窗口查看、结果上限、编辑前 lint 守卫。
3. **Agentless**（Xia，2407.01489，FSE 2025 杰出论文）——分层定位 + 采样 + 复现 / 回归测试选择 + 多数投票；$0.70/任务的强基线；消融表是复现测试价值的最直接证据。
4. **AutoCodeRover**（Zhang，2404.05427，ISSTA 2024）——AST 级搜索 API + 频谱故障定位；结构化代码搜索优于裸读文件。
5. **OpenHands**（Wang，2407.16741，ICLR 2025）+ **CodeAct**（2402.01030，ICML 2024）——事件流架构、沙箱运行时、代码即动作。
6. **LocAgent**（Chen，2503.09089，ACL 2025）——图引导定位，32B 微调模型 14% 成本追平 Claude 3.5；调用图工具的学术依据。
7. **CodeMonkeys**（Ehrlich，2501.14723，ICML 2025）——test-time scaling 的串行 / 并行两轴，选择质量是瓶颈。
8. **Trae Agent**（ByteDance，2507.23370）——生成 / 剪枝 / 选择三段式，选择器自己写测试并执行；75.2%。
9. **TestPrune**（2510.18270）——回归测试精选 +4–5pp 还省钱；直接对应改造计划四、1。
10. **Diff-XYZ**（2510.12487）——编辑格式对照实验，search/replace 胜出。
11. **SWE-bench Pro**（Scale，2509.16941）+ **The SWE-Bench Illusion**（2506.12286）——Verified 已污染的证据，以及为什么 Pro 用 SWE-Agent scaffold 而弃用 Agentless（多文件编辑）。
12. **Agentic Software Issue Resolution with LLMs: A Survey**（2512.22256，242 篇）——2025 年综述，查漏用。

**训练与验证器**（了解方向即可）

- SWE-Gym（2412.21139）：首个开放的 policy + verifier 训练配方。
- SWE-smith（2504.21798，NeurIPS 2025）：环境稀缺是瓶颈，bug 注入合成 5 万任务。
- SWE-RL（Meta，2502.18449）：无执行的 diff 相似度奖励也能 RL。
- R2E-Gym（2504.07164）：执行式 + 学习式混合验证器优于任一单独。
- Kimi-Dev（2509.23045）：在 Agentless 式流水线里练技能，再迁移到 agent scaffold。

**检索与上下文**

- CORE-Bench（2606.11864）：通用代码 embedding 在 issue→edit 上崩溃，需领域 SFT。
- Is Grep All You Need?（2605.15184）、Codebase-Memory（2603.27277）、LARGER（2605.16352，"先 grep 锚点、再图扩展"）。
- cAST（2506.15655）：如果做 embedding，按 AST 切块。
- Aider repo map 博客（aider.chat/docs/repomap.html）：tree-sitter + PageRank。

**记忆**

- SWE-Exp（2507.23361）、子任务级记忆（2602.21611）、SWE Context Bench（2602.08316）、Agent Workflow Memory（2409.07429）。结论一致：存抽象洞见不存轨迹，检索要严。

**基础范式**：ReAct（2210.03629）、Reflexion（2303.11366）、LATS（2310.04406）、Tree of Thoughts（2305.10601）。

**工程文章（比论文更影响日常决策）**

- Anthropic《Building effective agents》（2024-12）：workflow vs agent 的分类法；Agentless 是 workflow，SWE-agent 是 agent。
- Anthropic《Effective context engineering for AI agents》（2025-09）、《Writing tools for agents》：截断、`concise / detailed` 输出、可操作的错误信息。
- Anthropic《Raising the bar on SWE-bench Verified with Claude 3.5 Sonnet》（2024-10）：极简 scaffold 的出处。
- Cognition《Don't Build Multi-Agents》（2025-06）。
- HumanLayer《12-factor agents》：own your control flow、own your context window、tools are structured outputs。
- Cursor《Improving agent with semantic search》（2025-11）：唯一公开的 embedding 增益对照数据。
- OpenAI《Why we no longer evaluate SWE-bench Verified》（2026）。
- OWASP Top 10 for Agentic Applications 2026：ASI05（意外代码执行）和 ASI06（记忆 / 上下文投毒）是 issue→patch 场景最相关的两条。

---

## 五、对照现有改造计划的逐项评估

| 计划条目 | 评估 | 调整建议 |
|---|---|---|
| 三、1 复现优先（部分完成） | ✅ 方向正确 | 剩余的"模型写复现脚本"依赖内层循环有 bash，排在内层循环之后；复现结果升级为补丁选择的第一信号 |
| 三、2 调用图 + import 图（已完成） | ✅ 有 LocAgent / Codebase-Memory 支撑 | 用途从"检索通道之一"改为"暴露给模型的两个工具 + repo map 数据源 + 回归精选数据源"；只做 Python 的决定与业界（Pro 用 SWE-Agent 也是 Python 优先）一致 |
| 三、3 多 agent 消融 | ⚠️ 题目要改 | Locator / Coder / Critic 拆分不做；改为 A（现有流水线）vs B（外层治理 + 内层单循环）vs C（B + 只读 explore 子 agent） |
| 三、4 case-based 记忆 | ⬇️ 降级 | 先做 AGENTS.md 读取 + `(tenant, repo)` 运行笔记；案例库等评测集能测出 2–7pp 差异再做 |
| 三、5 Hooks / 事件系统 | ✅ 上调为第二优先 | 字段对齐 OTel GenAI，SSE 形状对齐 AG-UI；它是前端和可观测的共同前提 |
| 四、1 Verification 回归检测 | ⬆️ 上调 | TestPrune 证据最硬，调用图现成 |
| 四、2 Planning 风险判定用调用图 | 保持 | 小改动，跟四、1 一起做 |
| 四、3 Review 前置静态检查 | ⬆️ 上调 | 零成本、确定性，放在 LLM 复审前 |
| 四、4 前端对比视图 | 保持，依赖三、5 | 数据源就是事件流 |
| 五 优先级 | 重排 | 见第六节路线 |
| 六 评测方法论 | ✅ 保持 | 加 mini-SWE-agent 对照组、加成本 / 步数指标 |
| 十、5 引入 logging | 并入三、5 | `structlog` 已在依赖里，作为事件的一个消费者 |
| 十、2 `ModelPort.structured()` 签名 | 顺手修 | 内层循环需要 `ModelPort` 加一个原生 tool-calling 方法，一起改 |

**计划里没有、本文建议新增的条目**

- **A. 内层 solver 循环**（本次重构的核心）：在沙箱容器内运行，工具集 `bash` / `edit` / `read` / `grep` / `glob` / `goto_definition` / `find_references` / `finish`，步数与成本双预算，复现优先提示词，输出截断 + 观察折叠。替换 Research 的 Localizer 内部、Coding 的 `_gather_context` + `_propose_and_apply_patch`，Verification 节点保留为 harness 侧的独立验证（不信任模型自报）。
- **B. checkpointer 决策**：见 2.10，二选一。
- **C. `Runtime` 接口 + 长生命周期容器**：见 2.11。
- **D. 删除清单**：Rewriter 与 18 种 SearchKind、`kind_mapping`、`HybridSearchService` 主副搜 + RRF、`SearchRouter`、LLM 版 `CalibrationJudge`（保留规则版做 task_type 守卫）、HISTORY 检索（改成 `git log / blame` 走 bash）、`inspect/` 骨架（接通或删）。
- **E. Best-of-N + 执行式选择器**：N 可配置，默认 1。
- **F. 后端补齐**：`events` / `tool_calls` / `approvals` / `costs` 表，每次运行的 token 与沙箱秒数记账，写工具（push、PR）加幂等键，租户级 token 预算。
- **G. 前端升级**：见第六节。

---

## 六、推荐的目标架构与路线

### 6.1 目标架构（一句话）

**外层是治理状态机，内层是 mini-SWE-agent。** 外层用 LangGraph（接 checkpointer）驱动 Intake → Plan（含风险与审批信封）→ Solve（内层循环，在沙箱内）→ Verify（harness 侧独立验证 + 回归精选）→ Review（静态门 + LLM）→ PR；每个工具调用经过 `authorize()` 单一收口并发射事件；事件同时流向日志、数据库、SSE。

### 6.2 三条能力线各自的选型

**Agent 线**
- 循环：自己写的 while 循环（12-factor），跑在 LangGraph 的一个节点里；原生 tool calling（Responses API 支持）。
- 工具：8 个（见五、A）；工具描述按 Anthropic《Writing tools for agents》写：输出上限、错误信息告诉模型下一步怎么缩小范围。
- 上下文：截断 → 折叠 → 阈值摘要；repo map 开场；系统提示放稳定前缀。
- 编辑：精确 str_replace，编辑后自动 ruff / pyright 诊断。
- 验证与选择：复现测试 + 调用图回归精选 + 可选 best-of-N。
- 记忆：AGENTS.md 读取 + 运行笔记。

**后端线**
- FastAPI + SQLAlchemy + Postgres（开发期 SQLite）；`SKIP LOCKED` 队列保留；LangGraph `AsyncPostgresSaver`（或 SqliteSaver）以 `(tenant_id, task_id)` 为键。
- 事件模型 `AgentEvent`（Strict Pydantic），字段对齐 OTel GenAI；消费者：structlog、`events` 表、asyncio 队列 → `GET /v1/tasks/{id}/events`（SSE，支持 `Last-Event-ID` 断线续传）。
- 沙箱 `Runtime` 接口；每任务一个长生命周期容器；`docker exec` 逐条命令。
- 成本记账：每次模型调用的 usage 落 `costs` 表，按任务和租户聚合；租户级 token 桶。
- 写操作幂等键：`task_id + step + args_hash`。
- 可观测升级路径：加一个 OTLP exporter 指向自托管 Langfuse，不改事件模型。

**轻量前端线**
- 保留 React + Vite，升级到 TypeScript，用 `openapi-typescript` 从 FastAPI 的 OpenAPI 生成类型（前后端契约共享是这条线最值得展示的点）。
- 数据层：TanStack Query 做列表和详情，`EventSource` 做实时事件；不引组件库，现有 CSS 体系继续。
- 四个视图：任务时间线（事件流渲染成 step / tool call 树，可折叠）、diff 查看器（`@git-diff-view/react`，吃后端渲染好的 unified diff）、审批弹窗（展示 plan hash、declared files、allowed tools、风险原因，批准即调 `/approve`）、评测对比（两次运行的分层指标并排，即改造计划四、4）。
- 不做：终端面板（需要 WebSocket + xterm.js，价值不够）、聊天界面。

### 6.3 分阶段路线

| 阶段 | 内容 | 完成标志 |
|---|---|---|
| P0 评测地基 | 10–15 个真实历史 bug 的金标准集；SWE-bench Lite 抽 30–50 个；同模型跑 mini-SWE-agent 对照；分层指标 + 成本 | 一条命令出对比报告 |
| P1 内层循环 | `Runtime` 接口、长生命周期容器、8 个工具、solver 节点替换 Research / Coding 内部；删除清单 D | A vs B 消融有数据 |
| P2 事件与前端 | `AgentEvent`、三个消费者、SSE 端点；前端 TS 化 + 时间线 + diff + 审批弹窗 | 从浏览器看完一次运行并审批 |
| P3 名实相符 | checkpointer + `Command(resume)`；回归精选；Review 静态门；Planning 风险用调用图 | 文档与实现一致，回归精选有节省数据 |
| P4 放大 | best-of-N + 执行式选择器；成本记账与租户预算 | N=3 vs N=1 的增益与成本曲线 |
| P5 记忆 | AGENTS.md 读取；运行笔记；案例库仅在可测时做 | 有无笔记的对照 |

每个阶段结束都能独立讲一个完整故事，中途停下也不会留下半成品。

---

## 七、未核实事项与风险

- 厂商自报的 SWE-bench Verified / Pro 数字（Fable 5、Mythos、GPT-5.6 等）只有厂商页面或聚合站来源，harness 各异，不可横向比较。
- Cursor 当前索引实现（Merkle 树 + embedding + turbopuffer）在 2026 年文档里已不再明说；Amp、Continue 的检索栈没拿到一手资料。
- Windsurf "召回率提升 200%"、Augment "MCP 让合作 agent 提升 70%+"、代码图厂商 "省 121 倍 token" 均为自报，无方法学。
- MCP 2026-07-28 修订是 RC 还是正式版，来源说法不一；对本项目影响为零。
- 记忆类论文的基线 scaffold 各不相同，+2–7pp 的区间应视为量级而非精确值。
- 本文对 RepoAegis 代码的三条事实（无 checkpointer、前端轮询、observability 空壳）是本次直接核对的；其余对现状的描述来自 `docs/` 与 `refactor-plan.md`。

---

## 主要来源

榜单与 scaffold：[swebench.com](https://www.swebench.com/)、[tbench.ai TB4](https://www.tbench.ai/leaderboard/terminal-bench/4)、[SWE-bench/experiments](https://github.com/SWE-bench/experiments)、[Anthropic SWE-bench scaffold](https://www.anthropic.com/engineering/swe-bench-sonnet)、[Epoch harness](https://epoch.ai/benchmarks/swe-bench-verified)、[mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent)、[SWE-agent 维护状态](https://swe-agent.com/latest/)、[OpenHands SDK 论文](https://arxiv.org/html/2511.03690v1)、[Trae Agent](https://arxiv.org/html/2507.23370)、[Codex 沙箱](https://learn.chatgpt.com/docs/sandboxing)、[Claude Code 权限模式](https://code.claude.com/docs/en/permission-modes)、[Claude Code 沙箱](https://code.claude.com/docs/en/sandboxing)、[Jules critic](https://developers.googleblog.com/en/meet-jules-sharpest-critic-and-most-valuable-ally/)。

检索与验证：[Agentless](https://arxiv.org/html/2407.01489v2)、[LocAgent](https://arxiv.org/html/2503.09089)、[SweRank](https://arxiv.org/html/2505.07849)、[CORE-Bench](https://arxiv.org/html/2606.11864)、[Codebase-Memory](https://arxiv.org/html/2603.27277v1)、[Is Grep All You Need](https://arxiv.org/html/2605.15184v1)、[Cursor semsearch](https://cursor.com/blog/semsearch)、[Augment 工程师访谈](https://jxnl.co/writing/2025/09/11/why-grep-beat-embeddings-in-our-swe-bench-agent-lessons-from-augment/)、[Claude Code 放弃 RAG](https://newsletter.pragmaticengineer.com/p/building-claude-code-with-boris-cherny)、[CodeMonkeys](https://arxiv.org/html/2501.14723v1)、[TestPrune](https://arxiv.org/html/2510.18270)、[TDAD](https://arxiv.org/html/2603.17973)、[Diff-XYZ](https://arxiv.org/html/2510.12487v2)、[OpenAI apply_patch 指南](https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide)、[CodeRabbit 现场数据](https://arxiv.org/html/2607.03316v2)、[SWE-Exp](https://arxiv.org/html/2507.23361)、[子任务级记忆](https://arxiv.org/html/2602.21611)、[SWE Context Bench](https://arxiv.org/html/2602.08316)。

工程基建：[LangGraph 1.0](https://www.langchain.com/blog/langchain-langgraph-1dot0)、[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Diagrid: checkpoints ≠ durable execution](https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows)、[Temporal LangGraph 插件](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)、[12-factor agents](https://github.com/humanlayer/12-factor-agents)、[SWE-ReX](https://github.com/SWE-agent/SWE-ReX)、[OTel GenAI 约定](https://opentelemetry.io/docs/specs/semconv/gen-ai/)、[Langfuse 自托管](https://langfuse.com/self-hosting)、[AG-UI](https://docs.copilotkit.ai/agentic-protocols/ag-ui)、[Vercel UI stream](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)、[Vercel tool approvals](https://ai-sdk.dev/docs/agents/tool-approvals)、[AWS Dogwood](https://www.infoq.com/news/2026/08/aws-dogwood-agent-policy/)、[Hatchet 多租户队列](https://hatchet.run/blog/multi-tenant-queues)、[@git-diff-view/react](https://www.npmjs.com/package/@git-diff-view/react)、[OWASP Agentic Top 10](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)。

综述与方法论：[Cognition](https://cognition.com/blog/dont-build-multi-agents)、[Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[Anthropic writing tools](https://www.anthropic.com/engineering/writing-tools-for-agents)、[OpenAI 退役 Verified](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)、[SWE-bench Pro](https://arxiv.org/html/2509.16941v2)、[SWE-Bench Illusion](https://arxiv.org/abs/2506.12286)、[Issue-resolution 综述 2512.22256](https://arxiv.org/abs/2512.22256)。
