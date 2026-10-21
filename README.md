<div align="center">
  <h1>
    <img src="assets/repo-aegis-mark-tight.png" height="90" alt="RepoAegis"><br>RepoAegis
  </h1>
  <p>事件驱动的代码仓库维护平台</p>
  <p>状态：设计阶段 · 许可证：Apache-2.0</p>
</div>

---

## 简介

RepoAegis 监听 GitHub 仓库里发生的事件，比如新 issue、CI 失败、依赖更新，按事件类型交给对应的处理器，由 agent 或确定性规则处理。需要信息、判断或授权时，它在 GitHub 里向人提问或请求确认，人回应后继续。

agent 只能写自己的分支，合并等对外有风险的动作由人决定。

> **当前状态**：项目处于设计阶段，还没有可运行的代码。下文描述的是设计目标，不是已实现的功能。

## 要解决的问题

仓库维护有大量重复而琐碎的工作：新 issue 要分类、查重、追问缺失的信息；主分支 CI 变红，要先判断是真失败还是不稳定的测试；依赖要升级，还要确认升级后能跑。

RepoAegis 把其中机械、可自动化的部分交给 agent 与规则，把需要判断和需要负责的部分留给人。

## 工作方式

![工作方式](docs/diagrams/01-workflow.png)

1. GitHub 事件经 webhook 进入系统，先验签并去重。
2. 路由到对应的工作项，按事件处置清单分流：交给 agent、交给规则、或仅记录。
3. 需要 agent 的事件创建任务，由处理器驱动 agent 执行。
4. agent 通过评论、标签、草稿 PR 等方式对外动作；缺信息或需要授权时，在 GitHub 里 @ 相关的人。
5. 人在 GitHub 里回复或评审，回应成为新的事件，任务继续。

## 设计目标

- **不是所有事件都启动 agent。** 需要读懂自然语言、探索代码或多步判断的才交给 agent；能写成确定规则的由代码直接处理。
- **GitHub 是主交互渠道。** 提问、回复、评审、合并都在 GitHub 里完成，不要求维护者离开 GitHub。
- **权限收紧。** agent 只能写 `agent/*` 分支；PR 的合并由人和 GitHub 处理；分诊 agent 只读、不持有密钥。
- **可恢复、可审计。** 数据库是各进程唯一的交接处；任务有租约，处理幂等，通知经发件箱发送；每一步都有记录。
- **可扩展。** 一类事件的处理方式是一个处理器，新增处理器不改核心。
- **各模块独立评测。** 不追编码榜单，分别度量分诊、不稳定测试判断、维护 PR 等环节。

## 架构

![总体架构](docs/diagrams/02-architecture.png)

模块化单体，多进程部署：API 进程接收事件，Worker 进程执行任务，调度与通知循环处理超时和发件箱，三者通过 PostgreSQL 协作。编码引擎和沙箱仅在需要改代码的处理器里使用。

## 处理器

| 处理器 | 触发 | 做什么 | 阶段 |
|---|---|---|---|
| 分诊（`issue.triage`） | issue 新建、补充信息 | 分类、查重、追问、打标签，给出文字建议；只读 | MVP |
| CI 修复（`ci.repair`） | 主分支工作流失败 | 先重跑排除不稳定测试，否则读日志、修复、真实 CI 验证、开草稿 PR | 后续阶段 |
| 复现（`issue.reproduce`） | 分诊判定有复现步骤的 bug | 在沙箱里复现 | 后续阶段 |
| 修复（`issue.fix`） | 已确认的 bug 且策略允许 | 修复并开草稿 PR | 后续阶段 |
| 依赖升级（`dependency.upgrade`） | 依赖更新 PR 且 CI 失败或大版本 | 评估变更、适配代码、写风险说明 | 规划中 |
| PR 预审（`pr.review`） | 外部 PR 新建 | 对照仓库约定，指出风险 | 规划中 |

## 路线

| 里程碑 | 内容 |
|---|---|
| MVP | 事件到任务，分诊（只读），收件箱与人的回应 |
| CI 维护线 | CI 失败的判断与修复，首次引入沙箱与编码引擎 |
| issue 修复线 | 复现与修复 |
| 运行时机制 | 权限规则、Hooks、子智能体、会话恢复等 |
| 评测与打磨 | 各模块评测、文档与演示 |

任务与进度在 GitHub 的 [Milestones](https://github.com/ETOLucy/RepoAegis/milestones) 与 Issues 中管理，清单见 [docs/milestones.md](docs/milestones.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/design-draft.md](docs/design-draft.md) | 设计草案：架构、领域模型、状态机、核心流程、数据模型、处理器、运行时、安全 |
| [docs/milestones.md](docs/milestones.md) | 里程碑与任务清单 |
| [docs/diagrams/](docs/diagrams/) | 设计文档用的图（SVG 源文件与 PNG） |

## 参与

项目处于设计阶段，设计文档里"待验证事项"一节列出了尚未核实的假设。任务以 GitHub issue 管理，从 MVP 里程碑开始。

## 许可证

Apache-2.0，见 [LICENSE](LICENSE)。
