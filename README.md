<div align="center">
  <h1>
    <img src="assets/repo-aegis-mark-tight.png" height="64" alt="RepoAegis"><br>RepoAegis
  </h1>
  <p>让 agent 根据 GitHub issue 改代码并开 PR</p>
  <p>状态：开发中，触发和开工前检查已在测试仓库跑过，agent 的循环、模型调用和工具已写、还没接起来，产出检查和交付未开始 · 许可证：Apache-2.0</p>
</div>

---

## 要做什么

有权限的人给 issue 打上专用标签 `agent-fix`（名字暂定），RepoAegis 就在隔离环境里克隆仓库，让 agent 改代码，开一个草稿 PR，在 issue 里汇报结果，然后撤掉标签。每个 issue 只处理一次：PR 之后怎么评审、修改、合并或关闭，全部由人决定。

用 GitHub Actions 触发，不自己搭接收通知的服务：筛选事件、记录每次运行、保存日志都由 GitHub 完成。代码放在 RepoAegis 仓库里，要接入的仓库只放一个十几行的调用文件，模板是 [examples/agent-fix.yml](examples/agent-fix.yml)。

## 一次处理怎么走

1. **触发**：Write、Maintain 或 Admin 角色的人给一个开着的 issue 打上 `agent-fix`。其他人的动作一律不理会。→ [触发与叫停](docs/触发与叫停.md)
2. **开工前检查**：程序按打标签那一刻的版本整理 issue 内容，拦下带图片或附件、打标签前被没权限的人改过的；再调用一次模型，判断是不是清楚、能验证、规模够小的改代码任务。→ [agent 读到的内容](docs/agent读到的内容.md)、[接收哪些 issue](docs/接收哪些issue.md)
3. **agent 干活**：在沙箱里浅克隆仓库，用 bash、编辑器等四个工具读代码、改代码、跑测试，需要时按编号读本仓库别的 issue，最后交一份补丁和固定格式的汇报。→ [agent 怎么干活](docs/agent怎么干活.md)
4. **产出检查和发布**：在不拿密钥的机器上检查补丁和汇报，合规才推新分支、开草稿 PR。→ [安全设计](docs/安全设计.md)
5. **收尾**：无论成败都由程序在 issue 里按模板留言并撤掉标签；中途被有权限的人叫停时存档后暂停，重新打标签、内容没变就接着做。→ [暂停存档与监控](docs/暂停存档与监控.md)

明确不做的事情（不根据评审继续修改、不合并 PR、不面向大型仓库等）及原因，见 [项目边界](docs/项目边界.md)。

## 做到哪了

**已提交，并在私有测试仓库 `ETOLucy/repoaegis-sandbox` 跑过：**

- 可复用的工作流 [.github/workflows/agent-fix.yml](.github/workflows/agent-fix.yml) 和开工前检查 [src/repoaegis/precheck.py](src/repoaegis/precheck.py)：查权限、查 issue 当前状态、内容检查和清洗。只用 Python 标准库。
- 实测（2026-10-25）：打上 `agent-fix`，从运行创建到作业开始约 4 秒，作业 7 秒跑完；打无关标签，作业被跳过、不分配机器；撤标签、关闭 issue，检查结论分别是"标签已不在""已关闭"。令牌只有 `issues: read` 和 `metadata: read` 就查到了权限。2026-10-30 测过修改人、图片的拦截，以及 GitHub 接受并发组的 `queue: max` 写法。

**已写、有单元测试，未提交；在测试仓库只跑过局部：**

- 开工前检查里的停掉正在跑的运行、内容对比、挑选暂停存档；按工作日算存档期限。
- agent 作业的骨架：AWF 容器包住整个 agent 程序；读 issue 服务（2026-11-02 在测试仓库跑通，容器里找不到令牌）；收尾作业上传 Langfuse（2026-10-31 用 agent 替身跑通）。
- agent 主循环 [loop.py](src/repoaegis/loop.py)、调 DeepSeek 的 [model.py](src/repoaegis/model.py)、第一条消息 [first_message.py](src/repoaegis/first_message.py)、四个工具 [tools/](src/repoaegis/tools/) 和命令的环境变量白名单。还没组装成能跑的程序入口。

**还没写：** 程序入口、每步存档和从存档接着做、产出检查、发布、各种留言、定时对账。

## 现在卡在哪

- **最先要实测**：运行还没结束时，接口能不能列出它已经上传的产物。列不出来，检查程序就找不到正在跑的那次，"叫停"和"内容一致就让旧的继续"都失效。
- ~~AWF 能不能接上 DeepSeek（流式输出和工具调用）~~：2026-11-03 实测能，密钥代理不用自己搭（见 [AWF沙箱实测](docs/调研/AWF沙箱实测.md) 第八节）。
- **已知的代码缺口**：开工前检查没通过时收尾作业不执行，issue 里收不到说明；被取消时轨迹传不到 Langfuse；读 issue 服务的回答里带着写的人是谁；编辑器要的 flake8 没装；最后一条命令可能跑过 AWF 的 65 分钟。各篇文档的"待定"一节写了细节。
- **待拍板**：产出检查的规则、token 上限的默认值、同一 issue 的检查要不要排队、收尾作业放在哪、密钥在轨迹里怎么遮、发布用什么身份。各篇文档末尾的"待定"一节有完整清单。

## 文档

设计文档按一次处理的顺序排，每条规则只写在一处：

- [触发与叫停](docs/触发与叫停.md)：什么动作会开工、谁的动作算数、怎么叫停、重新打标签时比什么。
- [接收哪些 issue](docs/接收哪些issue.md)：五条接收规则和阈值、不通过时怎么回应、怎么写 issue 更容易被接。
- [agent 读到的内容](docs/agent读到的内容.md)：用哪一版内容、修改人、清洗、图片和附件、agent 运行中怎么读别的 issue。
- [agent 怎么干活](docs/agent怎么干活.md)：模型、循环、四个工具、克隆到哪。
- [暂停存档与监控](docs/暂停存档与监控.md)：存档怎么存和留多久、收尾留言、监控。
- [安全设计](docs/安全设计.md)：作业拆分、权限、密钥代理、产出检查。
- [项目边界](docs/项目边界.md)：不做什么、为什么。

调研报告放在 [docs/调研/](docs/调研/README.md)，每次调研一篇，是上面各篇决定的依据。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
