# RepoAegis 里程碑与任务清单

状态：草案。与 [design-draft.md](design-draft.md) 配套：阶段编号 M0 到 M7 对应其 §16，括号里的 § 是对应的设计章节。

## 使用方式

- 每个二级标题对应一个 GitHub Milestone；"目标"可填进 Milestone 描述。
- 每条任务建成一个 issue 并挂到对应 Milestone；大任务用 sub-issues 拆分。
- 截止日期留空，开工前按实际估算填写。
- 任务标签：`design`、`backend`、`agent`、`github`、`eval`、`docs`、`verify`。`verify` 表示核实类任务，结论回写设计文档 §17。

---

## 1. MVP

**覆盖阶段**：M0 到 M3

**目标**：新 issue 进来，分诊只读地分类、追问、打标签并给出文字建议；人在 GitHub 里回复后任务继续。不执行代码，不需要沙箱，不接 Ops 平台，不做控制台。

**完成标准**

- 在测试仓库新建 issue，30 秒内收到评论或标签。
- 同一事件重发不产生第二个任务。
- agent 追问后，人在 issue 里回复，任务继续并给出结论。
- 回应超时后，任务被搁置并留下说明，不再消耗预算。
- 人在运行中途关闭 issue，任务被取消。
- 每个任务有完整的时间线与花费记录。

### M0 可行性验证

- [ ] `verify` 在测试仓库登记 webhook，经转发到本机并打印载荷（一次性程序，不进项目）（§10.4）
- [ ] `verify` 以 GitHub App 身份回写一条评论（§10.2）
- [ ] `verify` 查证 webhook 验签算法与头字段（§17）
- [ ] `verify` 确认开发机内存与磁盘是否支撑后续阶段（§17）

### M1 事件到任务

- [ ] `backend` 数据库与迁移：`repos`、`work_items`、`events`、`tasks`、`task_log`、`usage_ledger`、`spans`（§6）
- [ ] `backend` webhook 入口：验签、`delivery_id` 去重、同事务写事件、工作项与任务（§4.2、§6.3）
- [ ] `backend` 事件处置清单：agent、规则、仅记录三类分流的配置（§4.1）
- [ ] `backend` 事件路由：丢弃 agent 自己与可忽略机器人的事件；归入工作项；按有无活动任务分流（§4.2）
- [ ] `backend` 任务状态机、乐观锁、"每个工作项至多一个活动任务"的部分唯一索引（§4.3、§4.4）
- [ ] `backend` Worker：原子领取、租约、心跳、过期回收（§2.4）
- [ ] `backend` 同类事件窗口合并；并发上限（§4.3）
- [ ] `docs` 状态机转换表与事务边界落成文档（§4、§6.3）

### M2 分诊（只读）

- [ ] `agent` agent 循环与工具系统：统一签名、参数校验、结果截断（§8.2）
- [ ] `agent` headless 运行：由任务触发，无交互（§8.2）
- [ ] `agent` 只读工具：搜 issue、读代码、读仓库档案（§7.2）
- [ ] `agent` 处理器注册机制与分诊处理器（§7.1、§7.2）
- [ ] `agent` 分诊规则：类别到动作的配置化映射，首批覆盖缺信息的 bug、疑似重复、使用问题（§7.3）
- [ ] `github` `GitHubClient`：评论、标签、读 issue（§2.3）
- [ ] `backend` 预算上限与超限处理（§2.4）
- [ ] `eval` 分诊评测集：开源仓库历史 issue 回放（§12.2）

### M3 人机交互

- [ ] `backend` `inbox_items`、`outbox` 与发件箱循环（§5.3）
- [ ] `github` `Notifier`：GitHub 评论 @；`issue_comment` 事件匹配回收件箱条目（§5.1、§9）
- [ ] `backend` 唯一负责人、认领、超时升级、搁置（§9.4）
- [ ] `agent` 人的回应到达后任务继续（§5.1）
- [ ] `backend` 人为打断：关闭、重开、改标签时取消或暂停任务，记录纠正（§4.2）
- [ ] `backend` `Policy` 表与 `mode` 读取（最小版）（§3、§7.4）
- [ ] `backend` 命令与指示只采纳 OWNER、MEMBER、COLLABORATOR（§13）
- [ ] `eval` 确定性测试：状态机、租约、去重、收件箱超时、人为打断（假模型）（§12.2）

---

## 2. CI 维护线

**覆盖阶段**：M4

**目标**：主分支 CI 失败后，`ci.repair` 先重跑判断是否为不稳定测试；不是则读日志，调用编码引擎修复，推到 `agent/*` 分支由真实 CI 验证，通过后开草稿 PR，由人评审合并。首次引入沙箱、`CodeWorker` 与真实 CI 验证。

**完成标准**

- 不稳定测试与真失败能被区分；不稳定测试只上报，不改代码。
- 修复只推到 `agent/*` 分支，不能触及主分支。
- 草稿 PR 含原因、改动、风险与 CI 结果。

### M4 `ci.repair`

- [ ] `verify` 查证工作流运行失败的事件名、载荷字段与失败判定（§10.1）
- [ ] `github` 订阅 CI 事件：首次失败先由规则重跑，仍失败再创建 `ci.repair` 任务（§4.1）
- [ ] `agent` 不稳定测试判断：重跑并对比历史（§5.2、§11.2）
- [ ] `agent` `CodeWorker` 接口与 mini-swe-agent 接入（§11.1）
- [ ] `verify` 实测 mini-swe-agent 能否接 DeepSeek，以及 `run()` 的返回值（§17）
- [ ] `backend` 沙箱与工作区管理（§11.2）
- [ ] `github` `CIVerifier`：推 `agent/*` 分支，读取真实 CI 结果（§2.3）
- [ ] `github` Rulesets：限定 agent 只能写 `agent/*`（§10.3、§13）
- [ ] `github` 开草稿 PR，写明原因、风险与结果（§5.2）
- [ ] `github` Checks API：在 PR 上显示 RepoAegis 检查项（§10.3）
- [ ] `eval` CI 修复评测集，参考 CI-Repair-Bench（§12.2）

---

## 3. issue 修复线

**覆盖阶段**：M5

**目标**：分诊判定为有复现步骤的 bug 后，`issue.reproduce` 在沙箱里复现；经策略允许或维护者同意，`issue.fix` 复用 M4 的编码引擎与验证开草稿 PR。分诊本身仍不写代码。

**完成标准**

- 能复现的 bug 标记 `confirmed` 并附复现结果。
- 修复任务只在策略允许或人同意后创建。
- 复现失败有明确的默认动作（§17）。

### M5 `issue.reproduce` 与 `issue.fix`

- [ ] `agent` `issue.reproduce`：沙箱复现与结果记录（§7.2）
- [ ] `backend` 分诊产出到下一个处理器 的派生规则，读取 `Policy`（§7.3）
- [ ] `agent` `issue.fix`：复用 `CodeWorker` 与 `CIVerifier`（§7.2）
- [ ] `backend` `Policy` 完整化：`auto`、`suggest`、`ask`、`ignore` 与限额（§7.4）
- [ ] `eval` 复现成功率；修复 PR 合并后测试是否仍绿（§12.2）

---

## 4. 运行时机制

**覆盖阶段**：M6

**目标**：把 Claude Code 官方文档中的扩展机制逐个自研接入，配置与事件形状对齐官方；实现前先读官方文档。

**完成标准**：每个机制有确定性测试，并至少被一条业务链路实际使用。

### M6 运行时

- [ ] `agent` 权限规则：allow、ask、deny，与 `Policy` 同构（§8.2）
- [ ] `agent` Hooks：PreToolUse、PostToolUse、Stop 等，用于拦截对外动作（§8.2）
- [ ] `agent` 子智能体：独立上下文、限定工具、配置化（§8.2）
- [ ] `agent` Skills 与仓库档案：按需加载（§8.2）
- [ ] `agent` MCP 客户端（§8.2）
- [ ] `agent` 上下文管理：超长折叠；改动消息前缀前先核对缓存成本（§8.2）
- [ ] `agent` 会话持久化与恢复（§8.2）
- [ ] `docs` 对齐清单：官方字段与本项目字段逐项对照（§8）

---

## 5. 评测与打磨

**覆盖阶段**：M7

**目标**：各模块自测，并在成品上评测；补齐文档与演示。

**完成标准**：每个模块有可重复的指标与结果；确定性测试稳定通过；README 与演示可复现。

### M7 评测与打磨

- [ ] `eval` 分诊：标签准确率、查重召回、该追问时是否追问（§12.2）
- [ ] `eval` 不稳定测试判断的正确率（§12.2）
- [ ] `eval` 维护 PR：合并后测试是否仍绿、改动大小（§12.2）
- [ ] `eval` 模型相关行为：同一任务重复运行 N 次报成功率（§12.2）
- [ ] `eval` 回复语气与长度的人工抽检（§12.2）
- [ ] `docs` README、架构图、演示脚本
- [ ] `github` 用 RepoAegis 维护它自己的仓库，并用 Milestones 管理自身进度

---

## 后续（不属于上述里程碑）

- 处理器：`dependency.upgrade`、`pr.review`（§7.2）；规则：定时提醒长期无回应的工作项（§4.1）
- 飞书群通知（§9.3）
- 外部 Ops 平台接入：Phoenix 或 Langfuse，经 `TraceSink`（§12.1）
- 只读管理页面：任务时间线、策略设置、待处理汇总（§9.1）
- 记忆：从人的纠正与驳回中学习（§8.2）
- GitHub 附加功能：Milestones、Sub-issues、Projects、Discussions、Releases 的联动（§10.3）
