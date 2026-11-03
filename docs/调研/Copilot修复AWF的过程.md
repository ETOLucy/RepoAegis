# 调研报告：Copilot 怎么修好我们报给 AWF 的问题

> 日期：2026-10-31。标【一手】的来自 GitHub 接口返回的记录或官方文档原文，标【推断】的是从记录推出来、没有单独验证的。

## 一、为什么查

2026-10-30 我们给 AWF 提了 [github/gh-aw-firewall#10840](https://github.com/github/gh-aw-firewall/issues/10840)（安装检查的管道被 SIGPIPE 误判，见 [AWF沙箱实测](AWF沙箱实测.md)）。维护者把它交给 GitHub 的代码 agent Copilot，几个小时后修复就合并、发版了。Copilot 做的事和 RepoAegis 要做的是同一件：拿到 issue，改代码，开 PR，交给人审。所以看它每一步怎么走、周围配了什么，对照我们的设计找能借鉴的地方。

## 二、方法和出处

- PR [github/gh-aw-firewall#10852](https://github.com/github/gh-aw-firewall/pull/10852) 的提交、时间线、评论、审查：`gh api repos/github/gh-aw-firewall/pulls/10852/commits`、`.../issues/10852/timeline`、`.../issues/10852/comments`、`.../pulls/10852/reviews`。
- 仓库给 agent 的说明和环境：提交 `6b5573a` 的 `AGENTS.md`（指向 `CLAUDE.md`，412 行）、`.github/workflows/copilot-setup-steps.yml`、`.github/agents/`、`.github/skills/`。
- 官方文档：[About Copilot coding agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent)、[Risks and mitigations](https://docs.github.com/en/copilot/concepts/agents/coding-agent/risks-and-mitigations)、[Customize the agent firewall](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/customize-the-agent-firewall)（2026-10-31 读，用网页摘要工具提取，原文没有逐句核对）。
- 没看到的：Copilot 的会话记录（它读了哪些文件、跑了哪些命令）不公开。

## 三、过程（时间是 UTC，2026-10-30）【一手】

| 时间 | 发生了什么 |
| --- | --- |
| 20:12:06 | Copilot 推了一个空提交 "Initial plan"，同时开了草稿 PR |
| 20:12:10 | 维护者把 issue 指派给 Copilot（PR 和指派几乎同时出现） |
| 20:12:39 | 记录里出现 "copilot_work_started" |
| 20:17:06 | 真正的修复提交：`action.yml` 改一行 + 测试文件加 30 行 |
| 20:22:15 | "copilot_work_finished"，从开始到结束约 10 分钟 |
| 20:52:12 | 维护者把草稿改成"可以审了" |
| 20:54:01 | Copilot 代码审查机器人给出"建议通过，没有问题" |
| 21:28–21:41 | 门禁机器人提示加 `ready-for-aw` 标签跑冒烟测试；覆盖率检查通过 |
| 22:26–22:45 | 维护者加标签，十几个冒烟测试工作流跑完（Claude、Codex、Gemini、Copilot 各引擎、网络隔离、chroot 等），各自回一条评论，其中两个失败 |
| 23:34:56 | 维护者合并 |
| 次日 00:39 | 发布 v0.29.1，含这个修复 |

Copilot 的产出：
- 代码改动和我们 issue 里建议的一行完全相同。
- 自己补了 3 个回归测试：让冒充的 `gh` 输出超过管道缓冲区的帮助文字（让旧写法必然失败）、缺少参数时仍拒绝、`gh` 出错时仍拒绝。
- PR 描述分"改了什么""测试覆盖"两段，写明 Fixes #10840。

## 四、怎么看它"有多厉害"

- **难的部分是 issue 里已经给好的。** 原因、复现、一行修法都写在 issue 里【一手】，Copilot 照着改。它额外做对的是补测试，而且测试设计抓住了要害：用超过管道缓冲区的输出让旧写法必然失败，而不是碰运气。
- **真正厉害的是周围那套流程，不是单个 agent。** 草稿 PR 一开始就出现、代码审查机器人、覆盖率检查、十几个冒烟测试、发版自动化，都在 Copilot 之外。人只做了四件事：指派、改成可审、加标签触发测试、合并。
- 两个冒烟测试失败（Azure OpenAI 密钥模式、服务连通性），维护者仍然合并了【一手】；看评论内容像是和这次改动无关的环境问题【推断】。

## 五、官方文档里和我们相关的规则【一手】

- **谁能触发：** "Only users with write access to the repository can trigger"；没有写权限的人的评论 "are never presented to the agent"。
- **能推哪里：** 只能推一个分支，新任务用 `copilot/` 开头的新分支；不能自己执行 git 命令，只能做"simple push operations"。
- **PR：** 只能是草稿，agent 不能改成可审、不能批准、不能合并；发起任务的人不能批准这个 PR。
- **CI：** agent 开的 PR，工作流默认要有写权限的人点 "Approve and run workflows" 才会跑。
- **提示词注入：** 交给 agent 前过滤隐藏字符；HTML 注释里的文字不交给 agent。
- **交付前自查：** 用 CodeQL 查安全问题、新依赖对照漏洞库查、扫描密钥、再请代码审查给第二意见，有问题先自己改。
- **提交署名：** 提交作者是 Copilot，发起人记为共同作者；提交说明里附会话记录的链接。
- **时长：** Actions 上的会话最长 59 分钟，不能延长。
- **网络：** 默认有防火墙，同时默认开启"推荐白名单"：系统软件源、容器镜像仓库、各语言的包仓库、常见证书机构。防火墙只管 agent 用 bash 启动的进程，不管 MCP 服务和"setup steps"。请求被挡时，在 PR 描述里加一条警告，写明被挡的地址和发起请求的命令。
- **装依赖：** 仓库可以放一个 `.github/workflows/copilot-setup-steps.yml`，作业名必须是 `copilot-setup-steps`，在 agent 开工前跑，用来预装工具和依赖（AWF 仓库自己就用它装 Node 和 gh-aw）。

## 六、借鉴什么，不借鉴什么

**借鉴：**
1. **装依赖的两条路一起给。** 仓库方可以写一份"开工前准备步骤"，在沙箱外、agent 开工前跑；agent 自己需要时，也能通过包仓库白名单安装。这正好回答我们悬而未决的"依赖谁来装"，细节在讨论依赖时再定。
2. **被挡的网络请求要让人看见。** 写进最后的 issue 留言或 PR 描述：哪个地址、哪条命令。AWF 自带 `awf logs summary` 能出被挡域名的统计【一手，`docs/github_actions.md`】。
3. **要求 agent 补回归测试。** 在提示词和汇报字段里要求"修改前失败、修改后通过的测试"，做不到就在汇报里说明原因。
4. **可追溯：** PR 描述或提交说明里附这次 Actions 运行的链接，人能回头看 agent 做了什么。
5. **以下几条我们已经定了同样的做法，可以当作佐证：** 只认写权限的人触发、只开草稿 PR、不让 agent 合并、60 分钟上限、agent 不直接执行 git 推送（我们是发布作业推，agent 作业没有令牌）。

**不借鉴：**
1. **一开工就开草稿 PR 并持续更新计划。** 这需要 agent 那一步拿着能写仓库的令牌，和我们"agent 作业不拿写权限，发布另起一台机器"的拆分冲突。想让人早点看到"开始了"，可以由检查作业留一句言，要不要做另行讨论。
2. **CodeQL、代码审查第二意见这类交付前自查。** 先按已定的"规则检查做扎实、暂不加 AI 复查"走；依赖漏洞检查可以以后考虑。
3. **十几个冒烟测试那套 CI。** 那是 AWF 仓库自己的 CI，不是 Copilot 的一部分，和我们无关。

## 七、尚未查清的

- Copilot 会话里具体读了什么、跑了什么命令、跑没跑测试（会话记录不公开）。
- "推荐白名单"的完整域名列表（文档说在 allowlist reference 里，没打开）。
- 文档内容是网页摘要工具提取的，引号里的短句没有和原文逐字核对。
