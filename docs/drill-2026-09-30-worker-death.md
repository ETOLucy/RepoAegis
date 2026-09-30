# 演练记录：worker 死在改代码中间（2026-09-30）

> 验证 #17（租约）和 #18（重做幂等）在真环境下拼起来是否成立：真数据库、真 git 检出、
> 真 Docker 沙箱、真 GitHub。issue 用 psf/requests#6917。完整的多故障演练另行设计，
> 这次只做最小的一种：进程被强杀。

## 时间线（UTC）

| 时刻 | 发生了什么 | 证据 |
|---|---|---|
| 12:42:31 | 建任务 `b4ffeada`，worker `23224` 领走做计划 | `task.status_changed queued→planning` |
| 12:43:42 | 计划批准，同一 worker 领走改代码，凭证 2 | 任务行 `lease_owner=…:23224:…`，`lease_token=2` |
| 12:43:4x | 模型第一次改文件（`replace src/requests/utils.py`），脚本立刻 `taskkill /F` 杀掉进程 | 检出里 `M src/requests/utils.py`，半截编辑 |
| 12:44 – 12:48 | 重启后端，新 worker `20684`。任务纹丝不动：租约未到期，不能判死 | 任务行不变 |
| 12:48:42 | 死掉的 worker 最后一次心跳算起 5 分钟，租约到期 | `lease_until` |
| 12:48:43 | 新 worker 巡查发现过期，回收；`recoveries=1`，租约清空 | `task.reclaimed {previous_owner: …:23224:…, recoveries: 1}` |
| 12:48:43 | 新 worker 领走，凭证 3；求解阶段先把检出退回干净基线，半截编辑被清掉 | `lease_token=3`；日志 `workspace.restored` |
| 12:49 – 12:53 | 重做三轮（每轮开始前退回上一轮快照：`round-1`、`round-2`），第三轮到轮次上限 | 日志 `workspace.restored name=round-1 / round-2`；三条 `round.finished` |
| 12:53:32 | 先写补丁信封、再改状态到等待 | `approval.requested` 在 `task.status_changed` 之前 |
| 12:53:46 | 推送、开出 PR，任务 done | `pull_request.opened`，ETOLucy/requests#5 |

结论：**worker 死后任务不再永远卡住**，从死亡到恢复 5 分 1 秒，与租约长度一致；重做从干净现场开始；整条线在事件表里可回放。

## 演练暴露并已修的问题

1. **数据库层的写入没从上下文取凭证。** 规划结束时 `record_run` 直接走 `storage`，
   而上下文凭证只在状态机的 `advance`/`emit` 里取，写入被自己的栅栏拒掉，任务留在
   planning 且无人再领。修法：`current_claim` 挪到 `storage.py`，所有带租约核对的写入
   都回退到它。
2. **无人持有的 planning 任务没人捡。** 上一条的后果。修法：worker 在 queued 之后捡
   无租约的 planning 任务重新规划。重启后顺带捡回了一个 9 月 14 日起就卡在 planning
   的老任务。
3. **等推送审批时空转领取。** 任务停在 delivering 等人批推送，worker 每半秒领一次、
   看一眼、释放，一分钟领了 120 次；并且人点批准的瞬间常常正好有人持有，写入被拒，
   接口返回 500。修法：最新的推送信封还开着的任务不领；人批时撞上持有者返回 409
   而不是 500。

## 没按预期的地方

- 第一次尝试的「杀」下手晚了近两分钟（等人工确认），模型已经改完并通过测试，任务在
  等补丁审批、无人持有，那一刀等于没砍。第二次改成脚本从批计划到杀进程全程自动。
- 重做后的三轮修法没有通过全部测试（第三轮仍有 1 个失败），到轮次上限开门。脚本自动
  批了补丁，开出的 PR 是红的，演练后已关闭。模型结果有随机性，不是恢复机制的问题，
  但说明「自动批准」只能用在评测，不能用在任何会对外的场景。

## 单元测试没覆盖、只有真跑才发现的

心跳协程陪着一次真实 Docker 测试跑完没有问题；`restore` 在真的 requests 检出上
（含 `.repoaegis/` 目录、符号链接）没有误删；`find_pull_request` 的返回格式与 GitHub
一致（第一个任务的重复交付路径没触发，这一条仍未在线验证）。
