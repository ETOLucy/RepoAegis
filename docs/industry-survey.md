# 业界 Coding Agent 全流程调研（2026-09）

**日期**：2026-09-12
**范围**：issue → patch 这一类 coding agent 的全流程方案。覆盖 19 个主流系统、80 余篇论文与工程文章，以及 SWE-bench Verified / SWE-bench Pro / Terminal-Bench 的实时榜单数据。
**方法**：五路并行调研（框架架构、学术论文、工程基建、检索与验证、经典 scaffold 深挖），对照一手来源（GitHub / arXiv / 官方博客 / swebench.com 与 tbench.ai 页面数据）。标 **[未核实]** 的条目只有二手来源。
**定位**：这是一份纯粹的外部事实档案，只记录业界做了什么、有什么证据、哪些有效哪些是噱头。不含针对任何具体项目的选型建议。

---

## 一、2026 年收敛到了什么

| 维度 | 2024 年主流 | 2026 年收敛结果 | 代表 |
|---|---|---|---|
| 循环结构 | 分阶段流水线（定位→修复→验证） | 单个 ReAct 循环直到模型说完成；子 agent 只读、返回摘要 | mini-SWE-agent、Claude Code、Codex、OpenHands V1 |
| 工具集 | 十几个专用检索 API | bash + 1 个编辑器 + grep/glob + read；LSP / 代码图作为可选插件 | Anthropic SWE-bench scaffold（"一个 bash 工具和一个字符串替换编辑器"）、Auggie v2（"一个 bash 三个文件工具"） |
| 检索 | embedding RAG | grep 为主，embedding 退居 IDE 层，代码图用于依赖类问题 | Claude Code 明确放弃向量库；Cursor 是唯一有对照数据证明 embedding 有增益的 |
| 编辑格式 | 整文件重写 / unified diff | 精确 old/new 字符串替换（str_replace）或 OpenAI V4A apply_patch | Diff-XYZ：search/replace 0.96 vs udiff 0.90 |
| 上下文 | 截断 | 工具输出截断 + 旧观察折叠 + 阈值触发的 LLM 摘要压缩 | Claude Code、Codex、OpenHands condenser；Amp 反其道用 handoff |
| 验证 | 跑测试 | 复现脚本优先 + 回归测试精选 + 执行式补丁选择 | Agentless、Trae、CodeMonkeys |
| 沙箱 | Docker | 本地 CLI 用 Seatbelt / bubblewrap+seccomp；托管用 Firecracker / VM；研究用 SWE-ReX 抽象 | Codex、Claude Code、SWE-agent |
| 权限 / HITL | 全自动或全手动 | 三层：静态允许/拒绝规则 → 沙箱 → 分类器/人工；审批绑定到具体调用的参数哈希 | Claude Code 权限管线、Cursor Auto-review、Vercel AI SDK v6 tool-approval |
| 记忆 | 无 | 仓库级指令文件（AGENTS.md / CLAUDE.md）+ 自动学习笔记 | AGENTS.md 已由 Agentic AI Foundation 托管 |
| 多 agent | 角色拆分（Manager / Coder / QA） | "单写者"原则：并行只读探索，串行写入 | Cognition《Don't Build Multi-Agents》；2026 年各家都加了只读子 agent |

**几条值得单独记住的事实**

- **分阶段流水线为什么输**：Agentless / AutoCodeRover 把"定位 → 修复 → 验证"当作模型解题的方法，阶段间单向传递。定位阶段猜错文件，修复阶段没有退回重找的能力。单循环里模型改一处、跑一下、发现不对再回头，这个反馈回路是分阶段结构给不了的。这两个项目分别在 2024-12 和 2025-04 后停止更新，现在只作为廉价基线和 test-time scaling 的外壳存活。
- **SWE-bench Pro 的作者弃用 Agentless 改用 SWE-Agent scaffold**，理由是 Agentless"在多文件编辑上有困难"。
- **极简 harness 的原始出处**是 Anthropic 2024-10 的 SWE-bench 报告：只给模型 bash 和一个字符串替换编辑器，采样"直到模型认为自己做完了"。Epoch AI 的独立 harness 是同一形态。

---

## 二、榜单现状（供校准预期，不宜直接对标）

- **SWE-bench Verified**：官方榜 2026 年零提交。顶部是 live-SWE-agent + Claude 4.5 Opus 79.2%（2025-12）、Sonar Foundation Agent 79.2%、TRAE + Doubao-Seed-Code 78.8%。厂商自报已到 88–95%。**OpenAI 已发文宣布不再评估 Verified**，Anthropic 对 Verified / Pro 做记忆化筛查。该基准已饱和且有污染证据。
- **SWE-bench Pro**：公榜（统一用 SWE-Agent scaffold）Sonnet 4.5 43.7%、GPT-5 36.3%。厂商用自家 harness 报 58–81%。**同一个模型，20 个点的差距全部来自 harness**，这是理解 scaffold 价值的最直接数据。
- **Terminal-Bench 4.0**（2026-09 上线，8 小时超时，约 66 个任务）：Codex + GPT-6 Astra 58.2%、Claude Code + Fable 5.1 57.9%、Claude Code + Opus 5 51.8%。注意 TB4 的 scaffold 是各厂商自己的 CLI，不是中立的 Terminus-2。
- **污染证据**：《The SWE-Bench Illusion》(2506.12286) 发现模型仅凭 issue 文本就能在 SWE-bench 仓库上以最高 76% 的准确率说出要改的文件路径，而在其他仓库只有 53%。另有研究 (2503.15223) 发现 7.8% 被判"正确"的补丁实际通不过开发者的测试套件。

---

## 三、逐环节：业界做法、证据、有效性判断

每一节按"业界做法 / 关键证据 / 什么有效、什么是噱头"三段写。

### 3.1 复现优先

**业界做法**：Anthropic 的 SWE-bench 提示词是"探索仓库 → 写复现脚本并执行 → 修 → 重跑 → 想边界情况"。SWE-agent、OpenHands、Trae 的提示词同构。Kimi-Dev 把它做成 BugFixer + TestWriter 自博弈。

**证据**：Agentless 的消融是最直接的数据——多数投票 25.67% → 加回归测试 27.0% → 加复现测试 32.0%（Lite），复现测试是最大单项增益。但 300 条生成的复现测试只有 94 条在真实修复上也通过，issue 本身的模糊度是天花板。Kimi-Dev 的 40 补丁 × 40 测试打分把 48.0% 推到 60.4%。反例：TDAD (2603.17973) 发现对 30B 小模型强推 TDD 流程反而让回归率从 6.08% 升到 9.94%。AgentLens (2605.12925) 指出约 10.7% 的"通过"是运气（有回归循环、盲目重试、缺验证）。

**判断**：**"先写复现脚本"本身是共识但直接因果证据薄弱**，而且对弱模型可能有害。**真正有硬证据的是用复现测试的执行结果去选补丁**。两者常被混为一谈，值得分开对待。

### 3.2 定位与检索

**业界做法**：Claude Code / Cline / Codex 不建索引，靠 grep / glob / read。Cursor / Augment / Copilot 建 embedding 索引，但主要服务 IDE 场景。Sourcegraph 反向卖精确代码智能（SCIP）而非 embedding。Aider 用 tree-sitter + PageRank 的 repo map 做开场上下文。Serena / Claude Code LSP 插件把 go-to-definition / find-references 暴露成工具。

**证据**：

- Claude Code 负责人公开说早期用过"RAG + 本地向量库"，很快发现"agentic search 普遍更好，而且更简单，没有安全、隐私、陈旧、可靠性那些问题"，生产里"真的就是 glob 和 grep，由模型驱动"。
- Augment 自己的 SWE-bench 工程师说"embedding 检索不是瓶颈"，agent 反复换 grep 就能找到。
- **CORE-Bench (2606.11864)**：通用代码 embedding 模型在传统代码检索上 NDCG@10 71.7，到 issue→edit 定位任务掉到 20.3。只有在 PR 数据上做领域 SFT 才有用（Qwen3-8B SFT 后 32.8）。这解释了为什么 Cursor 有效而通用 embedding 无效。
- **Cursor 是唯一公开对照数据的厂商**：用 agent 轨迹训练的 embedder，问答准确率 +12.5%（按模型 6.5–23.5%），代码保留率总体 +2.6%，但**在 1000 文件以上的大仓库才明显**。
- **Codebase-Memory (2603.27277)**：代码图 MCP 在 31 种语言上得分 0.83 vs grep 探索 0.92，但 token 用量 1k vs 10k、工具调用 2.3 vs 4.8。**代码图省 token，不提准确率。**
- **LocAgent (2503.09089)**：图引导定位文件级 Acc@5 87.6%（BM25 61.7%），微调的 32B 模型能以 14% 的成本追平 Claude 3.5。**SweRank (2505.07849)**：检索+重排完全不用 agent，函数级 Acc@5 81.4%，$0.011–0.015/实例、12.5 秒，对比 agent 方案的 $0.66、85 秒。
- **Agentless 统计**：约 50% 的 SWE-bench Lite issue 直接点名了要改的文件。定位经常根本不是瓶颈。
- **《Is Grep All You Need?》(2605.15184)**：跨 Claude Code / Codex / Gemini CLI，grep 普遍胜过向量检索，但**工具输出的投递方式（内联 vs 写文件）造成的差异和检索器本身一样大**，有 10 组对比里 5 组因此反转。

**判断**：
- **成立**：持续运行的 grep/glob/read agent 是强基线，通用 embedding RAG 在 SWE-bench 规模的仓库上打不过它。
- **成立**：embedding 只在用 agent 轨迹或 PR 数据训练过、且仓库足够大时才有增益。
- **成立**：结构化工具（LSP、代码图、repo map）稳定地把依赖类问题的 token 和工具调用降 2–10 倍，但不提高准确率。
- **成立**：分层定位和训练过的 reranker 在文件/函数级又便宜又准。
- **噱头**：Windsurf 的"召回率提升 200%"、Augment 的"MCP 让合作 agent 提升 70%+"、代码图厂商的"省 121 倍 token"，全是自报，无方法学。
- **关键提醒**：harness 设计（工具描述、输出投递方式、截断策略）对结果的影响不亚于检索算法本身。

### 3.3 上下文管理

**业界做法**：工具输出截断（Claude Code 25k token 上限；mini-SWE-agent 超过 1 万字符保留首尾各 5 千）；旧观察折叠（SWE-agent 的 `LastNObservations`）；到窗口 50–80% 触发 LLM 摘要压缩（Codex、Gemini CLI、Kiro、OpenHands condenser）。Amp 认为压缩有损，改用 `/handoff`。Anthropic 建议子 agent 隔离上下文、只回传摘要，并把笔记持久化到窗口外。

**证据**：Anthropic 的"context rot"论述（token 数上升时模型从上下文里准确回忆的能力下降）。OpenHands 的 condenser 声称约 2 倍成本下降。CodeMonkeys 发现让模型通读全部文件并排相关性花 14.6% 预算，但能让 92.6% 的实例把所需文件塞进 128k（50.5 倍压缩）。Anthropic 的《Writing tools for agents》给了具体手段：合并工具、把晦涩 ID 解析成人类可读名、`concise`/`detailed` 输出格式枚举（示例中 206 → 72 token）、可操作的截断与错误信息。

**判断**：**缺少严格对照实验，属于工程共识而非实证结论**。截断和折叠是确定性的、零成本、可测试；LLM 摘要压缩引入额外调用和信息丢失，是有代价的一步。

### 3.4 编辑原语

**业界做法**：str_replace（Claude Code、OpenHands、Trae、Gemini CLI、Augment）或 OpenAI V4A apply_patch（Codex、Cline、Kilo）。Aider 保留多种格式按模型切换。fast-apply 小模型（Morph / Relace）在退潮，Devin 因小模型误解指令放弃了它。

**证据**：**Diff-XYZ (2510.12487)** 是最干净的对照实验——大模型上 search/replace 应用精确匹配率 0.96–0.97，udiff 0.90，冗长版 udiff 崩到 0.01–0.08；0.5B 以下模型在所有格式上都失败。Aider 的历史数据：GPT-3.5 时代整文件重写（46%）胜过 diff（30%）；unified diff 曾把 GPT-4 Turbo 从 20% 拉到 61%，靠的是把"偷懒省略"减少 3 倍。OpenAI 的官方原则是"不用行号，同时给出被替换的精确代码和替换后的精确代码，带清晰分隔符"。Relace 称确定性 search/replace 在没有额外逻辑时约 10% 失败率，而前沿模型现在约 96% 编辑成功率。

**判断**：
- **成立**：不带行号、给出精确 old/new 的格式（search/replace、str_replace、V4A）对前沿模型最优。
- **成立**：unified diff 是 GPT-4-Turbo 时代的权宜之计；整文件重写只适合弱模型或极小文件。
- **成立**：常见失败模式是目标区域漂移或重复、跨文件依赖、空白与换行符差异，以及无用的错误反馈。健壮的实现用"精确匹配 → 模糊回退"并给详细错误信息。
- **噱头**：apply 模型厂商自产的跨厂商准确率对比表（Morph 的 98% vs Claude 86% 之类），无方法学。

### 3.5 执行、验证与回归测试

**业界做法**：模型在沙箱里自己跑测试；harness 层做基线检查（改动前就失败的测试不算 agent 的锅）；回归测试选择正在从"全跑"变成"精选"。

**证据**：**TestPrune (2510.18270)** 是这一节最硬的数据——LLM 定位可疑函数 → 取相关测试文件 → 覆盖率贪心最小化到约 9 个测试（对比十文件基线的 117 个、全套的 1000 倍），52 秒 vs 23 分 49 秒，精确率 0.63 vs 0.28。接入后：Agentless + Claude 50.8 → 55.6%，SWE-Agent 56.7 → 62.0%（Lite），**Trae Agent 65.0 → 70.2%**，成本 $0.02–0.05/实例且**倒省** 8–23% 的 agent 开销。TDAD 用静态 AST + 命名启发式生成 `test_map.txt`，回归率 6.08% → 1.82%（−70%），代价是 2 个百分点的 resolve。Trae 自己的 pruner 用 tester agent 挑真回归测试，假阴性 3.69%。现实差距：agent 在改代码的 PR 里只有 49.6% 附带了测试改动。

**判断**：**成立且被低估**——定向回归测试精选是少数几个"既提分又省钱"的手段。**未证实**：没有找到任何商业 agent 公开表明用了覆盖率驱动的回归测试选择（pytest-testmon 那一类）。

### 3.6 补丁选择与 test-time compute

**业界做法**：生成 N 个候选 → 剪枝 → 选择。Trae 的三段式（Generator / Pruner / Selector）、CodeMonkeys 的串行迭代 + 并行采样（每个候选自带测试）、OpenHands 用训练过的 critic 重排 5 个 rollout。

**证据**：

| 系统 | 基线 → 加选择 | oracle 上限 | 选择器 | 成本 |
|---|---|---|---|---|
| Agentless（Lite） | 25.7% → **32.0%** | 42.0% | 多数投票 + 复现/回归测试 | $0.70/实例 |
| CodeMonkeys（Verified） | 随机 45.8% → **57.4%** | 69.8% | 测试投票 + top-3 + 多轮选择器 | 选择环节占预算 <10% |
| Barrel of Monkeys（榜单补丁集成） | 最佳单体 62.8% → **66.2%** | 80.8% | 同上 | — |
| Trae Agent（Verified） | 单次 59–66.4% → **75.2%** | N=3 oracle 比随机高 20.8pp | 去重 + 回归过滤 + LLM 投票 | — |
| OpenHands + critic | 60.6% → **66.4%** @N=5 | — | TD 训练的 32B critic | — |
| SWE-Gym verifier | 20.6% → **32.0%** @16 | 42.8% | 2.6k 轨迹训练的 ORM | — |
| 纯 LLM 判官 | 76.1% → **78.2%** @N=3 | 84.4% | Gemini 2.5 Flash，不执行 | — |
| Kimi-Dev | 48.0% → **60.4%** | — | 40 补丁 × 40 测试执行 | — |

《Large Language Monkeys》(2407.21787) 给出背景规律：覆盖率（任一样本正确）随样本数在四个数量级上呈对数线性增长，DeepSeek-Coder-V2 在 Lite 上从 15.9%（1 样本）到 56%（250 样本）。

**判断**：
- **成立**：执行式选择（生成的复现测试 + 回归测试）是证据最强的杠杆，值 5–12 个百分点，成本适中。
- **成立**：学习型 critic 优于提示词判官；两者都不如执行信号。
- **成立**：异构系统集成能免费捡到便宜（Barrel of Monkeys 从 62.8 到 66.2）。
- **有争议**：Augment 明说多模型集成"对真实产品不现实"。纯 LLM 判官在强候选池上只加约 2 个百分点。
- **瓶颈在选择不在覆盖**：oracle 上限普遍比实际高 10–15 个百分点。

### 3.7 复审与静态门

**业界做法**：Jules 有单遍 critic 反复审到干净为止；Claude Code 有 review 子 agent 和 Stop hook；CodeRabbit 把 linter 与静态分析输出喂进 LLM 提示词再评。

**证据**：CodeRabbit 的现场数据（10,191 个 PR 上的 31,073 条评论）——**36.4% 被采纳、56.3% 被拒**，被拒的里面 43.3% 是误报，误报中 43% 源于缺乏整体系统理解；测试类建议采纳率 87.5%。LLM-as-judge 的系统综述指出存在位置偏好、长度偏好、自我偏好三类偏差。Agent-as-a-Judge (2410.10934) 显示让评判者能访问中间状态和产物后，与人类共识的一致率约 90%，而纯 LLM-as-judge 只有 60–70%。

**判断**：**成立**：评判者需要工具访问轨迹和产物，不能只看最终 diff；让判官先写评语再打分（Critique-out-Loud）能改善校准。**未证实**：静态检查前置（先跑 linter/type checker 再让 LLM 评审）没有任何对照研究隔离出这个顺序的效果，尽管它零成本且确定性。

### 3.8 多 agent 与角色拆分

**业界做法**：Cognition 2025-06 的《Don't Build Multi-Agents》提出三条——共享完整轨迹而非只传消息、动作携带隐含决策、避免并行写者。到 2026 年各家都加了子 agent，但一律是**只读探索或上下文隔离、返回摘要，只有一个写者**。Cognition 自己也上了 sub-Devin，OpenAI 给 GPT-5.6 Sol 做了 4 agent 的 ultra 模式。

**证据**：早期角色拆分系统（CodeR 28.33%、MASAI、MAGIS）帮助的是弱模型。Anthropic 的多 agent 研究系统比单 agent 好约 90%，但 token 成本 15 倍，且任务是研究不是改代码。

**判断**：**成立**：并行只读探索 + 串行写入（单写者原则）是当前共识。**已被证伪**：Locator / Coder / Critic 那种把解题流程按角色切开的拆法，在强模型上没有优势，业界用一年时间走了一圈又回来了。

### 3.9 记忆

**业界做法**：三层——仓库级人工指令（AGENTS.md / CLAUDE.md，进 git）、仓库级自动学习笔记（Claude Code auto memory、Cursor Memories，存"这个测试 flaky""用 uv 别用 pip"这类）、用户或组织级偏好（Devin Knowledge、Mem0）。AGENTS.md 已由 Linux Foundation 下的 Agentic AI Foundation 托管，Codex、Copilot、Cursor、Gemini CLI、Jules、Devin、Aider、Zed 等都读它。

**证据**：SWE-Exp (2507.23361) 的经验库让 DeepSeek-V3 从 36.0% 到 42.0%。子任务级分类记忆 (2602.21611) 让 Gemini 2.5 Pro 从 53.5% 到 60.3%，但**整段轨迹记忆对 Claude 模型接近 0 或负收益**。Memory Transfer Learning (2604.14004)：洞见级记忆平均 +3.7% Pass@3，原始轨迹则造成"实现方式的脆性锚定"并在部分基准上掉分。SWE Context Bench (2602.08316)：oracle 摘要把 resolve 从 26.3% 拉到 34.3%，但**自由访问历史上下文使成本 +27% 而准确率不变**。DreamBench-SWE (2608.20664)：观察到的无关检索率 47.4%、陈旧激活 20%、重复犯错 57.1%。

**判断**：**成立**：抽象过的、按任务阶段分类的洞见，配合严格的检索门槛，能带来 +2–7 个百分点，成本可忽略。**已被证伪**：整段轨迹或自由形式的记忆是中性偏有害的，而且更贵。**噱头**：没有任何厂商公布过记忆功能带来的 resolve rate 增量；"会随时间持续改进"的说法没有数字支撑。

### 3.10 编排框架与持久化

**业界做法**：LangGraph 1.x（2025-10 发布，承诺 2.0 前无破坏性变更）是 Python 默认选择。HumanLayer 的《12-factor agents》主张"框架管管道（checkpoint、流、中断），循环、提示词、工具 schema 自己写"，其论点是框架优先的团队质量会卡在 80% 左右，而多数生产级 coding agent 本质是"大部分确定性的软件 + 少数几个放 LLM 的决策点"。Pydantic AI 把 Temporal / DBOS / Prefect / Restate 做成可插拔的持久化后端。

**证据与争议**：Diagrid 的批评被广泛引用——**checkpoint 不等于 durable execution**：LangGraph 给你保存的状态，但没有崩溃检测、没有自动恢复（必须用正确的 `thread_id` 重新 invoke）、没有防止两个 worker 重复恢复同一个线程。Temporal 在 2026-07 发布了 LangGraph 插件作为回应，把每个节点跑成 Temporal Activity。LangGraph 自己的文档明确警告：`interrupt()` 恢复时**整个节点从头重跑**，所以 interrupt 之前的代码必须幂等，且不能包在 try/except 里。

**判断**：2026 年的共识是"用框架来接你不想自己写的管道，但控制流、提示词、工具 schema 留在自己代码里"。框架 vs 手写不再是非此即彼。**具体到只有少数中断点的系统，引入图框架的理由是薄的**——Postgres `SELECT FOR UPDATE SKIP LOCKED` 的租约 + 自己的状态表就能覆盖，而且更透明。

### 3.11 沙箱

**业界做法**：托管沙箱（E2B Firecracker 约 150ms 启动、Daytona 约 90ms、Modal gVisor 唯一支持 GPU、Cloudflare Sandboxes 2026-04 GA）与自托管加固 Docker 并存。本地 CLI 走进程级隔离：Claude Code 用 macOS Seatbelt、Linux bubblewrap + socat 域名白名单代理 + 可选 seccomp；Codex 用 `sandbox-exec` / bwrap+seccomp，三档模式（read-only / workspace-write 默认 / danger-full-access）与审批策略正交。研究侧 SWE-ReX 把"起 shell、执行命令、拿输出"抽象成接口，后端可换 Local / Docker / Modal / Fargate / Daytona。

**社区共识**：单租户自用仓库，加固的一次性 Docker（rootless、只读根、drop caps、no-new-privileges、seccomp、默认 `--network none` + 出口白名单代理）是可接受下限；多租户不共享内核，用 Firecracker / Kata 或直接租用托管沙箱。

**值得注意的具体设计**：Claude Code 的凭据遮蔽——沙箱进程只看到哨兵值，由代理在出站时注入真实密钥（含 TLS 终止和 AWS SigV4 重签名）。Cloudflare 的出口凭据注入是同一思路。

### 3.12 权限、审批与事件

**业界做法**：Claude Code 的权限管线是 hooks → deny 规则 → ask 规则 → 权限模式 → allow 规则 → 回调，模式含 `auto`（一个独立的分类器模型审查每个动作，看得到用户消息和工具调用但工具结果被剥离）。Cursor 3.6 的 Auto-review 是同一形态（白名单 → 沙箱 → LLM 分类器），但 Cursor 自己声明该分类器"是尽力而为的便利功能，不是安全边界"。**跨厂商的共同形态是三层叠加：静态允许/拒绝规则 → OS 沙箱 → 模型分类器**，人工提示是最后手段。

**审批信封**：正在从布尔值变成**绑定到（主体、工具名、调用 id、参数哈希、过期时间）的签名信封**，执行时重新校验，这样模型无法在批准之后调包参数。Vercel AI SDK v6 已经内置 `tool-approval-request/response`，2026-03 有多份独立 RFC 收敛到同一组字段。AWS 在 2026-08 开源 Dogwood（Cedar 扩展），用 `formerly`、`count_within`、`sum_within` 这类时间窗算子表达审批、限流和累计花费上限。

**可观测**：OTel GenAI 语义约定 2026-06 独立成仓，整体仍是 Development 状态，但 `invoke_agent` / `execute_tool` / `chat` 三种 span 和 `gen_ai.usage.*` token 属性事实上已稳定。Langfuse（MIT，可自托管）、Arize Phoenix、W&B Weave 都直接吃 OTLP。**没有统一的 agent run schema**，但 OTel GenAI、OpenInference、Langfuse 三者在收敛。

### 3.13 Agent 与 UI 之间

**业界做法**：AG-UI（CopilotKit）定义了传输无关的事件流——生命周期（`RUN_STARTED/FINISHED`）、文本（`TEXT_MESSAGE_*`）、工具（`TOOL_CALL_*`）、状态（`STATE_SNAPSHOT/STATE_DELTA`，用 JSON Patch），原生支持 LangGraph、Pydantic AI、CrewAI、Mastra。Vercel AI SDK 的 UI message stream 是 SSE，v6 新增了 `tool-approval-request/response`。MCP 的 2026-07-28 修订是最大一次改动（无状态核心、Tasks 扩展、elicitation 改为多轮请求、OAuth 加固）。

**传输选择**：2026 年共识是默认 SSE（HTTP 原生、`Last-Event-ID` 自动重连、代理友好、鉴权简单），只有客户端需要高频回传时才用 WebSocket。挂 PTY 的终端面板是唯一真正需要 WebSocket 的组件。

**产品们实际渲染什么**：Devin 是右侧面板的 Progress / Shell / Browser / Editor 标签页，点计划步骤会把 shell、编辑、浏览器活动过滤到该步。OpenHands V1 是事件溯源的会话 + 内嵌 VS Code Web / VNC。**共同的控件集合是：步骤时间线（带状态的计划）、每次编辑的 diff 查看器、终端/日志面板、绑定到具体工具调用的审批弹窗、成本/token 计量。**

### 3.14 评测基础设施

**业界做法**：官方 SWE-bench 是 Docker harness + `sb-cli`（边跑边提交云端评测）。**Harbor** 正在成为中立的任务格式，已经驱动 Claude Code、Codex CLI、OpenHands、mini-SWE-agent 和 Terminus 2，并可扩展到 Daytona / Modal。Inspect AI（UK AISI，MIT）有 SWE-bench 实现、Docker/K8s/Proxmox 沙箱插件和工具审批机制。SWE-agent 官方文档现在把新用户直接指向 mini-swe-agent。

**当前"难"的基准**：SWE-bench Pro（1,865 个长周期任务，含商业闭源分割）、SWE-bench Live（2024 年后的 issue，自动化维护）、SWE-rebench（去污染的持续更新榜）、SWE-PolyBench（唯一带 AST 定位指标的）、Terminal-Bench 4.0。

---

## 四、代表系统速查表

| 系统 | 循环 | 检索 | 编辑 | 上下文 | 沙箱 | 记忆 | 可查到的成绩 |
|---|---|---|---|---|---|---|---|
| mini-SWE-agent | bash-only ReAct，每步独立 subprocess，约 100 行 | grep / find | heredoc / sed | 输出截断，无压缩 | Docker / Podman / bwrap / Modal | 无 | Verified 76.8%（Claude 4.5 Opus，官方 bash-only 榜） |
| SWE-agent 1.x | ReAct + ACI，可选 RetryAgent 重试元循环 | 窗口查看 + 搜索命令 | str_replace_editor | 旧观察折叠、prompt cache 标记 | SWE-ReX | 无 | Verified 66.6%（Claude 4 Sonnet）；**已进入维护模式，官方指向 mini** |
| Agentless | 定位→修复→验证流水线，无 agent | 仓库树 + LLM + embedding | search/replace，40 个候选 | 类骨架而非全文 | 仅跑测试 | 无 | Verified 50.8%；2024-12 后停更 |
| AutoCodeRover / SpecRover | 检索 agent → 补丁 agent | 7 个 AST 搜索 API + 频谱故障定位 | 片段替换 | 只积累 API 返回 | Docker | 无 | Verified 51.6%；2025-02 被 Sonar 收购 |
| Moatless | 结构化动作循环 / MCTS 变体 | embedding + AST | StringReplace | FileContext token 预算 | Docker | 无 | Verified 70.8%（Claude 4 Sonnet，$0.64/实例）；作者自称业余项目 |
| OpenHands V1 | 同步 ReAct，可选 delegate | grep / glob | str_replace / apply_patch | LLM condenser | Docker / 远程 | `.agents/skills`、AGENTS.md | Verified 72.8%（Sonnet 4.5，SDK 论文） |
| Trae Agent | ReAct + 生成/剪枝/选择 | bash + SQLite 代码知识图 | str_replace | 无压缩，Lakeview 步骤摘要 | Docker 可选 | 无 | Verified 78.8%（Doubao-Seed-Code，官方榜） |
| Refact.ai | 规划器 + 只读子 agent 群 | tree-sitter AST (LMDB) + VecDB | update_textdoc / apply_patch | 四级压缩管线 | 本地 | `.refact/knowledge` 知识图 | Verified 74.4%（2025-06） |
| Claude Code | 单循环 + 子 agent（≤3 层嵌套） | ripgrep / glob，LSP 插件 | 精确 str_replace | 自动压缩 + 重载 CLAUDE.md | Seatbelt / bwrap+seccomp | CLAUDE.md、rules、MEMORY.md | Verified ≈80.8%（Opus 4.6）；TB4 57.9%（Fable 5.1） |
| Codex CLI | Rust op/event 循环 + 子 agent | shell grep | V4A apply_patch | 自动压缩，模型原生训练过跨窗口压缩 | Seatbelt / bwrap+seccomp / Landlock | AGENTS.md 链，32KiB 上限 | TB2 82.7%（GPT-5.5）；Pro 64.6%（GPT-5.6 Sol） |
| Aider | 人机轮流，architect/editor 双模型 | tree-sitter + PageRank repo map | whole / diff / udiff 可切 | repo map + prompt cache | 无 | CONVENTIONS.md | polyglot 88.0%（gpt-5） |
| Cursor | Agent + 云端子 agent | instant grep + 自训 embedding | fast-apply / edit | Max 模式、Composer 自摘要 | Seatbelt / Landlock；云端 Firecracker | `.cursor/rules`、AGENTS.md | Composer 2：TB2 61.7 |
| Devin | 单 VM 长时 agent + sub-Devin | DeepWiki 仓库索引 | 直接编辑 | 未公开 | 云 VM + 快照 | Knowledge、Playbooks | 原始 SWE-bench 13.86%（2024），之后无产品级数字 |
| Amp | 主 agent + Search/Oracle/Librarian 子 agent | grep/glob/codebase_search | old_str/new_str | **移除了压缩**，改用 `/handoff` | Orbs 隔离远程环境 | AGENTS.md | 未公布 |
| Augment | 单 agent + Context Engine | 自托管 embedding，100M 行 <200ms | read / edit / write + bash | 廉价模型压缩，首条消息逐字保留 | Docker / VM | `.augment/rules` | Verified 65.4%（2025）；Pro 61%（自报） |
| Copilot coding agent | 每 issue 一个 agent，59 分钟上限 | GitHub code search RAG | commit | 未公开 | Actions runner + 出口防火墙 | copilot-instructions.md | Verified 56.0%（VS Code，2025-04） |
| Jules | plan → 人工审批 → 执行 → critic | shell | 直接编辑 | 未公开 | 每任务新 Cloud VM | 仓库级偏好记忆 | 未公布 |
| Kiro（前 Amazon Q） | 规格驱动 DAG；自治模式是多 agent | grep + LSP | write | 约 80% 触发压缩 | 云沙箱 | `.kiro/steering` | Q Developer 曾 66% Verified（2025-04） |
| live-SWE-agent | 从 mini 的 bash-only 起步，**运行时自己生成工具** | 自生成 | 自生成 | 继承 mini | 继承 mini | 无 | **Verified 79.2%（Claude 4.5 Opus），官方榜第一** |

---

## 五、必读清单

按"读完能改变工程决策"排序，前 12 篇是核心。

1. **SWE-bench**（2310.06770，ICLR 2024）——定义任务、指标（FAIL_TO_PASS + PASS_TO_PASS）和 Docker harness。
2. **SWE-agent / ACI**（2405.15793，NeurIPS 2024）——接口设计比提示词重要：窗口查看、结果上限、编辑前 lint 守卫。
3. **Agentless**（2407.01489，FSE 2025 杰出论文）——分层定位 + 采样 + 复现/回归测试选择 + 多数投票。消融表是复现测试价值的最直接证据。
4. **AutoCodeRover**（2404.05427，ISSTA 2024）——AST 级搜索 API + 频谱故障定位。
5. **CodeAct**（2402.01030，ICML 2024）+ **OpenHands**（2407.16741，ICLR 2025）——代码即动作、事件流架构、沙箱运行时。
6. **LocAgent**（2503.09089，ACL 2025）——图引导定位，小模型 14% 成本追平前沿模型。
7. **CodeMonkeys**（2501.14723，ICML 2025）——test-time scaling 的串行/并行两轴，选择质量是瓶颈。
8. **Trae Agent**（2507.23370）——生成/剪枝/选择三段式，选择器自己写测试并执行。
9. **TestPrune**（2510.18270）——回归测试精选，既提分又省钱。
10. **Diff-XYZ**（2510.12487）——编辑格式对照实验。
11. **SWE-bench Pro**（2509.16941）+ **The SWE-Bench Illusion**（2506.12286）——基准饱和与污染的证据。
12. **Agentic Software Issue Resolution: A Survey**（2512.22256，242 篇）——2025 年综述，查漏用。

**训练与验证器**：SWE-Gym（2412.21139，首个开放的 policy + verifier 配方）、SWE-smith（2504.21798，环境稀缺才是瓶颈）、SWE-RL（2502.18449，无执行的 diff 相似度奖励）、R2E-Gym（2504.07164，混合验证器优于单一）、Kimi-Dev（2509.23045，先在廉价流水线练技能再迁移到 agent）。

**检索与上下文**：CORE-Bench（2606.11864）、Is Grep All You Need?（2605.15184）、Codebase-Memory（2603.27277）、LARGER（2605.16352，先 grep 锚点再图扩展）、cAST（2506.15655，按 AST 切块）、SweRank（2505.07849）。

**记忆**：SWE-Exp（2507.23361）、子任务级记忆（2602.21611）、SWE Context Bench（2602.08316）、Agent Workflow Memory（2409.07429）。

**基础范式**：ReAct（2210.03629）、Reflexion（2303.11366）、LATS（2310.04406）、Tree of Thoughts（2305.10601）。

**工程文章（比论文更影响日常决策）**

- Anthropic《Building effective agents》（2024-12）——workflow 与 agent 的分类法。
- Anthropic《Effective context engineering for AI agents》（2025-09）、《Writing tools for agents》——截断、输出格式枚举、可操作的错误信息。
- Anthropic《Raising the bar on SWE-bench Verified》（2024-10）——极简 scaffold 的原始出处。
- Cognition《Don't Build Multi-Agents》（2025-06）。
- HumanLayer《12-factor agents》——own your control flow、own your context window。
- Cursor《Improving agent with semantic search》（2025-11）——唯一公开的 embedding 增益对照数据。
- Diagrid《Checkpoints are not durable execution》——对 LangGraph 持久化能力的批评。
- OpenAI《Why we no longer evaluate SWE-bench Verified》（2026）。
- OWASP Top 10 for Agentic Applications 2026——ASI05（意外代码执行）和 ASI06（记忆/上下文投毒）最相关。

---

## 六、未核实与存疑

- 厂商自报的 SWE-bench Verified / Pro 数字（Fable 5、Mythos、GPT-5.6 等）来自厂商页面或聚合站，harness 各异，不可横向比较。
- Cursor 当前索引实现（Merkle 树 + embedding + turbopuffer）在 2026 年文档里已不再明说；Amp、Continue 的检索栈无一手资料。
- Windsurf「召回率提升 200%」、Augment「MCP 让合作 agent 提升 70%+」、代码图厂商「省 121 倍 token」均为自报，无方法学。
- Claude Code 和 Cursor 各自宣称的「权限提示减少 84%」均为二手来源。
- MCP 2026-07-28 修订是 RC 还是正式版，来源说法不一。
- 记忆类论文的基线 scaffold 各不相同，+2–7 个百分点应视为量级而非精确值。
- Nemotron-CORTEXA 的具体数字无法从公开渠道取得（OpenReview 受限）。

---

## 七、主要来源

**榜单与 scaffold**：[swebench.com](https://www.swebench.com/)、[tbench.ai TB4](https://www.tbench.ai/leaderboard/terminal-bench/4)、[SWE-bench/experiments](https://github.com/SWE-bench/experiments)、[Anthropic SWE-bench scaffold](https://www.anthropic.com/engineering/swe-bench-sonnet)、[Epoch harness](https://epoch.ai/benchmarks/swe-bench-verified)、[mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent)、[SWE-agent 维护状态](https://swe-agent.com/latest/)、[live-SWE-agent](https://arxiv.org/abs/2511.13646)、[OpenHands SDK 论文](https://arxiv.org/html/2511.03690v1)、[Trae Agent](https://arxiv.org/html/2507.23370)、[Codex 沙箱](https://learn.chatgpt.com/docs/sandboxing)、[Claude Code 权限模式](https://code.claude.com/docs/en/permission-modes)、[Claude Code 沙箱](https://code.claude.com/docs/en/sandboxing)、[Jules critic](https://developers.googleblog.com/en/meet-jules-sharpest-critic-and-most-valuable-ally/)、[Amp handoff](https://ampcode.com/news/handoff)、[Augment harness 重建](https://www.augmentcode.com/blog/auggie-cli-harness-rebuild-53-percent-cheaper)。

**检索与验证**：[Agentless](https://arxiv.org/html/2407.01489v2)、[LocAgent](https://arxiv.org/html/2503.09089)、[SweRank](https://arxiv.org/html/2505.07849)、[CORE-Bench](https://arxiv.org/html/2606.11864)、[Codebase-Memory](https://arxiv.org/html/2603.27277v1)、[Is Grep All You Need](https://arxiv.org/html/2605.15184v1)、[Cursor semsearch](https://cursor.com/blog/semsearch)、[Augment 工程师访谈](https://jxnl.co/writing/2025/09/11/why-grep-beat-embeddings-in-our-swe-bench-agent-lessons-from-augment/)、[Claude Code 放弃 RAG](https://newsletter.pragmaticengineer.com/p/building-claude-code-with-boris-cherny)、[CodeMonkeys](https://arxiv.org/html/2501.14723v1)、[TestPrune](https://arxiv.org/html/2510.18270)、[TDAD](https://arxiv.org/html/2603.17973)、[AgentLens](https://arxiv.org/abs/2605.12925)、[Diff-XYZ](https://arxiv.org/html/2510.12487v2)、[OpenAI apply_patch 指南](https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide)、[CodeRabbit 现场数据](https://arxiv.org/html/2607.03316v2)、[Agent-as-a-Judge](https://arxiv.org/abs/2410.10934)、[Large Language Monkeys](https://arxiv.org/abs/2407.21787)。

**记忆**：[SWE-Exp](https://arxiv.org/html/2507.23361)、[子任务级记忆](https://arxiv.org/html/2602.21611)、[SWE Context Bench](https://arxiv.org/html/2602.08316)、[Memory Transfer Learning](https://arxiv.org/html/2604.14004)、[DreamBench-SWE](https://arxiv.org/html/2608.20664)。

**工程基建**：[LangGraph 1.0](https://www.langchain.com/blog/langchain-langgraph-1dot0)、[LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Diagrid 对持久化的批评](https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows)、[Temporal LangGraph 插件](https://temporal.io/blog/temporal-langgraph-plugin-durable-execution)、[12-factor agents](https://github.com/humanlayer/12-factor-agents)、[SWE-ReX](https://github.com/SWE-agent/SWE-ReX)、[OTel GenAI 约定](https://opentelemetry.io/docs/specs/semconv/gen-ai/)、[Langfuse 自托管](https://langfuse.com/self-hosting)、[AG-UI](https://docs.copilotkit.ai/agentic-protocols/ag-ui)、[Vercel UI stream](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol)、[Vercel tool approvals](https://ai-sdk.dev/docs/agents/tool-approvals)、[AWS Dogwood](https://www.infoq.com/news/2026/08/aws-dogwood-agent-policy/)、[Harbor](https://github.com/harbor-framework/harbor)、[Inspect AI](https://inspect.aisi.org.uk/)、[OWASP Agentic Top 10](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)。

**综述与方法论**：[Cognition](https://cognition.com/blog/dont-build-multi-agents)、[Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)、[Anthropic writing tools](https://www.anthropic.com/engineering/writing-tools-for-agents)、[OpenAI 退役 Verified](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)、[SWE-bench Pro](https://arxiv.org/html/2509.16941v2)、[SWE-Bench Illusion](https://arxiv.org/abs/2506.12286)、[Issue-resolution 综述](https://arxiv.org/abs/2512.22256)。
