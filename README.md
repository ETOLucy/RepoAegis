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

1. 有权限的人给 issue 打上专用标签。其他标签、其他事件一概不管。
2. RepoAegis 收到 GitHub 发来的"加标签"通知，确认打的是专用标签、不是自己打的。
3. 在隔离环境里克隆仓库、装依赖，agent 读代码、改代码、跑测试。
4. 把改动推到一个新分支，开草稿 PR，PR 描述里写 `Closes #编号` 让 GitHub 自动关联 issue。
5. 做不出来或跑不起来时，在 issue 里如实说明原因。
6. 无论成败，最后撤掉专用标签，这个 issue 的处理到此结束。

目前不处理带图片的 issue，图片以外的附件一律跳过。图片要靠能看图的模型才能读懂，是否引入看图能力，等主体流程做完再决定；遇到带图片的 issue 时具体怎么回应，尚未确定。

## 谁能触发

GitHub 规定只有 Triage 及以上权限的人才能给 issue 打标签（[权限文档](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization)），所以只读权限的外部人触发不了。是否再加一层更严格的检查，尚未确定。

## 验证进展

已在私有测试仓库里实测通过：

- 用 GitHub 官方命令行的转发扩展（`gh webhook forward`）把 `issues` 通知转到本机，打标签后一两秒内就能收到。
- 通知里的字段和 GitHub 文档一致：打的标签在 `label.name`，操作者在 `sender.login`，`sender.type` 区分人和机器人，`issue` 里有没有 `pull_request` 字段可以区分 issue 和 PR。
- 打专用标签、打其他标签、撤标签三种通知都能正确区分，只有第一种会触发。
- 请求头的大小写和文档写法不同（`X-Github-Event` 而不是 `X-GitHub-Event`），读取时必须不区分大小写。

还没验证：

- 用机器人身份（GitHub App）撤标签时，程序能否认出是自己做的而不再触发。
- 设置密钥后的签名校验。
- 在 GitHub 网页上重发同一条通知时，投递编号是否保持不变（去重要靠它）。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
