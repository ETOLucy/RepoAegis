# 调研：worker 崩溃后的恢复与交接

> 2026-09-29。补充 `industry-survey.md` 3.10「编排框架与持久化」没有展开的一个问题：
> 任务做到一半，进程死了，怎么办。三路并行调研（持久执行平台、数据库任务队列、
> 编码 agent 系统），最后一节对照到 RepoAegis。

## 一、问题拆开

「进程死了怎么办」其实是三个独立的问题，业界每个系统都是分别回答的：

1. **怎么发现它死了**（崩溃检测）：心跳、租约超时、还是重启时自查。
2. **从哪里接着做**（检查点粒度）：整个任务重做，还是某一步重做，还是某一次调用重做。
3. **接着做的人拿到什么**（交接内容）：完整对话历史、摘要、结构化对象，还是磁盘上的文件和 git。

「是否需要 handoff 机制」问的是第 3 个问题；但没有前两个，第 3 个没有意义。

## 二、持久执行平台怎么做

这一类系统（Temporal、Inngest、DBOS、Restate、LangGraph、Cloudflare Workflows、Vercel Workflow）的卖点就是「代码写成普通函数，崩溃后自动从断点继续」。它们的机制惊人地一致：

| 系统 | 检查点粒度 | 恢复方式 | 崩溃检测 | 对用户代码的要求 |
|---|---|---|---|---|
| Temporal | 每个 Activity 的入参与返回值写进 append-only 的 Event History | 新 worker 从头重放 workflow 代码，已有记录的 Activity 直接回填结果 | Workflow Task Timeout 默认 10 s；Activity 的 Start-To-Close 超时 + 可选 Heartbeat | workflow 必须确定性（禁随机、时间、IO）；Activity 必须幂等，「完成恰好一次，但可能执行多次」 |
| Inngest | 每个 `step.run` 的返回值落库 | 函数从头重跑，命中过的 step 跳过 | HTTP 请求失败即重试，每个 step 默认 4 次 | step id 确定且唯一；非确定逻辑必须放进 step |
| DBOS | 每个 step 一次 Postgres 写入 | 用原输入重跑 workflow，每到一个 step 先查表 | 不用心跳：重启时按 executor_id 自恢复 PENDING 的 workflow；分布式由 Conductor 通知接管 | workflow 确定性；step 幂等；workflow 级恢复上限 100 次，超过进「死信」 |
| Restate | 每个 `ctx.run` 的操作与结果写进 journal | 在新实例上重放 journal | inactivity 1 min 后要求挂起，abort 10 min 后强杀 | `ctx.run` 体「可能执行多次」，必须幂等 |
| LangGraph | 每个 super-step 后存整张图的状态快照 | 同一 thread_id 再 invoke 一次，从最后检查点继续 | 没有租约或心跳，由调用方触发恢复 | 副作用要包进节点；durability 三档：exit / async / sync |
| Cloudflare / Vercel | 每个 step 的返回值缓存 | `run` 从头执行，step 外代码重跑 | 平台重试，step 默认 5 / 3 次 | step 名是缓存键，必须确定；step 必须幂等 |

四条共性：

- **检查点粒度统一落在「一次外部调用 / 一个 step」**，没有任何系统在 step 内部再细分。
- **恢复的统一模式是「重放 + 跳过」**：编排层从头重跑，有记录的步骤回填，第一个没记录的步骤真正执行。
- **幂等由用户代码承担，框架不保证**。所有文档都承认「step 做完了但结果没来得及记」这个窗口。
- **崩溃检测分两派**：服务端为中心的用超时和心跳；数据库为中心的（DBOS）用状态标记 + 重启自查。

来源：Temporal https://docs.temporal.io/workflow-definition 、https://docs.temporal.io/encyclopedia/detecting-activity-failures ；Inngest https://www.inngest.com/docs/learn/how-functions-are-executed ；DBOS https://docs.dbos.dev/architecture 、https://docs.dbos.dev/production/workflow-recovery ；Restate https://docs.restate.dev/concepts/durable_execution ；LangGraph https://docs.langchain.com/oss/python/langgraph/checkpointers ；Cloudflare https://developers.cloudflare.com/workflows/build/rules-of-workflows/ ；Vercel https://vercel.com/docs/workflows/concepts 。

## 三、数据库任务队列怎么做

RepoAegis 的 worker 就是一个「轮询数据库表」的队列，所以这一类最贴近。

**`FOR UPDATE SKIP LOCKED` 解决的和没解决的。** 它解决多个 worker 抢同一行；它没解决的是行锁随事务结束而释放：要么整个任务在一个事务里跑（长任务占着连接和锁），要么领到就提交（崩溃后行停在 running，数据库不会自动回收）。所以下面每个库都在它之外加了一层「超时 / 心跳 + 回收」。

| 库 | 崩溃检测 | 谁回收 | 回收后 |
|---|---|---|---|
| Oban（Elixir） | 开源版纯固定超时，默认 1 h；Pro 版靠 producer 心跳 | Lifeline 插件每 60 s 扫 | 放回 available 重试，耗尽 max_attempts 后 discarded。文档明说「可能让仍在执行的任务重复执行」 |
| pg-boss（Node） | `expireInSeconds` 默认 15 min，另有 `heartbeatSeconds` | 带 `supervise` 的实例 | 按普通失败走 retryLimit，耗尽进死信 |
| Solid Queue（Rails） | 心跳：60 s 一次，5 min 没心跳算死 | supervisor | **标失败不自动重试**，理由是「任务本身可能就是杀死进程的原因」 |
| River（Go） | 固定超时 `RescueStuckJobsAfter` 默认 1 h | leader 选出的一个 client | 重投或 discard；文档明说「可能与仍在跑的旧尝试并行」 |
| Graphile Worker | `locked_at` 超过 4 h | 任意 worker 每 8–10 min 顺带扫 | 重新可领 |
| SQS | visibility timeout 默认 30 s，上限 12 h | broker 自身 | 重新可见；长任务用 ChangeMessageVisibility 续租 |
| Celery | `acks_late` + broker 的 visibility_timeout（Redis 默认 1 h） | broker | 著名的坑：任务时长超过 visibility timeout 会被反复重投 |

**僵尸 worker 与 fencing token。** 所有基于超时或心跳的方案都承认「旧尝试可能还在跑」，因此都要求幂等。Kleppmann 的论证：持锁者可能因为 GC 暂停等原因停顿超过租约而不自知，醒来后继续写。唯一的硬防线是 **fencing token**：每次领取发一个单调递增的编号，所有写入带编号，存储端拒绝小于已见最大值的写入。Kubernetes 的 client-go 明确声明自己没有做这一层。

共性：租约时长要么是「任务允许运行的上限」（固定超时派），要么是「多久没心跳算死」（心跳派）；续租间隔取租约的几分之一；回收后多数重试并计数、耗尽进死信；Solid Queue 是唯一选择「标失败等人看」的。

来源：https://www.postgresql.org/docs/current/sql-select.html ；https://oban.hexdocs.pm/Oban.Lifeline.html ；https://pgboss.io/api/queues ；https://github.com/rails/solid_queue ；https://riverqueue.com/docs/maintenance-services ；https://worker.graphile.org/docs/error-handling ；https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html ；https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html ；https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html 。

## 四、编码 agent 系统怎么做

**（A）恢复方式归成四类：**

1. **重放事件流 / 转录**。OpenHands：事件流是唯一真相源，`State` 只存迭代数等元数据，历史一律从事件重建；V1 SDK 是 `base_state.json` + 逐条事件文件。Claude Code：JSONL 转录全量重放，崩溃时还在跑的工具调用标为 cut off，「模型被告知先检查它是否已生效再决定要不要重跑」。
2. **从文件系统 + git 重建**。Anthropic《Effective harnesses for long-running agents》：每个会话都是新上下文，开头固定读 `git log` 和进度文件，选一个未完成的 feature 做，做完提交。git commit 就是检查点，对话不跨会话传递。Devin 的机器快照同理。
3. **以完成粒度跳过，未完成的从头重跑**。SWE-agent / mini-SWE-agent 批量模式：trajectory 只在完成时写，「中断后重跑脚本即可」；SWE-bench 官方 harness：`report.json` 存在就跳过该实例，实例内部不可续。
4. **不支持 / 降级**。mini-SWE-agent 交互模式；Codex 依赖模型自己重读仓库重建进度，用户在 issue 里要求引入结构化检查点。

**（B）交接内容归成四类：**

1. **完整对话历史**：OpenAI Agents SDK 的 handoff 默认把整段历史交给下一个 agent（可用 `input_filter` 裁剪、可附结构化 `input_type`）。注意这是同一进程内 agent 之间转交控制权，和跨进程恢复是两回事。
2. **摘要文档**：Claude Code / Codex / OpenCode 的 compaction；Amp 的 handoff（模型从旧线程提炼出新线程的起始 prompt + 相关文件清单，用户可改）。Amp 已彻底移除 compaction，理由是「有损，且鼓励摘要叠摘要的长线程」。
3. **结构化对象**：OpenHands 的 observation / 事件；Agents SDK 的 `input_type` schema；SWE-bench 的 predictions 文件。
4. **文件系统 + git**：Anthropic long-running harness、Devin、Jules。上下文完全不跨会话传，全靠磁盘上的产物与记录。

来源：https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents ；https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents ；https://github.com/OpenHands/OpenHands/pull/2709 ；https://docs.openhands.dev/sdk/guides/convo-persistence ；https://code.claude.com/docs/en/sessions ；https://ampcode.com/news/handoff ；https://openai.github.io/openai-agents-python/handoffs/ ；https://mini-swe-agent.com/latest/usage/swebench/ ；https://www.swebench.com/SWE-bench/reference/harness/ 。

## 五、对照到 RepoAegis

先盘点已经有的，再说缺的。

**已经有的，而且和业界主流同构：**

- **append-only 事件表**是任务的审计真相源（同 Temporal 的 Event History、OpenHands 的事件流）。
- **阶段之间的交接已经是结构化对象**：计划在审批信封里，每一轮的记录（改了什么、测试报告、假设）是事件，下一轮从事件表读回来。这正是第四节（B）里的第 3 类，也是 Amp 弃用 compaction 后的方向。**不需要再加一份自由文本的 handoff 文档**，那是第 2 类，业界证据偏向结构化。
- **检出可以从记录的 commit 重建**（solving 阶段发现工作区不在就按 `repo_sha` 重新准备）。
- **领任务是一次 compare-and-set**，两个 worker 不会领同一个。

**缺的，按第一节的三个问题：**

1. **崩溃检测：没有。** 任务停在 planning / solving / verifying / delivering 时，没有任何东西能判断它的 worker 是死是活。这是本次调研里每个系统都有、我们唯一没有的部件。
2. **检查点粒度：事实上已经是「一个阶段」**，只是没有人利用它。planning 从头重做等于重新克隆、重新跑一次模型（几分钱）；一轮 solving 从头重做需要把工作区退回上一轮结束时的样子；verifying 重做就是重跑测试；delivering 重做需要 push 与开 PR 幂等。业界会把粒度切到「每次 LLM 调用」（Temporal 把每个外部调用做成 Activity），但对我们一个阶段只花几分钱、几十秒，而且上下文工程已经决定每个阶段是独立会话，「阶段」就是自然的 step。
3. **幂等：部分。** 逐阶段看：
   - planning：重做前要确认没有已存在的待审批信封，否则会开两道门。
   - solving：崩溃发生在一轮中间，工作区里有半截编辑。需要每一轮结束时把工作区状态记成一个 git 提交或 ref（Anthropic harness 的「git 即检查点」），重做时先 `reset` 回去。
   - verifying：幂等，重跑即可。
   - delivery：push 已是 `--force`，幂等；开 PR 前要先查同名分支是否已有 PR。
4. **僵尸 worker：半防。** 状态改写带着「期望的旧状态」，僵尸的 `advance` 会失败；但 `emit` 事件和开审批信封不带这个条件，僵尸仍能写。补上 fencing token（领任务时发一个递增编号，所有写入带它）才能封死。

## 六、可选方案

- **甲：租约 + 心跳 + 回收 + fencing token。** 任务行加 `lease_owner`、`lease_until`、`lease_token`。领任务时写入；阶段进行中定期续租（LLM 循环的 `on_step` 回调和沙箱等待是天然的续租点）；worker 每次 tick 顺带把过期租约的任务放回该阶段的可领状态并计数，超过上限标 failed（River / Oban 的模式）；状态改写和事件写入都带 token，旧 token 被拒绝（Kleppmann）。配套做阶段幂等（第五节第 3 条）。
- **乙：只做启动恢复。** 服务启动时把停在中间状态的任务放回去（DBOS 的 executor_id 自恢复模式）。十几行代码，只覆盖「整个服务重启」，多 worker 无效，也不防僵尸。
- **丙：不做。** 单机单 worker，出事手动改数据库。

三者都不需要新的交接机制；差别只在崩溃检测和幂等的完整程度。
