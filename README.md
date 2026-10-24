<div align="center">
  <h1>
    <img src="assets/repo-aegis-mark-tight.png" height="64" alt="RepoAegis"><br>RepoAegis
  </h1>
  <p>让 agent 根据 GitHub issue 改代码并开 PR</p>
  <p>状态：重构中，暂无可运行代码 · 许可证：Apache-2.0</p>
</div>

---

## 要做什么

有权限的人给 issue 打上专用标签，RepoAegis 就在隔离环境里克隆仓库，让 agent 改代码，开一个草稿 PR，在 issue 里汇报结果，然后撤掉标签。每个 issue 只处理一次：PR 之后怎么评审、修改、合并或关闭，全部由人决定。

下面是目标流程，**还没有实现**：

1. 有权限的人给一个**开着的** issue 打上专用标签，新建的、以前建的都可以。新建 issue 本身、打别的标签、已关闭的 issue，一概不触发。
2. GitHub Actions 收到"打上专用标签"就开始处理；收到"撤掉专用标签"就取消这个 issue 正在进行的处理。打别的标签也会产生一次运行，但在条件判断那一步就被跳过，不会开始处理。
3. 在隔离环境里克隆仓库、装依赖，agent 读代码、改代码、跑测试。
4. 把改动推到一个新分支，开草稿 PR，PR 描述里写 `Closes #编号` 让 GitHub 自动关联 issue。
5. 做不出来或跑不起来时，在 issue 里如实说明原因。
6. 无论成败，最后在 issue 里给出反馈并撤掉专用标签，这个 issue 的处理到此结束。

"每次都有反馈、都撤标签"由程序保证，不靠 agent 自觉（已定）：收尾单独放在最后一个作业里，设成不管前面成功、失败还是被取消都会执行；它只看前面留下了什么（有没有改动、agent 有没有交汇报、是否超时或出错），按事实选留言模板，汇报内容来自 agent 交的固定格式汇报。另有一个定时运行的小工作流做兜底：找出挂着专用标签、却没有正在运行的处理的 issue，在 issue 里说明上次处理没有正常结束，并撤掉标签。各种情况下的留言具体怎么写，待讨论。

目前不处理带图片的 issue，图片以外的附件一律跳过。图片要靠能看图的模型才能读懂，是否引入看图能力，等主体流程做完再决定；遇到带图片的 issue 时具体怎么回应，尚未确定。

明确不做的事情（不根据评审继续修改、不合并 PR、不自建通知服务、不面向大型仓库等）及原因，以及什么样的 issue 适合交给 agent、怎么写更容易被做对，见 [docs/项目边界.md](docs/项目边界.md)。

## 用什么触发

用 GitHub Actions，而不是自己搭一个接收通知的服务。Actions 的配置可以直接写成"只在 issue 被打上标签、并且标签名是专用标签时运行"，筛选在 GitHub 那边完成；每次运行的记录和日志都保存在仓库的 Actions 页面上，不需要自己再存一份。用 Actions 自带的令牌撤掉标签不会再次触发运行，这是 GitHub 为防止循环触发做的规定。

## 谁能触发

GitHub 规定只有 Triage 及以上权限的人才能给 issue 打标签（[权限文档](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization)），所以只读权限的外部人触发不了。但 Triage 角色（能管 issue 和标签、不能改代码）也能打标签，所以处理开始后的第一步，还要用接口查一次打标签的人在仓库里的角色：**只有 Write、Maintain、Admin 三种角色打的标签才开始处理**（已定，和 Copilot、claude-code-action、codex-action、gh-aw 的默认门槛一致）。其他人或机器人账号打的标签，在 issue 里说明"没有触发权限"并撤掉标签。

## 验证进展

已在私有测试仓库里实测过 GitHub 的 issue 事件内容：

- 打的标签在 `label.name`，操作者在 `sender.login`，`sender.type` 区分人和机器人，`issue` 里有没有 `pull_request` 字段可以区分 issue 和 PR。
- 打专用标签、打其他标签、撤标签三种动作能清楚区分，只有第一种需要处理。

还没验证或还没定：

- 在测试仓库里放一个最小的 Actions 配置，验证"打上标签 → 运行 → 撤掉标签"的全过程。
- 新建 issue 时就直接带上专用标签，按 GitHub 官方文档会触发"打标签"，与我们的规则一致，待实测确认。
- 处理中途标签被人撤掉、撤掉后又重新打上怎么办，以及能否从外部停止正在运行的处理：官方项目的做法和我们的建议见 [docs/中途撤标签与重复触发.md](docs/中途撤标签与重复触发.md)，待拍板和实测。
- 用 Actions 自带令牌推送的代码不会触发仓库的 CI，推代码和开 PR 用什么身份，还没定。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
