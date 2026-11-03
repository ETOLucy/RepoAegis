# 调研：什么样的 issue 适合交给"打标签就开工"的 agent

> 本报告 2026-10-24 写在调研会话的笔记目录（原文件名 `research-task-boundary.md`），2026-10-25 原样搬进 `docs/调研/`，正文没有再复核，行号和版本以当时为准。据此的决定见 [接收哪些 issue](../接收哪些issue.md) 和 [项目边界](../项目边界.md)。

调研日期：2026-10-24。所有文档都是当天打开的版本，这些产品更新很快，结论有时效性。

标注约定：
- 【原意】= 我打开了一手来源，下面是对原文的转述（引号里是原文，每处不超过 15 个英文单词）。
- 【推断】= 我自己的理解或归纳，原文没这么说。
- 【待验证】= 只看到二手说法，或者没能直接核对原文。

---

## 【过程】

1. **GitHub Copilot**：先在 `github/docs` 仓库的文件树里定位，发现"coding agent"已改名"cloud agent"（云端 agent），旧路径会重定向。把整个仓库（commit `02c25d1`）下载到本地，读了最佳实践页、负责任使用说明（Application Card）、产品概念页、API 页、自动化里的"置信度"页，并全文搜索"clarif / ask / waiting_for_user"等词，看 Copilot 遇到说不清的任务会怎么做。另外查了 Copilot 官方反馈仓库里一条相关的功能请求。
2. **OpenAI Codex**：`developers.openai.com/codex` 现在整体跳转到 `learn.chatgpt.com`；`openai/codex` 仓库的 `docs/` 只剩指向网站的空壳。下载了网站提供的整本手册 `codex-manual.md`（约 3 MB）搜索；OpenAI 官网的《How OpenAI uses Codex》网页对脚本返回 403，改为下载它的 PDF 原文阅读。
3. **Devin**：下载 `docs.devin.ai` 的 `llms.txt` 和 `llms-full.txt`（全站文本），读了"When to Use Devin""Good vs. Bad Instructions""Best Practices"三页，在全站文本里搜"confidence"找到 Devin 2.1 的置信度机制；另读了 Cognition 的长文《Coding Agents 101》原网页并核对了两处引文。
4. **OpenHands**：读了 `docs.openhands.dev` 的"When to Use OpenHands""Prompting Best Practices""Issue Triage and the ready-for-dev Gate""Issue to Pull Request Automation""Software Factory"五页；又去 `OpenHands/OpenHands` 和 `OpenHands/software-agent-sdk` 仓库核对文档里说的"就绪检查"脚本，发现文档和仓库现状不一致（见第 4 节）。
5. **Google Jules**：读了 `jules.google/docs` 的 Running tasks、Planning、FAQ、Errors 和三篇更新日志。
6. **SWE-bench Verified**：OpenAI 官网原文对脚本返回 403，改读 Internet Archive 2026-01-01 的存档；从文中链接下载了**标注说明 PDF 原文**和**标注结果数据包**，在 WSL 里用 Python 重新统计了一遍，和文章公布的百分比对上了。
7. **实证研究**：用 arXiv 接口搜"AIDev"，找到约 50 篇基于 AIDev 数据集的论文；下载并读了其中 4 篇与"按任务类型的合并率"直接相关的原文（另 1 篇只看了摘要）。

---

## 1. GitHub Copilot cloud agent（原 coding agent）

来源文件都在 `github/docs` 仓库，commit `02c25d16`：
- A = `content/copilot/tutorials/cloud-agent/get-the-best-results.md`（页面标题 *Best practices for using GitHub Copilot to work on tasks*）
- B = `content/copilot/responsible-use/agents.md`（Application Card）
- C = `content/copilot/concepts/copilot-surfaces/copilot-on-github.md`

**什么样的 issue 算"写好了"**【原意】
A 第 27–38 行说，任务越清楚、范围越窄，效果越好。理想的任务有三样东西：一是把要解决的问题或要做的事说清楚；二是完整的验收标准，也就是"什么样算做好了"（比如要不要带单元测试）；三是指出该改哪些文件。文中还建议把分配给 Copilot 的 issue 当成一段提示词来看，问问自己它能不能直接拿去当 AI 的提示词用。B 第 284 行把同样三点又写了一遍。

**适合交给它的类型**【原意】
A 第 42 行建议先从简单任务开始，例子有：修 bug、改界面功能、提高测试覆盖率、更新文档、改善无障碍、处理技术债。

**建议人自己做、不交给它的四类**【原意】（A 第 44–62 行，原文是列表）
- 复杂、范围大的任务：需要跨仓库知识和测试的大范围重构；需要理解依赖和老代码的复杂问题；需要很深领域知识的任务；业务逻辑很重的任务；需要保持设计一致性的大改动。
- 敏感、关键的任务：线上关键问题；涉及安全、个人身份信息（PII）、会影响认证的任务；故障应急。原文："Tasks involving security, personally identifiable information, authentication repercussions"。
- 含糊的任务：需求含糊、开放式、需要边摸索不确定性边找方案的任务。
- 学习型任务：开发者想借这件事深入弄懂代码的情况。

**GitHub 自己在安全问题上有两种说法**【原意 + 推断】
B 第 85 行的"预期用途"里却写着，cloud agent 可以处理安全相关修复、依赖升级和有针对性的重构。C 第 61 行也说可以把代码扫描告警（code scanning alerts，由 GitHub 静态分析工具发现的具体漏洞）在"安全战役"里批量指派给 Copilot。
【推断】两种说法放在一起看，GitHub 的意思大概是：**有工具能指出具体位置、也能复查的安全告警**可以交给它；**涉及认证、隐私、需要人做安全判断的开放问题**不要交。这是我的归纳，原文没把两者放在一起解释。

**硬性限制**【原意】
C 第 133–142 行：一次只能改一个仓库；一个任务只开一个 PR；每次会话最长 59 分钟，不能延长，任务太复杂会超时停掉，建议拆成小任务。

**遇到不清楚或做不了的任务，Copilot 会怎么做**
- 【原意】B 第 118 行（性能一节里 cloud agent 的"输出格式"步骤）：第一轮跑完后，它会更新 PR 描述，**可能附上它访问不到的资源，以及解决建议**。之后人在 PR 里评论或 @copilot，它再接着改。
- 【原意】API 文档（`content/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-via-the-api.md` 第 89 行）列出的任务状态里有一个 `waiting_for_user`（等用户回应），但文档没解释什么情况下会进入这个状态。
- 【原意】我在 Copilot 文档里**没找到**"cloud agent 拿到含糊 issue 会先提问再动手"的说明。"会问澄清问题"只出现在 Copilot CLI 的计划模式里，那是本地交互场景，不是指派 issue 的场景。
- 【待验证·社区来源】Copilot 官方反馈仓库 `copilot-coding-agent/user-feedback` 第 84 号 issue（2025-08-30）里，有用户请求"让 coding agent 开工前先问澄清问题"。回复者 `nhu-do` 说团队也在考虑这件事，并指出当时在 PR 以外的地方 @Copilot 不起作用。回复者是不是 GitHub 员工我没有核实。
- 【推断】截至今天的文档，Copilot 的默认做法看起来是"照着 issue 做，把做不到的写进 PR 描述，等人在 PR 里纠正"，而不是"开工前停下来提问"。
- 补充【原意】Copilot 有一套"理由 + 置信度（高/中/低）+ 审批"机制（`content/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/about-automation-rationale-and-approvals.md`），但第 105–107 行写明它**只管 agent 对 issue 属性的改动**（加标签、设类型、指派、关闭等），**不管开 PR、推代码**。所以它不是"开工前自评能不能做成"的机制。第 141 行还特别说明，审批只是流程上的方便，不是安全边界。

---

## 2. OpenAI Codex

**任务大小**【原意】
来源：《How OpenAI uses Codex》PDF（`https://cdn.openai.com/pdf/6a2631dc-783e-479b-b1a4-af0cfbd38630/how-openai-uses-codex.pdf`），第 11 页"Best practices"一节的"Start with Ask Mode"条。原话："well-scoped tasks that would take you or a teammate about an hour"，后面接着说，或者是几百行代码就能实现的量。同一条还说，随着模型变强，能接的任务会变大。大改动建议先用 Ask 模式出实施计划，再切到 Code 模式去做。
同页另一条：提示词最好写得像一个 GitHub issue，带上文件路径、组件名、diff、文档片段。
【待验证】这份 PDF 是 2025 年 Codex 还在研究预览期时发的，现在的手册里我没再看到"一小时 / 几百行"这句话。

**OpenAI 内部实际拿 Codex 做什么**【原意】
同一 PDF 的目录列了 7 类用途：读懂代码、重构和迁移、性能优化、提高测试覆盖率、加快开发、保持专注、探索构思。注意"重构和迁移"在 OpenAI 这里是**推荐**用途，和 Copilot 正好相反（见文末"分歧"一节）。

**现行官方最佳实践**【原意】
来源：`https://learn.chatgpt.com/docs/codex-manual.md`（原地址 developers.openai.com/codex/learn/best-practices 已跳转），"Best practices"一节，文件第 2470–2510 行附近。
- 提示词默认带四样东西（第 2480–2487 行）：**Goal**（要改什么、做什么）、**Context**（相关文件、文档、例子、报错）、**Constraints**（要守的规范、架构、安全要求）、**Done when**（完成前什么必须成立，比如测试通过、行为改变、bug 不再复现）。原文说这样能让 Codex 不跑偏、少做假设。
- "Plan first for difficult tasks"（第 2498 行起）：任务复杂、含糊或难以描述时，先让 Codex 做计划再写代码。计划模式会让它收集上下文、**问澄清问题**，然后再定计划。
- 【推断】这是交互场景下的建议。Codex 的 GitHub Action（手册第 38735 行起，`openai/codex-action`）跑在 CI 里，用的是非交互的 `codex exec`，没有人能回答它的问题。和 RepoAegis 一样，在这种场景下"先问再做"用不上。
- 手册里讲 Linear 集成的部分（第 35586 行附近）说，请求含糊时会退回到最近用过的环境。这是选运行环境的规则，和需求是否清楚无关，只记一笔。

**遇到不清楚的任务会怎么做**【原意 + 推断】交互模式下靠计划模式提问（见上）。云端 / GitHub 场景下，我**没找到**官方说明"含糊任务会拒绝或留言"。

---

## 3. Devin

**适合什么、多大**【原意】
- `https://docs.devin.ai/essential-guidelines/when-to-use-devin.md`，"Evaluating Tasks for Devin"一节：判断一个任务适不适合，问自己三件事：能不能写出清楚的成功标准（有测试、CI、可验证的结果最好）；上下文够不够；拆开会不会更好。经验法则："if a task would take you three hours or less"，Devin 多半能做；更长的就拆成几个会话。
- 同页"Pre-Task Checklist"：好任务要有清楚的起点和终点，加上明确的成功标准（测试通过、照着已有写法、CI 变绿）。会话尽量保持在 XS/S/M 大小。
- `https://docs.devin.ai/use-cases/best-practices.md`（企业场景）：理想的任务是人工 **90 分钟以内**能做完、初级工程师难度、彼此隔离且可逐步推进、结果客观可验证、向后兼容可以单独合并。"简单、定义清楚"的任务可靠性高；"复杂的全新功能"可靠性低。迁移、重构、现代化改造、技术债是好用途，**前提是能拆成一片一片逐步做**；原文明确不推荐"整个仓库一次性迁移完"那种。
- `https://docs.devin.ai/essential-guidelines/good-vs-bad-instructions.md`，"Bad"一节有三个反例：开放式的"找出代码问题并修掉"（没有成功标准，不知道何时算完）；纯主观的"让落地页好看点"；又大又模糊的"给应用重建微服务架构"（需要大量架构决策和权衡）。
- 【原意，WebFetch 摘要后核对了两句原文】Cognition 的长文《Coding Agents 101》（`https://devin.ai/agents101`）说，回报最高的是"medium-to-large tasks (typically 1-6 hours of work)"；安全方面建议"avoiding giving access to production services entirely"（核对过原网页）。
- 【原意】`https://docs.devin.ai/llms-full.txt` 中"How does Devin fit into my existing SDLC?"一节（文件第 3228 行起）：说 Devin 擅长大规模迁移，客户用它拆过几百万行的老系统。这是营销口径，和上面"不要一次性全仓迁移"并不矛盾：前提都是切成小片。

**开工前自评 / 置信度**【原意】
`https://docs.devin.ai/llms-full.txt` 发布说明 2025-05-15 "Devin 2.1"条（文件第 12810–12825 行）：
- Devin 2.1 加了置信度评分（绿 / 黄 / 红），表示它觉得自己能做成的可能性。原文开头承认："coding agents can be overconfident"。
- 会话里有三个时点会给出置信度：会话开始时、做完计划后、回答关于代码的问题时。
- 不是绿色时，它会**停下来等用户批准计划**再继续；绿色就自动往下做。
- 官方称置信度和成功率高度相关，但没给数字。【待验证】
- 通过 Linear / Jira 集成，可以**不开会话**就批量给很多工单打置信度，先挑高分的交给 Devin。

另外【原意】Linear 集成页（`llms-full.txt` 第 4949 行起 "# Linear"）：工单交给 Devin 时如果没带剧本标签（playbook，预先写好的一套做法），默认用 `!plan` 剧本，也就是默认先出计划。

---

## 4. OpenHands

**"When to Use OpenHands"**【原意】（`https://docs.openhands.dev/openhands/usage/essential-guidelines/when-to-use-openhands.md`）
- 简单任务（最适合）：加函数、给已有代码写单元测试、修有清楚报错的简单 bug、格式和风格修正、加文档注释、简单重构（改名、抽函数）、改配置。
- 中等任务（适合，可能要多轮）：加接口、给已有模块加功能、跨多个文件的调试、迁移到新写法、写集成测试、有明确指标的性能优化、配 CI/CD。
- 复杂任务（要拆）：跨很多文件的大重构、架构改动、复杂业务逻辑、多服务集成、原因不明的性能问题、**安全审计**、框架或大版本依赖升级。
- "Poor Fit Scenarios"表：需求模糊（不知道"做完"指什么）、探索性工作、**高度敏感的代码**（容错为零，必须有人审）、需要组织内部知识、视觉设计。
- 危险信号列表："Make it look better"、"Figure out what's wrong"、"Rewrite everything"、"Do what makes sense"，以及没经审查就改生产基础设施。
- "Decision Framework"是一棵五步判断树：任务定义清楚了吗 → 有清楚的成功标准吗 → 范围可控吗（原文写的是 **少于 100 行代码**）→ 代码库里有可参照的例子吗 → 能验证结果吗。五个都是"是"，才算好候选。
- `prompting-best-practices.md`"Characteristics of Good Prompts"：具体、指明位置、范围合适（一个功能，一般不超过 100 行）。

**和 RepoAegis 几乎同款的流程**【原意】
`https://docs.openhands.dev/openhands/usage/agent-canvas/prebuilt/issue-to-pr.md`：轮询 issue 跟踪器里带触发标签（默认 `openhands`）的 issue → 去重（同一个 issue 只处理一次）→ 每个 issue 起一个 agent 会话 → 克隆、建分支、改、跑测试、开 PR（默认草稿）→ 回 issue 留言贴 PR 链接。想重跑就"撤标签再打上"。安全一节提醒：issue 内容是不可信的，agent 把它当作要实现的任务，而不是可信的指令。

**"需求是否清楚"的结构化检查**【原意 + 文档与代码不一致】
`https://docs.openhands.dev/overview/issue-lifecycle.md` 描述了 OpenHands 自己仓库的 `ready-for-dev`（可以开发了）标签：
- bug 类 issue（主仓库）三条都要满足：`### Steps to Reproduce` 有内容，且指明用哪种方式运行；`### Actual Behavior` 里嵌了截图或视频；`### Acceptance Criteria` 里至少有一条 `- [ ]` 勾选项。
- SDK 仓库的 bug：`### Actual Behavior` 里要有可运行的命令或代码片段（python/pytest/uv/pip），外加至少一条验收勾选项。
- 新功能类：`### Desired Behavior` 有内容，外加至少一条验收勾选项。
- 文档说可以用 `.github/scripts/check_issue_readiness.py` 在本地跑这个检查。
- **但是**我去两个仓库的 main 分支核对：这个脚本找不到；对应的工作流 `.github/workflows/issue-readiness-check.yml` 现在叫 "Ready-for-dev Label Policy"，SDK 仓库那份开头注释写着它只检查打标签的人有没有写权限，"not issue content"。也就是说，**现在的代码只看谁打的标签，不看 issue 内容**。
- `software-factory.md` 里，"可以开发了"的判断交给一个 triage agent（分诊 agent），由它"排优先级、查依赖、定好验收标准"之后再打 `ready-for-dev`，开发 agent 只认这个标签。
- 【推断】OpenHands 的做法是"人或分诊 agent 先把 issue 整理到能做的状态，开发 agent 只接已就绪的"。文档里那套按标题检查内容的规则可能已经撤掉或换了地方，现状【待验证】。

---

## 5. Google Jules

来源：`https://jules.google/docs/`（`running-tasks.md`、`review-plan.md`、`faq.md`、`errors.md`、更新日志）。

- 【原意】"Write a clear prompt"一节：提示词要具体、有范围。好的例子：给 `fetchUserProfile` 加加载动画；修提交反馈表单时的 500 错误；给某个 hook 写 JSDoc。要避免的例子："Fix everything""Optimize code""Make this better"。
- 【原意】同节一句："If Jules needs more clarity, it will ask for feedback before writing code." 也就是它需要更多信息时，会在写代码前问。
- 【原意】`review-plan.md`：每个任务都先出计划，人可以在聊天框里改计划、回答它的问题；但如果人走开，计划会按计时器**自动批准**，"no need to babysit"。
- 【原意】更新日志 2026-01-26："Planning Critic"（计划评审员）。对不需要人介入、会被自动批准的计划，先由第二个 agent 审一遍；官方称任务失败率因此降低 9.5%（官方自报数字，没有方法说明）。
- 【原意】从 GitHub issue 启动：给 issue 打 `jules` 标签（不区分大小写），Jules 会在 issue 里留言，做完后贴 PR 链接。
- 【原意】`errors.md`"Common causes of failure"：最常见的失败原因里有一条是"提示词太模糊或太宽"。FAQ 说任务失败时会自动重试，最常见的原因是安装脚本坏了或提示词模糊。
- 【未找到】Jules 文档里**没有**专门讲"哪些类型的任务别交给它"（安全、重构等）的内容。

---

## 6. SWE-bench Verified 的人工标注准则（"issue 写清楚没有"的现成打分表）

**来源**
- 文章：OpenAI《Introducing SWE-bench Verified》（2024-08，`https://openai.com/index/introducing-swe-bench-verified/`，我读的是 Internet Archive 2026-01-01 存档）。
- **标注说明原文 PDF**：`https://cdn.openai.com/introducing-swe-bench-verified/swe-b-annotation-instructions.pdf`（5 页，以下"第 X 行"指 pdftotext 转出来的行号）。
- **标注数据**：`https://cdn.openai.com/introducing-swe-bench-verified/swe-bench-annotation-results.zip`，里面有 `ensembled_annotations_public.csv`（1699 条，3 人合并后的结果）和 `samples_with_3_annotations_public.csv`（每个标注员各自的原始标注）。

**标注员拿到的设定**【原意】（PDF 第 43–50 行）
想象自己是一名有经验的工程师，被要求提一个 PR 解决这个 issue。代码库可以随便看，issue 也看得到，**但不能要求对方澄清**，只能凭现有信息干活："you are not able to ask for clarification"。另外只看 issue 正文，不看下面的讨论，不点外链（第 37–39 行）；后来补充说明，可以假定 issue 里嵌的图片看得见（脚注 1）。
【推断】这正是 RepoAegis 的处境：一次运行，中途没有人可以问。所以这套打分表几乎可以直接搬过来用。

**问题 1.1：issue 写得够清楚吗（0–3 分，越高越糟）**【原意，转述】（PDF 第 52–63 行）
- 0 分：issue 写清楚了，怎样才算成功解决一目了然。
- 1 分：有些空白要自己补，但有一个合理的理解，能看出成功的解法该是什么样。
- 2 分：issue 含糊，可以有不同理解，看不出成功的解法该是什么样。
- 3 分：没有更多信息的话，几乎不可能明白要你做什么。
每个分数都配了一个真实 issue 当例子（pylint #5201 / sympy #18030 / scikit-learn #14520 / pylint #5743）。打完分还要写至少 100 字的理由，并点出具体文件名、函数名或代码行（问题 1.2）。

**问题 2.1：测试合不合理**【原意，转述】（PDF 第 87–123 行）
用来判卷的测试（`FAIL_TO_PASS`，指修复前会失败、修复后应该通过的那些测试），会不会把其他合理的解法错判成失败：
- 0 分：测试能覆盖所有可能的解法。
- 1 分：能覆盖大多数正确解法，少数不寻常的解法可能被漏判。
- 2 分：测试能用，但一些完全合理的解法可能被判错。
- 3 分：测试太窄或太宽，或者测的东西和 issue 说的不是一回事。
说明里点出最常见的毛病是：测试依赖了原 PR 里新起的函数名、变量名或报错文字，而这些在 issue 里根本没提（第 97–102 行）。

**问题 3.1：难度（只作参考，不用来筛选）**【原意，转述】（PDF 第 134–155 行）
估计一名有经验、并花了几小时熟悉代码库的工程师，从读懂问题、想出方案到写完代码要多久：
- 少于 15 分钟（比如给函数加几个断言）
- 15 分钟到 1 小时（需要动点脑子的小改动）
- 1 到 4 小时（大改一个函数，或改多个文件）
- 超过 4 小时（很偏门、要大量调研，改动超过 100 行）
第 142–144 行特别说明：如果 issue 原本太含糊，打难度分时假设"要做什么"已经澄清，只剩"怎么做"留给工程师。

**其他（问题 3.3–3.5）**【原意】还有没有别的大问题（0 否 / 1 是，加自由文字说明），以及标注员对自己整份答卷的把握（1–5 分）。

**筛选规则和结果**【原意】（存档文章 "Annotation Criteria" 一节 + 我对数据包的复算）
- 每个样本由 3 名标注员各自打分，**取三人中最严重的那个分**（文章说这是有意偏保守）。
- 1.1 或 2.1 合并后 ≥2 分，或者被标了"其他大问题"的样本，一律剔除。等于说三个人里只要有一个人觉得有问题，就剔掉。
- 93 名有 Python 经验的开发者标了 1699 个样本。38.3% 被判为 issue 描述不清（≥2），61.1% 被判为测试不合理，合计剔除 68.3%。剩下的 539 条里，优先保留难度高的，再随机凑够 500 条，就是 Verified。
- 我在 WSL 里用 `ensembled_annotations_public.csv` 复算，"描述不清"四档的条数是：0 分 396、1 分 653、2 分 542、3 分 108；≥2 占 38.26%，测试分 ≥2 占 61.15%，剔除 68.28%，和文章一致。难度四档：<15 分钟 417、15 分钟–1 小时 906、1–4 小时 329、>4 小时 47。
- 【推断】这份数据说明，即使是热门 Python 开源项目里**已经被合并修复**的真实 issue，按"不能追问"的标准，三人里至少一人判为"说不清"的也接近四成。所以要预期：真实仓库里会有相当比例的 issue 过不了"写清楚没有"这一关。

---

## 7. 实证研究：agent 开的 PR 按任务类型的合并率

所有数字都来自 AIDev 数据集（2025 年中从 GitHub 采集，覆盖 Codex、Devin、Copilot、Cursor、Claude Code 五个 agent 开的 PR）。共同的局限我放在本节末尾。

**(a) Li, Zhang, Hassan,《The Rise of AI Teammates in SE 3.0》，arXiv 2507.15003v1（2025-07-20），AIDev 原始论文**【原意】
- 用 GPT-4.1-mini 按 Conventional Commits（一种约定俗成的提交分类：feat 新功能、fix 修 bug、docs 文档、refactor 重构、perf 性能等）把 PR 自动分成 11 类，只看 500 星以上的仓库（AIDev-pop 子集）。
- Finding #2：各家 agent 的接受率（被合并的比例）都明显低于人，差距主要出在 feat、fix、perf。三家总体：Codex 64%、Devin 49%、Copilot 35%，比人低 15–40 个百分点。
- Finding #3：文档类是强项。Codex 的 docs 类 PR 接受率 88.6%、Claude Code 85.7%，都高于人的 76.5%。作者的解释是文档改动功能风险小。
- Table 4：Copilot 的 PR 里 42.2% 是 fix，比其他 agent 都高；作者推测这是因为 Copilot 和 GitHub issue 绑得紧，常被指派具体的 bug。

**(b) Pinna, Gong, Williams, Sarro,《Comparing AI Coding Agents: A Task-Stratified Analysis of Pull Request Acceptance》，arXiv 2602.08915v2，MSR '26**【原意】
- 口径：只保留已关闭、仓库是 MIT 或 Apache-2.0 许可、并且至少有一条他人评审或评论的 PR，共 7156 条。这个筛选会抬高接受率（见末尾局限）。
- 摘要和 Table 2：任务类型是影响接受率的主要因素。各类平均接受率：chore 84.0%、docs 82.1%、style 78.1%、ci 75.0%、build 72.5%、refactor 71.2%、feat 66.1%、fix 66.0%、test 61.5%、perf 55.4%。最高和最低差 29 个百分点。docs 比 feat 高约 16 个百分点，这个差距超过了大多数任务上 agent 之间的差别。
- 分 agent 看，fix 类差别最大：Codex 83.0%、Cursor 80.4%，Devin 45.6%。文档类各家差别小（都在 79% 以上）。

**(c) Siddiq 等,《Security in the Age of AI Teammates: An Empirical Study of Agentic Pull Requests on GitHub》，arXiv 2601.00477v2（2026-09-03 修订）**【原意】
- 先用关键词筛、再人工确认，得到 1293 个和安全相关的 agent PR，约占 4%。agent 做的安全工作大多是"加固类"（测试、文档、配置、错误处理），而不是直接修漏洞。
- 第 4.4 节：安全相关 PR 的合并率 61.50%，非安全的 77.33%；评审时长中位数 3.92 小时，非安全的只有 0.11 小时（p<0.001）。也就是说，人审 agent 的安全 PR 明显更谨慎。
- Table 5：Copilot 的安全 PR 里有 81.2% 关联了已有 issue（"由人圈定的安全问题"），合并率 49.6%，比它自己的非安全 PR 低 5.9 个百分点；Claude Code 差 15.0 个百分点；Codex 几乎没差别（−0.8）。作者把 Codex 的情况解释为改得更窄、更挑。
- 摘要：PR 被拒和"复杂、啰嗦"的关系，比和"是不是安全话题"的关系更强。

**(d) Nakashima 等,《Why Agentic-PRs Get Rejected: A Comparative Study of Coding Agents》，arXiv 2602.04226v1**【原意】
- 人工看了 654 个被拒的 PR，找出 7 种只在 agent PR 里出现的被拒原因，其中包括"不信任 AI 生成的代码"。
- 67.9% 的被拒 PR 里，评审者**根本没写为什么拒**，原因无从判断。
- Table 1 的总体接受率（口径和 (a) 不同）：Codex 85.8%、Devin 55.5%、Copilot 55.0%、Cursor 74.6%、Claude Code 71.3%、人 82.6%。

**(e) 只看了摘要的**：Rahman 等，arXiv 2602.02345（MSR '26）说 Codex 在多数任务类别上接受率都高，Copilot 的 PR 引发的评审讨论最多。arXiv 上另有约 45 篇基于 AIDev 的论文（标题见检索结果，比如 2602.00164 讨论 fix 类 PR 为什么没被合并、2606.13468 讨论 fix 被拒的原因），我没有读，不作结论。

**共同局限**【原意 + 推断】
- **"合并"不等于"做对了"**，(b) 的效度一节自己也这么说：合并的 PR 也可能有 bug。反过来，没合并也不一定是做错了，(d) 的被拒原因里就有"项目不活跃""改用了别人的方案"等。
- **口径不一**：同样是 Copilot，(a) 算出 35%，(d) 算出 55.0%。区别在分母算不算还开着的 PR、算不算草稿 PR、有没有要求"至少有人评审过"。不同论文的数字不能直接拿来比。
- 任务类型是 LLM 根据 PR 标题和正文**自动分**的，不是人工标的。
- 数据是 2025 年中采的，很多 agent 当时刚发布两个月；之后模型和产品都换过好几代。
- 都是观察性研究，不能说明因果。比如 docs 合并率高，可能是因为任务简单，也可能是评审对文档放得松，(b) 自己也提到了这种可能。

---

## 对 RepoAegis 的启示

### 各家都同意的部分（一致点）

【原意，汇总】下面每一条都至少有 3 家明确写过：

1. **要能验证**。Copilot 要"验收标准"，Codex 要"Done when"，Devin 要"清楚的成功标准"，OpenHands 判断树里有"能不能验证结果"。四家都把"有测试或可检查的结果"当作适合交给 agent 的核心条件。
2. **适合**：有明确报错或复现步骤的 bug、补测试、写或改文档、小范围的配置和样式修改、按已有写法照着做。实证数据也对得上：docs、chore 这类合并率最高（(a)(b)）。
3. **不适合：需求含糊、开放式、主观的任务**。Copilot、Devin、OpenHands、Jules 四家都举了几乎一样的反例（"让它更好""把所有问题都修了""看看哪里坏了"）。
4. **不适合：没法切小的大范围重构、架构改动**。Copilot 说别交；Devin、OpenHands、Codex 说要拆成能单独验证、单独合并的小块。
5. **大小上限**：几家的经验值落在"人工一小时左右到三小时以内"（Codex 约 1 小时或几百行；Devin 企业指南 <90 分钟、入门页 ≤3 小时；OpenHands <100 行；Copilot 单次会话硬上限 59 分钟）。只有 Devin 的长文说 1–6 小时回报最高，但那是"多轮人机来回"的用法，不是一次跑完。

### 有分歧的地方

- **安全相关**。Copilot 最佳实践说"涉及安全、PII、认证"的任务人自己做，OpenHands 把"高度敏感代码""安全审计"列为不适合或要拆。但 GitHub 自己的 Application Card 又说 cloud agent 能做"安全相关修复"，还支持把代码扫描告警批量指派给 Copilot。
  【推断】可以这样调和：**扫描工具指出了具体位置、修完能再扫一遍复查**的告警修复，是"可验证的小任务"；**认证流程、权限模型、隐私处理这类需要人做安全判断的改动**，是"敏感任务"。实证 (c) 也显示，agent 的安全 PR 合并率更低、评审时间长很多（中位数 3.92 小时对 0.11 小时），审的人明显更不放心。
- **重构和迁移**。Copilot 偏保守（大范围重构别交）；Devin 和 OpenAI 内部把重构和迁移当成主力用途。分歧在"能不能切小"：几家都反对"一次性全仓改完"。RepoAegis 一次运行只处理一个 issue、开一个 PR，天然更接近"一片"。
- **遇到说不清的任务怎么办**，几种做法很不一样：
  - Copilot：文档里没写开工前会提问。照做，把做不到的写进 PR 描述，等人在 PR 里纠正。
  - Jules：先出计划，需要时会提问；没人理就按计时器自动批准，并另用一个"计划评审员" agent 审计划。
  - Devin 2.1：给置信度，不是绿色就停下等人批准；还能不开会话先给工单批量打分。
  - Codex：交互模式下用计划模式提问；在 CI 里跑没人可问。
  - OpenHands：前面加一道"可以开发了"的关口（人或分诊 agent 打 `ready-for-dev`），开发 agent 只接已就绪的。
  RepoAegis 的情况是"一次运行，中途没人回答"。上面这些里，"停下来等人"的做法用不上；能借鉴的是 **"开工前判断，判断不过就在 issue 里说明原因、提出具体问题，然后不改代码就结束"**，以及 OpenHands 那种"由人把关"的前置关口（RepoAegis 的"有权限的人打标签"已经是这道关口）。最终怎么选需要你拍板。

### "需求是否清楚"有没有可操作的标准

有，而且有现成的可以直接借用。【推断：下面是我把几份一手材料拼起来的结果】

- **打分尺子：SWE-bench Verified 的问题 1.1（0–3 分）。** 它的前提设定"不能要求澄清，只能凭 issue 正文干活"和 RepoAegis 完全一样。0 分、1 分可以做（1 分时要把自己补上的假设写进 PR 和 issue 汇报）；2 分、3 分不做，在 issue 里说明哪里不清楚。它还要求打分时点出具体文件名、函数名，这一条也可以照搬，让 agent 的判断有据可查。
- **检查清单**：几家的要素基本重合，可以合成一份。
  - 问题或目标是什么（Copilot 的问题描述、Codex 的 Goal、OpenHands 的 Desired Behavior）
  - bug 能不能复现：有没有复现步骤、实际行为（OpenHands 的 Steps to Reproduce / Actual Behavior）
  - 做完是什么样：验收标准或测试（Copilot 的验收标准、Codex 的 Done when、OpenHands 至少一条 `- [ ]` 勾选项）
  - 改哪里：文件或位置提示（Copilot、OpenHands 都提到，但 Copilot 说没有也行，agent 能自己搜）
  - 约束：不能动什么（Codex 的 Constraints）
- **测试那一问也有用**：SWE-bench 的问题 2.1 提醒，测试可能依赖 issue 里没提到的命名或报错文字。【推断】对 RepoAegis 来说可以换个角度用：agent 自己写的测试，不应该只是在验证它自己选的实现细节。
- **大小判断**：可以借用 SWE-bench 的难度四档（<15 分钟 / 15 分钟–1 小时 / 1–4 小时 / >4 小时），结合各家经验值，把"超过某一档就不做"定为边界。具体切在哪一档，需要你决定。
- **要注意**：SWE-bench 是 3 个人打分、取最严的那个，一个 agent 自己给自己打分没有这种保险。Devin 也承认 agent 容易高估自己，并声称置信度和成功率相关，但没公布数字【待验证】。所以如果要用"agent 自己判断"，需要你决定要不要加一道保守规则（比如拿不准就按 2 分处理）。我没找到有哪家公开过"agent 自评有多准"的数据。
- **实证数据能拿来干什么**：合并率数据（docs 高，feat、fix、perf、test 低，安全类更低、审得更久）可以用来给"适合程度"排序。但口径不一、数据较老，不适合当硬门槛的依据。
