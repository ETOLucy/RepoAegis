# 实测报告：GitHub 的 issue 事件里有什么

> 实测于 2026-10-21（接收程序在本机用自己编的假请求自测）和 2026-10-24 北京时间 18:15–18:32（在私有测试仓库 `ETOLucy/repoaegis-sandbox` 里收真实事件）。材料：备份目录 `RepoAegis-backup-2026-10-25` 里当时会话记录中的命令和输出原文。2026-10-25 整理，未重新核对。测试用的 webhook、issue、PR、分支和本机程序事后都已删除，无法复现，只能以当时记录为准。实测过程有一部分原本记在一份已损坏的会话记录里，找不到的内容标【待验证】。

## 一、为什么查

最早的方案是自己写一个一直运行的服务，GitHub 一有事就通知它，它再决定处理不处理。这种"事件发生时，对方主动往你给的网址发一个 HTTP 请求"的机制叫 **webhook**。这个请求分两部分：**请求头**像信封上的字，写着是什么事件、投递编号；**请求体**是一份 JSON，叫**载荷**，写着具体发生了什么。

整个项目的前提是这条通知能到达程序，而且程序能从载荷里读出几件事：打的是哪个标签、谁打的、对象是 issue 还是 PR、操作者是人还是机器人。GitHub 文档对后两项写得不清楚，当时主对话凭记忆说了 `issue.pull_request` 和 `sender.type`，用户指出"你说的github这些取值，我不知道。"，查文档也没查到（见第三节），所以必须实测。延迟多大、投递编号能不能用来去重，也在要测的范围内。

后来触发方式改成了 GitHub Actions，工作流里直接读同一份事件内容（例如条件 `github.event.label.name == 'agent-fix'`），所以这次实测得到的字段位置仍然在用。

## 二、方法和出处

**先读文档。**【一手】2026-10-21 读了 GitHub 文档 *Webhook events and payloads*（`docs.github.com/en/webhooks/webhook-events-and-payloads`，当时只读了约 19.4 万字符中的前 10 万）：请求头有 `X-GitHub-Event`（事件名）、`X-GitHub-Delivery`（每次投递的唯一编号）、`X-Hub-Signature-256`（请求体的签名，只有设置了密钥才带）、`User-Agent`（以 `GitHub-Hookshot/` 开头）等；`issue_comment` 事件在 issue 和 PR 的评论上都会触发；文档提醒不要假设 `sender` 一定是引发事件的那个人，找不到用户时会填成 `ghost`。文档里没有找到 `issue.pull_request` 能区分 PR、`sender.type` 有 `User` 和 `Bot` 两种取值的说明。

**本机自测（2026-10-21）。** 在 WSL 里用 Python 标准库写了一个接收程序，监听 8000 端口，收到请求就判断、打印、把整条记录（收到时间、全部请求头、原始字节、解析后的载荷）追加写进一个记录文件。用自己编的假请求测了它的判断逻辑。这一步只证明程序本身没问题，不能证明 GitHub 的真实取值。

**真实事件（2026-10-24）。** 测试仓库是私有的。先在仓库里建了标签 `agent-fix`。因为本机没有公网地址，GitHub 的服务器够不着本机的 `localhost`，所以用 GitHub CLI 的扩展 `cli/gh-webhook` 做中转，命令是：

```bash
gh webhook forward --repo=ETOLucy/repoaegis-sandbox --events=issues --url=http://localhost:8000/
```

它会在仓库上临时登记一个 webhook，指向 GitHub 自己的中转服务（`webhook-forwarder.github.com`），本机这边和中转服务保持一条长连接，事件到了就推下来，再变成一个 POST 请求发到 8000 端口。这次没有设置密钥。

第一轮（18:15）用命令依次做了三件事，每件隔几秒：给 4 号 issue 打 `agent-fix`，给 5 号 issue 打 `bug`，撤掉 4 号的 `agent-fix`。然后读记录文件，看请求头名字、载荷顶层字段、标签和操作者的内容，并用 issue 的 `updated_at` 和本机收到时间估算延迟。最后用 GitHub 接口取 4 号 issue 的事件时间线核对。

第二轮（18:25–18:32）是演示给用户看的：接收程序加了一个查看网页，新建了 8 号、9 号两个演示 issue，再给 8 号打 `agent-fix`、给 9 号打 `bug`、撤掉 8 号的 `agent-fix`；之后改成只显示需要处理的通知，又给 8 号打了一次 `agent-fix`、给 9 号打 `documentation`、再撤掉 8 号的 `agent-fix`。

接收程序的判断规则是：事件必须是 `issues`，动作是 `labeled`，标签名是 `agent-fix`，且 issue 里没有 `pull_request` 字段，才算"开始干活"；其余一律忽略。

## 三、数据

### 第一轮：三条真实通知

| 本机收到（北京时间） | 动作 | 标签 | 程序判断 | issue 的 `updated_at` | 估算延迟 |
|---|---|---|---|---|---|
| 18:15:42 | `labeled`（4 号） | `agent-fix` | 开始干活 | 18:15:43 | -0.7 秒 |
| 18:15:49 | `labeled`（5 号） | `bug` | 忽略 | 18:15:50 | -0.9 秒 |
| 18:15:55 | `unlabeled`（4 号） | `agent-fix` | 忽略 | 18:15:57 | -1.1 秒 |

【实测】三条都收到了，判断全对。延迟算出来是负数，不是通知真的提前到了，而是本机时钟和 GitHub 服务器时钟差了一秒左右，【推断】实际延迟在一两秒以内。这是经过中转服务的延迟，GitHub 直接投递到公网地址时的延迟没测。

【实测】4 号 issue 的时间线和通知对得上：18:15:43 被 ETOLucy 打上 `agent-fix`，18:15:56 被撤掉。

### 载荷里的字段

【实测】以第一条通知为例：

- 顶层只有五个字段：`action`（动作）、`issue`、`label`、`repository`（哪个仓库）、`sender`（谁做的）。
- 打的标签在 `label` 里：`name` 是 `agent-fix`，另有 `color`、`description`。
- 操作者在 `sender` 里：`login` 是 `ETOLucy`，`type` 是 `User`。
- `issue.labels` 是这个 issue 当前身上的全部标签，当时是 `bug` 和 `agent-fix`。
- `issue` 里**没有** `pull_request` 字段，它是普通 issue。

### 请求头

【实测】和 GitHub 相关的请求头有 `X-Github-Delivery`、`X-Github-Event`、`X-Github-Hook-Id`、`X-Github-Hook-Installation-Target-Id`、`X-Github-Hook-Installation-Target-Type`、`X-Github-Request-Id`，另有一批中转链路加的头，这里不列。注意写法是 `Github` 而不是文档里的 `GitHub`。没有签名头，因为没设密钥。

这正好印证了 10-21 自测时修过的一个 bug：当时把请求头转成了普通字典，字典区分大小写，事件名读不到；改成按不区分大小写的方式读取才正常。【推断】写法变化可能是中转工具在转发时把请求头名统一改成了首字母大写的形式（GitHub CLI 是 Go 写的，Go 的 HTTP 库会这样规范请求头名），GitHub 直接投递时是不是文档里的写法没测。按 HTTP 规范请求头名本来就不区分大小写，读取时不区分大小写就不受影响。

### 第二轮：演示

【实测】第一段收到五条：两条 `issues.opened`（新建 8 号、9 号）、8 号 `labeled agent-fix`（开始干活）、9 号 `labeled bug`（忽略）、8 号 `unlabeled agent-fix`（忽略），从在 GitHub 上操作到终端出现记录都在一秒左右。这说明订阅 `issues` 这一类之后，新建、撤标签等所有动作都会发过来，webhook 没法只订阅其中某个动作，筛选只能靠程序。第二段结束时记录文件里一共 8 条通知，其中 2 条判为开始干活，都是 8 号被打上 `agent-fix`。

【实测】8 号 issue 的时间线（GitHub 服务器时间）：18:26:00 打上、18:26:18 撤掉、18:31:44 又打上、18:32:02 又撤掉。两次"打上"是两个独立动作，各有自己的投递编号，按投递编号去重挡不住。

### 中转工具的一个行为

【实测】转发工具运行时，仓库的 webhook 列表里多了一个名为 `cli` 的 webhook；停掉转发工具几秒后，它自己从列表里消失了。之前读它的源码时没找到退出时删除 webhook 的代码，以为测完要手动清，这个推断是错的，以实测为准。【推断】可能是 GitHub 那边在长连接断开后自动清理的。

## 四、和官方项目的对照

webhook 只能按"哪一类事件"订阅，所以 9 号打 `bug`、新建 issue 这类通知都会发来，要程序自己丢掉。Actions 不一样：配置里可以写 `on: issues: types: [labeled]` 只在打标签时运行，再加条件 `if: github.event.label.name == 'agent-fix'` 限定标签名，筛选在 GitHub 开机器之前就做完了。OpenHands 解决器的工作流就是这样写的（条件里的标签名是 `fix-me`）【待验证】：当时是凭之前读过的配置说的，现存记录里没有留下出处位置。

## 五、据此的决定

2026-10-24 北京时间 18:37，看完演示后用户说："我要使用 actions， 我不要 webhook。"同时定下触发规则：打开着的 issue，不论新旧，只要被打上 `agent-fix` 就处理；新建 issue 本身、打别的标签都不触发；已关闭的 issue 不处理（18:46 原话："至于 HU 关闭的话，那肯定是不要处理的"，"HU"是语音识别把 issue 识别错了）。随后测试用的 webhook、issue、PR、分支、本机接收程序和 `cli/gh-webhook` 扩展都删除了，记录见项目记忆 `project-scope-one-shot.md`；用户要求删除的原话【待验证】，在已损坏的会话记录里。

## 六、尚未查清

1. **PR 那一侧没有真实样本。** 现存记录里，真实通知只出现过"普通 issue 没有 `pull_request` 字段"。PR 的 `issue` 里确实有 `pull_request` 字段，只在假请求里出现过【待验证】。`README.md`"验证进展"一节写的"`issue` 里有没有 `pull_request` 字段可以区分 issue 和 PR"，比现存记录能支撑的多，原始记录可能在已损坏的会话记录里。
2. **机器人那一侧没有真实样本。** 真实通知里 `sender.type` 只出现过 `User`；`Bot` 只出现在假请求里【待验证】。README 写的"`sender.type` 区分人和机器人"同样待确认。当时列为未测的还有：用机器人身份撤标签时程序能否认出是自己。
3. 设置密钥后的签名校验；在网页上重发同一条通知时投递编号变不变（中转工具建的 webhook 一停就消失，没法在网页上点重发）。这两项在改用 Actions 后已不需要。
4. 请求头写法变成 `Github` 的原因（见第三节的推断）。
5. Actions 工作流里 `github.event` 的内容是否就是这份 webhook 载荷，当时是凭说明讲的，没有在那一刻核对文档【待验证】。
6. 新建 issue 时直接带上 `agent-fix` 会不会触发 `labeled`；标签整个从仓库删掉时，是否对每个 issue 发出撤标签事件；给 PR 打标签会不会触发 `issues` 事件。这些都要在测试仓库里用一个最小的 Actions 工作流实测。
7. 第一轮用的 4 号、5 号 issue 是否也已删除：记忆里只写了删除 1、8、9 号 issue 和关闭 2、7 号 PR【待验证】。
