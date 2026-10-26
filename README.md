<div align="center">
  <h1>
    <img src="assets/repo-aegis-mark-tight.png" height="64" alt="RepoAegis"><br>RepoAegis
  </h1>
  <p>让 agent 根据 GitHub issue 改代码并开 PR</p>
  <p>状态：开发中，触发和开工前检查已实现并实测，agent 部分未开始 · 许可证：Apache-2.0</p>
</div>

---

## 要做什么

有权限的人给 issue 打上专用标签，RepoAegis 就在隔离环境里克隆仓库，让 agent 改代码，开一个草稿 PR，在 issue 里汇报结果，然后撤掉标签。每个 issue 只处理一次：PR 之后怎么评审、修改、合并或关闭，全部由人决定。

下面是目标流程，**第 1、2 步已实现，其余还没有**：

1. 有权限的人给一个**开着的** issue 打上专用标签，新建的、以前建的都可以。新建 issue 本身、打别的标签、已关闭的 issue，一概不触发。
2. GitHub Actions 收到"打上专用标签"就开始处理。处理中途，Write、Maintain 或 Admin 角色的人撤掉标签、关闭、删除或转移这个 issue，正在进行的处理就停止；其他人（Triage、机器人、issue 作者）做这些动作，一律不理会，处理照常进行（2026-10-26 已定，见下文"谁能触发"）。打别的标签也会产生一次运行，但在条件判断那一步就被跳过，不会开始处理。
3. 在隔离环境里克隆仓库、装依赖，agent 读代码、改代码、跑测试。
4. 把改动推到一个新分支，开草稿 PR，PR 描述里写 `Closes #编号` 让 GitHub 自动关联 issue。
5. 做不出来或跑不起来时，在 issue 里如实说明原因。
6. 无论成败，最后在 issue 里给出反馈并撤掉专用标签，这个 issue 的处理到此结束。

"每次都有反馈、都撤标签"由程序保证，不靠 agent 自觉（已定）：收尾单独放在最后一个作业里，设成不管前面成功、失败还是被取消都会执行；它只看前面留下了什么（有没有改动、agent 有没有交汇报、是否超时或出错），按事实选留言模板，汇报内容来自 agent 交的固定格式汇报。另有一个定时运行的小工作流做兜底：找出挂着专用标签、却没有正在运行的处理的 issue，在 issue 里说明上次处理没有正常结束，并撤掉标签。各种情况下的留言具体怎么写，待讨论。

结束时的评论由程序按模板生成，agent 不直接写评论（2026-10-26 已定）：agent 做完只交一份固定字段的 JSON 汇报，程序校验后填进模板，字段和模板待定。评论里固定写明"按 某年某月某日某时 打上标签时的内容处理"，时间取打标签那条事件的时间；不附当时的原文，也不附哈希，因为 GitHub 对 issue 内容没有版本号或哈希，只记录每次修改的时间和修改人，能读仓库的人都能在正文的 **edited** 菜单里看到修改历史。修改标题或正文本身不触发运行，agent 始终按打标签那一刻的内容做到底；处理期间标题或正文被改过时，评论里加一句提示：改的人是 Write、Maintain 或 Admin，就写明谁在什么时候改了什么，并说明如需按新内容处理请撤掉标签后重新打上；是其他人改的，只提示去看修改历史。

目前不处理带图片的 issue，图片以外的附件一律跳过。图片要靠能看图的模型才能读懂，是否引入看图能力，等主体流程做完再决定；遇到带图片的 issue 时具体怎么回应，尚未确定。

明确不做的事情（不根据评审继续修改、不合并 PR、不自建通知服务、不面向大型仓库等）及原因，以及什么样的 issue 适合交给 agent、怎么写更容易被做对，见 [docs/项目边界.md](docs/项目边界.md)。

## 用什么触发

用 GitHub Actions，而不是自己搭一个接收通知的服务。Actions 的配置可以直接写成"只在 issue 被打上标签、并且标签名是专用标签时运行"，筛选在 GitHub 那边完成；每次运行的记录和日志都保存在仓库的 Actions 页面上，不需要自己再存一份。用 Actions 自带的令牌撤掉标签不会再次触发运行，这是 GitHub 为防止循环触发做的规定。

代码放在 RepoAegis 仓库里，要接入的仓库只放一个十几行的调用文件（见下文"验证进展"）。

## 谁能触发

GitHub 规定只有 Triage 及以上权限的人才能给 issue 打标签（[权限文档](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization)），所以只读权限的外部人触发不了。但 Triage 角色（能管 issue 和标签、不能改代码）也能打标签，所以处理开始后的第一步，还要用接口查一次打标签的人在仓库里的角色：**只有 Write、Maintain、Admin 三种角色打的标签才开始处理**（已定，和 Copilot、claude-code-action、codex-action、gh-aw 的默认门槛一致）。其他人或机器人账号打的标签，在 issue 里说明"没有触发权限"并撤掉标签。

打标签的人是谁，不看触发这次运行的那条事件里的 `sender`，而是翻 issue 的事件记录，取最后一次打上专用标签的人（2026-10-26 已定）。原因是 GitHub 不保证事件按发生顺序处理（[官方说明](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/troubleshooting-webhooks#webhooks-deliveries-are-out-of-order)，没有公开的概率数据），一次"撤标签"产生的运行可能最后才轮到，这时标签又被重新打上了，它就得接手干活，而它的 `sender` 是撤标签的人。所以每次运行都同时看两样：这条事件是谁做的，以及 issue 现在是什么样子。

处理开始后，只有这三种角色的动作会影响它（2026-10-26 已定）：他们撤标签、关闭、删除、转移 issue，处理就停止。其他人撤标签、关闭 issue、修改标题或正文，一律不理会：不留言、不把标签加回去，程序不为这件事动 issue；如果他们的动作让后面某一步做不成了（比如 issue 被删了、评论发不出去），只在运行日志里记一条错误，其余步骤照常。这条规则写进使用说明，不在 issue 里提示。代价是 issue 作者自己关掉 issue 后，agent 仍会做完并开草稿 PR。

这意味着"撤标签就停"不能再交给 Actions 的并发组自动取消，因为新运行进组那一刻还查不到撤标签的人是谁。要改成：每次运行先判断，需要停时由程序调用[取消接口](https://docs.github.com/en/rest/actions/workflow-runs#cancel-a-workflow-run)停掉正在跑的那次。这个接口需要 Actions 写权限，只给检查作业，agent 作业绝不能有。这一改动尚未写进代码。

## 验证进展

**已实现，并在私有测试仓库 `ETOLucy/repoaegis-sandbox` 实测通过（2026-10-25）：**

- 代码结构：RepoAegis 的 [.github/workflows/agent-fix.yml](.github/workflows/agent-fix.yml) 是一个只供别的仓库调用的工作流（开头写 `on: workflow_call`），条件判断、并发组和所有步骤都在这里。要接入的仓库只需把 [examples/agent-fix.yml](examples/agent-fix.yml) 复制到自己的 `.github/workflows/` 下并合进默认分支，它只负责订阅五种 issue 动作（打标签、撤标签、关闭、删除、转移）和声明权限。运行时用 `actions/checkout` 把 RepoAegis 检出到 `repoaegis/` 子文件夹，取的是工作流文件所在的那次提交，保证代码和工作流版本一致；用到的两个官方动作都固定到完整提交号。
- 开工前检查 [src/repoaegis/precheck.py](src/repoaegis/precheck.py)：查 issue 当前状态、最后打上标签的人、此人的权限，给出"开工""安静结束""没有触发权限"三种结论；只用 Python 标准库，不装任何东西；15 个单元测试在 [tests/test_precheck.py](tests/test_precheck.py)。
- 实测：打上 `agent-fix`，从运行创建到作业开始约 4 秒，作业 7 秒跑完，结论"开工"，令牌只有 `issues: read` 和 `metadata: read` 就查到了权限；打无关标签，产生运行但作业被跳过、不分配机器；撤掉标签，结论"标签已不在"；关闭挂着标签的 issue，作业执行（说明关闭事件的内容里带着标签），结论"已关闭"；重新打开 issue，不产生运行。Python 3.14 用的是运行机器上的缓存，不到 1 秒装好。

**还没验证或还没定：**

- 撤标签能否停掉正在跑的处理，以及作业上的条件为假时是否完全不进并发组：现在检查几秒就结束，没有东西可以被取消，要等 agent 那一步写好再测。
- 新建 issue 时就直接带上专用标签会不会触发，按 GitHub 官方文档会，待实测。
- 按 2026-10-26 的规则把"撤标签就停"改成程序判断后调用取消接口，尚未写进代码；并发组相应改成只排队不取消，细节写代码时定。
- Triage 撤掉标签后 agent 还在跑，这时有权限的人又打上标签：正在跑的那次是继续还是重来，待定。
- 用 Actions 自带令牌开的 PR，它的 CI 会停在"等待批准"状态，要有写权限的人点"Approve workflows to run"才跑（[官方说明](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)）；推代码和开 PR 用什么身份，还没定。
- 处理中途撤标签、重复触发的完整分析见 [docs/中途撤标签与重复触发.md](docs/中途撤标签与重复触发.md)。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
