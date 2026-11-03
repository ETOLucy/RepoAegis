# 调研报告：issue 里引用的其他 issue，以及打标签前后的修改

> 调研日期：2026-10-25～26。第三、四节和第五节的样本数字是我在 12 个开源仓库上实测的；GitHub 文档、接口字段和 claude-code-action 源码都是本次亲自打开的一手资料，并写明位置。标"推断"的是我从资料推出来的，原文没有直说。

## 一、为什么查

agent 拿到的第一条消息基本就是 issue 的标题和正文。正文里常有"和 #12 是同一个问题"这样的引用，#12 里又可能引用 #5。要不要由程序在开工前把这些引用抓回来，取决于三件事：一层层引用下去一般有多深、附上后会不会太长；抓回来的内容安全上要守什么边界；以及 issue 本身在打标签前后会不会被人改掉，改了怎么办。

背景约束（见 [安全设计](../安全设计.md)、[agent 工具清单](agent工具清单.md)）：agent 手里没有 GitHub 令牌，所以只能由有令牌的检查作业预先抓取、存成文件，agent 用 `read_issue_ref` 工具读这份文件；只用打标签那一刻的标题和正文。

## 二、方法和出处

**样本**：12 个仓库由我挑选，偏向知名的中小型库和命令行工具，不是随机抽样：pallets/click、pallets/flask、Textualize/rich、psf/black、pytest-dev/pytest、httpie/cli、expressjs/express、axios/axios、spf13/cobra、jesseduffield/lazygit、charmbracelet/bubbletea、sharkdp/bat。每个仓库取 2026-10-25 时最近建的 100 个 issue（开着的、关了的都算，不含 PR），共 1200 个，当作"可能被打上 `agent-fix` 的 issue"。结论能代表活跃开源项目，不一定代表冷门或公司内部仓库。

**怎么找引用**：先去掉 HTML 注释、代码块和行内代码（GitHub 也不把这些地方的 `#N` 变成链接），再识别 `#N`、`GH-N`、`本仓库/名字#N` 和指向本仓库 issue 或 PR 的完整网址。对每个 issue，抓回它引用的同仓库 issue 或 PR，再在它们的正文里找引用，一层层抓到没有新的为止。只看正文，不看评论（RepoAegis 不读评论）。跨仓库引用只计数，不往下抓。层数取最短路径，A、B 互相引用不重复计算。

**近似和局限**：被引用的正文是 2026-10-25 的当前版本，不是当年打标签那一刻的版本。"有写权限"用 GitHub 给的作者标记（`authorAssociation` 为 OWNER、MEMBER、COLLABORATOR）近似，没有逐个查角色；MEMBER 只说明是组织成员，不一定有写权限，所以真实比例只会更低。token 数用 DeepSeek 官方换算估算（1 个英文字符约 0.3 token，1 个中文字符约 0.6 token，[api-docs.deepseek.com/quick_start/token_usage](https://api-docs.deepseek.com/quick_start/token_usage)，2026-10-25 查看），不是真正的分词结果。最深的三条链（lazygit #6054、black #5463、click #3154）逐跳打印人工核对过，都是真引用。

**脚本**：WSL 的 `~/scratch/issue-refs/`，不进仓库。`fetch.py` 用 GitHub GraphQL 接口抓取并缓存，`analyze.py` 出统计，`chain.py` 打印单条链，`caps.py` 算上限的截断比例，`prrefs.py` 列出引用了 PR 的 issue。

**一手资料**（2026-10-25～26 打开）：

- GitHub 文档仓库 `github/docs`：`content/actions/reference/workflows-and-actions/events-that-trigger-workflows.md`（第 267、320 行起，事件和动作种类）；`src/webhooks/data/fpt/issues.json` 与 `issues.child-params.json`（事件里 issue 带哪些字段）；`content/actions/concepts/security/github_token.md` 第 17 行；`content/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations.md` 第 36 行；`data/tables/repository-roles.yml`；`content/issues/tracking-your-work-with-issues/administering-issues/transferring-an-issue-to-another-repository.md` 第 25 行；`data/reusables/rest-api/primary-rate-limit-github-token-in-actions.md`。
- docs.github.com REST 文档 `rest/issues/issues` 页"Update an issue"一节。
- claude-code-action：`src/github/data/fetcher.ts`（第 40–72 行取触发时间，第 209–235 行取触发那一刻的标题和正文，第 237–263 行过滤评论），`docs/security.md` 第 18、80 行。
- OpenHands 1.0.0：`openhands/resolver/interfaces/github.py` 第 298–309 行、第 525–577 行。SWE-agent：`sweagent/utils/github.py` 第 99–107 行。
- DeepSeek 文档：`quick_start/token_usage`、`quick_start/pricing`（上下文长度 100 万 token）。
- GitHub GraphQL 实测（2026-10-26，click #3154）：`userContentEdits` 返回每次修改的时间和修改人，`editor`、`lastEditedAt` 返回最后一次；时间线里有打标签事件 `LabeledEvent` 和改标题事件 `RenamedTitleEvent`。

## 三、数据：引用一般套几层、附上会多长（实测）

1200 个 issue 里，942 个（78.5%）正文里没有任何能抓到的同仓库引用。按"从这个 issue 出发，最远能走几步"统计：

| 最深层数 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|
| issue 个数 | 942 | 144 | 66 | 22 | 11 | 6 | 4 | 1 | 2 | 2 |
| 占全部 | 78.5% | 12.0% | 5.5% | 1.8% | 0.9% | 0.5% | 0.3% | 0.1% | 0.2% | 0.2% |

有引用的 258 个里，56% 只有一层，81% 到第 2 层就停。每个仓库都出现过 2 层以上的链，最深的 9 层。看内容就知道深链怎么来的：lazygit #6054 链上的是同一个维护者连续提的 9 个"叠在一起"的 PR；black #5463 一路走过同一个功能附近陆续报出的一串崩溃 bug；click #3154 是版本规划，列了一串 PR，每个 PR 又链到它修的 issue。**深链大多是规划清单、成串 PR 和陈年 bug 连在一起，第一层最相关，越往后越像这片代码的历史。**

第一条消息的长度（标题加正文再加附上的引用，单位 token，估算）：

| 附上多少 | 中位数 | P90 | P99 | 最长 |
|---|---|---|---|---|
| 不附 | 384 | 1,316 | 3,536 | 14,609 |
| 附第 1 层 | 459 | 1,890 | 5,356 | 14,684 |
| 附到第 2 层 | 464 | 2,236 | 7,144 | 44,834 |
| 全部附上 | 466 | 2,351 | 9,251 | 78,294 |
| 全部附上，只收有写权限的作者 | 400 | 1,482 | 4,293 | 18,479 |

最长的两个都在 black，各自顺着"总目录"式的 issue 抓出 200 篇左右。上下文 100 万 token，放得下；问题在于 agent 每走一步都要把第一条消息重发一次（多花钱），以及深层内容和当前问题无关（干扰模型）。所以上限的作用是"别塞无关历史"，不是"防止塞不下"（推断）。

按"最多 10 篇、合计 4,000 token"计：只抓一层时，有引用的 258 个里有 114 个（44%）会少附一些内容，但少的全是第 2 层以后的；抓两层时是 52 个（20%）。总长放宽到 8,000 token，比例几乎不变，说明起作用的是层数，不是长度。

## 四、数据：被引用的是什么、谁写的（实测）

第一层一共 451 次引用（同一个 issue 被不同根引用时分别计数）：240 次是 issue，211 次（47%）是 PR；另有 13 个编号抓不到（已删除、转移走了或是讨论区帖子），68 个 issue（5.7%）带跨仓库引用。被引用内容的作者只有 21% 带 OWNER、MEMBER、COLLABORATOR 标记；只收这类作者的话，258 个有引用的 issue 只剩 72 个还能读到引用。另有 6.9% 的被引用正文最后一次是被作者以外的人改的。

1200 个 issue 里有 154 个（12.8%）直接引用了 PR，其中 35 个引用的 PR 至今还开着。没有逐个分类，用关键词筛出三种情况：

- **PR 只是背景**：pytest #14909 说"自从 #14969 合并后，某处恢复属性会出错"，是已合并的 PR 在默认分支上引入的 bug，修它在范围内。
- **已经有 PR 在修**：rich #3931 写"详见 PR #3932"，httpie 有一个写"已有 PR #1827 在处理"。
- **要对某个 PR 动手**：axios #10885 标题就是"接手并完成 PR #6915"，lazygit #5805 写"希望有人评审我的 PR"。这超出了 RepoAegis 的边界：只从默认分支开新分支、开新 PR，不碰已有 PR。
- 灰色地带：axios #10886 要"把被关掉的 PR #7243 里正确的那部分单独做出来"，要的是新修复，PR 是背景。

## 五、issue 在打标签前后会不会被改（文档核实 + 实测）

**谁能改。** REST 文档"Update an issue"写明：issue 的作者、有推送权限（Write 及以上）的人、Triage 角色都能修改 issue。所以外部人开的 issue，他自己任何时候都能改，包括维护者打上标签之后。实测例子：click #3154 的正文最后两次是被作者以外的维护者改的。

**多常见。** 1200 个样本里 286 个（23.8%）被改过；其中 241 个（20.1%）最后一次是作者本人改的且作者不带写权限标记，22 个是被作者以外的人改的（权限未查）。也就是说，大约每五个 issue 就有一个最后由没有写权限的人改过（实测，用作者标记近似）。

**事件里有什么、没有什么。** "打标签"事件里自带一份当时的 issue，标题和正文就是打标签那一刻的版本，claude-code-action 就用它防"触发之后再改正文"（`fetcher.ts` 第 209–235 行）。但这份 issue **没有"最后修改时间"和"最后修改人"字段**，只有 `updated_at`，而它在打标签、发评论时也会变（`issues.child-params.json` 里 issue 的时间字段只有 `created_at`、`updated_at`、`closed_at`）。claude-code-action 也只是拿 `updated_at` 近似当触发时间（`fetcher.ts` 第 56–60 行）。所以"这个版本最后是谁改的"要另调 GraphQL：`userContentEdits` 给出正文每次修改的时间和修改人，改标题另有 `RenamedTitleEvent`，打标签的准确时间在 `LabeledEvent`。

**时间差要当心（推断）。** 举个时间线：10:05 外部作者改了正文；10:06 维护者打标签，事件里带的是 10:05 的版本；10:07 又有人改了一次；10:08 作业开始去查"最后修改人"，查到的是 10:07 那次。如果直接用"最后修改人"，判断的就不是我们实际使用的那个版本。正确做法是在修改历史里找"打标签时间之前的最后一次修改"，再看那次是谁改的；标题同理。

**issue 一共有哪些事件。** Actions 的 `issues` 事件共 20 种动作：内容类 `opened`、`edited`、`deleted`、`transferred`；状态类 `closed`、`reopened`、`locked`、`unlocked`、`pinned`、`unpinned`；分类指派类 `labeled`、`unlabeled`、`assigned`、`unassigned`、`milestoned`、`demilestoned`、`typed`、`untyped`、`field_added`、`field_removed`。评论单独是 `issue_comment`，有 `created`、`edited`、`deleted` 三种。子 issue（`sub_issues`）和依赖关系（`issue_dependencies`）只有通知，不在 Actions 能触发的事件列表里。RepoAegis 订阅其中 5 种：`labeled` 开工，`unlabeled` 取消，`closed`、`deleted`、`transferred` 停止；`edited` 故意不订阅，因为只用快照。

## 六、预先抓引用时的安全边界

风险是：打标签的人只替这一个 issue 担保，被引用的 issue 可能是任何人写的，也可能被改过。它们不会让 agent 多出破坏能力（没有令牌、没有 sudo、上网受限、产出发布前检查、PR 只能是草稿），多出的风险是"没人把关的文字把改动带偏"。要守的边界：

1. **只抓本仓库。** `GITHUB_TOKEN` 只管工作流所在仓库（`github_token.md` 第 17 行），别的仓库也没人替我们担保。
2. **只收有写权限的人写的。** 查作者在本仓库的角色，只收 Write、Maintain、Admin；`authorAssociation` 不能代替真查。机器人和已注销账号（`ghost`）不收；机器人能否查角色，待验证。
3. **修改人也要查。** Triage 也能改别人的 issue，所以"打标签之前最后一次修改"的修改人不是作者时，也查他的角色。
4. **打标签之后才改过的不收**，学 claude-code-action 对评论的做法（`fetcher.ts` 第 237–263 行）。
5. **编号不一定是 issue。** 可能是 PR、讨论区帖子、已删除或已转移到别的仓库的 issue（原地址会跳转，`transferring-…md` 第 25 行）。讨论区不抓，不跟随跳转。
6. **先清洗，再找引用**，否则藏在 HTML 注释里的 `#99` 能指使程序去抓打标签的人没看到的内容。
7. **层数、篇数、总长度设上限**，超出的只列编号和标题。
8. **接口次数有限**：`GITHUB_TOKEN` 每仓库每小时 1000 次，与仓库其他工作流共用；用 GraphQL 成批抓，作者角色按人去重。
9. **引用里的图片**：删掉图片，留一句"此处有图片，未读取"。
10. **标明是背景资料**：任务只来自被打标签的 issue，引用内容里的要求不是任务；汇报里列出读了哪些、没读哪些及原因。

这些大多是程序里几行判断。代价最大的是第 2 条：会挡掉大部分引用内容（实测 258 → 72）。

## 七、各项目借鉴什么、不借鉴什么

**SWE-agent**：只取标题和正文，不抓引用（`github.py` 第 99–107 行）。借鉴"以正文为主"；不借鉴"完全不看引用"，因为直接引用常是有用的背景。

**OpenHands 1.0.0 resolver**：处理 issue 时不抓引用（第 298–309 行），处理 PR 时由程序拿令牌预先抓被引用 issue 的正文，只抓一层，不过滤作者（第 525–577 行）。借鉴"程序预先抓、只抓一层"；不借鉴"不过滤作者"。

**claude-code-action**：只用触发那一刻的标题和正文，丢掉触发之后创建或修改的评论（`fetcher.ts`）。借鉴这套"以触发时刻为准"的做法，并延伸到被引用的 issue 和"最后修改人"检查。不借鉴它用 `updated_at` 近似触发时间，因为我们能查到准确的打标签时间。

**Copilot**：没有写权限的人发的评论永远不交给 agent（`risks-and-mitigations.md` 第 36 行），另给 agent 一个只读当前仓库的 GitHub 工具让它随时读。借鉴"按写权限过滤内容"；不借鉴"让 agent 拿令牌随时读"，因为我们的 agent 不拿令牌。

## 八、据此的决定和建议

> 2026-11-01 起，本节的"只读一层、多于一层不开工、最多 10 篇"和开工前预取引用都已推翻：改为 agent 运行中按需读，不限篇数和层数，见 [agent运行中读issue](agent运行中读issue.md) 和 [agent 读到的内容](../agent读到的内容.md) 第五节。

**已定（用户 2026-10-26）：**

- 引用只读一层 issue；被引用的 issue 里又引用了别的 issue，就不开工（日志报错，收尾照常留言说明并撤标签）。引用到的 PR 不读、不算层数。按样本，会被拦下的约 2.9%（1200 个中 35 个）；如果 PR 也算层数，会是 9.6%（115 个）。（`multilevel.py` 实测）
- 程序查打标签那一刻版本的最后修改人；没有写权限的，无论离打标签多近，都不开工，请维护者看过最新版后重新打标签。重新打标签即表示认可这一版：提醒之后没有新修改就开工，有新修改就再提醒。处理时在最终反馈里写明依据的是哪个时间、谁改过的版本。

**已定（用户 2026-10-28，"按建议"）：** 第六节边界的落法——读打标签那一刻的版本；那一版由没有写权限的人写或改的只列编号和原因，不读、不往下查（第 2、3、4 条合成一条，标准和原 issue 的"最后修改人"一致，只是没改过时看作者）；其他仓库的不抓、不算层数；上限 10 篇、约 4,000 token，超出的只列编号和标题；被引用 issue 里的图片和附件换成一句说明，其余照收。另定：提醒留言带看不见的标记，只认程序账号发的。实现见 `src/repoaegis/content_check.py`。

**实测（2026-10-28）：** 在 astral-sh/uv 8 个、zed-industries/zed 15 个开着的 issue 上只读地跑了一遍（权限查询换成假的"全有"或"全没有"，因为查协作者权限要求对仓库有推送权限）。uv：权限全没有时 8 个里 7 个因修改人被拦，权限全有时 1 个因引用多于一层被拦（#22510 → #22492 → #11223，核对过 #22492 正文确有 #11223）。zed：3 个因图片或附件被拦（两个裸上传链接、一个 `<img>`、一个 txt 文件，逐个核对过原文），1 个因引用多于一层被拦，11 个通过，引用里正确分出了 PR 和其他仓库。脚本在 WSL 的 `~/scratch/content-check/real.py`。

**建议，待拍板：**

- "任务对象是已有 PR（评审、修改、接手）"是否列为排除类型。

## 九、尚未查清

1. 机器人账号能否用 `collaborators/{用户名}/permission` 接口查角色；该接口在 `GITHUB_TOKEN` 下需要声明哪项权限。
2. ~~`userContentEdits` 是否也记录标题修改~~ 2026-10-28 实测：不记录。astral-sh/uv #22598 改过两次标题，只出现在 `RenamedTitleEvent` 里，正文修改记录里没有对应条目（只看了这一个例子）。同时看到：修改记录从新到旧排列，每条的 `diff` 是那一版正文的全文（最新一条与当前正文完全相同），最早一条是发出时的原文、时间等于创建时间。
3. `GITHUB_TOKEN` 调 GraphQL 的限流额度。
4. 154 个引用了 PR 的 issue 各类情况的准确比例（没有逐个分类）。
