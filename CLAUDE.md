# RepoAegis

事件驱动的代码仓库维护平台：监听 GitHub 仓库事件，按事件类型交给处理器（Handler），由 agent 或规则处理，需要信息、判断或授权时在 GitHub 里与人交互。当前处于设计阶段，没有实现代码。

文档：

- `README.md`：项目简介、处理器与路线概览，面向外部读者
- `docs/design-draft.md`：设计草案（唯一的设计来源）
- `docs/milestones.md`：里程碑与任务清单，对应 GitHub Milestones
- `docs/diagrams/`：设计文档用的图，`.svg` 是源文件，`.png` 由它渲染

## 工作约定

- 用中文回复和写文档；写成设计稿，不写成对话记录；去口水话、信息集成、只留一份。
- 命令一律给 Git Bash 写法，不用 PowerShell。
- 先设计后写代码：设计未确认前不写实现代码；一次性的验证代码放临时目录，不进仓库。
- 外部事实要标明是否核实：查到来源的写进 `design-draft.md` §18，没查到的写进 §17 待验证事项，不当事实用。
- 不要把没有核实的内容写成确定的结论；不确定就写"待验证"。
- 不要提交 git，除非用户明确要求。
- 删除或覆盖文件前先看目标内容；删除命令只能用 `&&` 接在已确认成功的上一步之后，不要用换行或分号串联。

## 命名

- 一类事件的处理方式叫"处理器 Handler"，不再叫"剧本 Playbook"。
- "分诊"在文档标题、里程碑名、issue 标题与正文里一律写中文；代码标识符保留 `issue.triage`，处理器表里写作"分诊（`issue.triage`）"。
- 文档标题和文件名不加 `v2`。
- 里程碑按 `docs/milestones.md` 的五个名称：MVP、CI 维护线、issue 修复线、运行时机制、评测与打磨；阶段编号 M0 到 M7。

## 新模块的工作流程（每个模块对应一个 issue）

每次开始做一个新模块，都按下面的顺序走，不跳步：

1. **对应 issue**：确认这个模块对应哪个 GitHub issue（或父 issue），读它的目标与完成标准，范围不清先问用户。
2. **读设计**：读 `docs/design-draft.md` 里相关的章节与图，指出这个模块涉及哪些表、状态、流程、接口、图。
3. **讨论设计**：与用户讨论这个模块的设计。讨论中没有敲定的内容不写进设计文档，只放进 §17 待验证事项，或记在对应 issue 的评论里；设计文档只写"当前的设计"，不记录讨论过程。
4. **敲定后回写文档**：用户确认后，按下面的"同步规则"表更新所有受影响的文档，包括 `design-draft.md` 的对应章节、`docs/diagrams/` 里的图（SVG 与 PNG 一起改）、`milestones.md`、README；把结论写进对应 issue 的评论或描述。文档更新完，再向用户汇报改了哪些文件。
5. **写代码**：设计与文档敲定之后才开始写代码；代码在独立分支上做，PR 描述里写明对应的 issue 与设计章节（如 `Closes #编号`、`设计：§4.4`）。
6. **实现后核对**：实现与设计有出入时，先问用户是改代码还是改设计，改定后同步文档，不让文档与代码长期不一致。
7. **收尾**：PR 合并后确认 issue 已关闭，父 issue 的进度已更新；有新增的外部事实，补进 §18 参考资料或 §17。

## 同步规则：改一处，同步其他处

改动前先 `grep` 找影响面；改完逐项核对下表，再向用户汇报同步了哪些文件。

| 改动 | 必须同步 |
|---|---|
| `design-draft.md` 的章节编号或标题 | `milestones.md` 里所有 `§x.y` 引用 |
| 增删改处理器（§7）或分诊规则（§7.3） | §4.1 事件处置清单、图 04、`milestones.md` 任务、§16 |
| 事件处置清单（§4.1） | 图 04、`milestones.md` 的 M1 任务 |
| 任务状态机（§4.4）或状态名 | 图 05、§6.1 与图 09 的 `TASKS.state`、§5 与图 06 到 08 |
| 表结构或约束（§6） | 图 09、领域模型（§3）与图 03、§6.2、§6.3、`milestones.md` 的 M1 与 M3 任务 |
| 核心流程（§5） | 图 06、07、08 |
| 接口或模块（§2.3、§8） | 图 02、图 10、§16、`milestones.md` |
| 里程碑内容（§16） | `milestones.md`，反之亦然；README 的"路线" |
| 处理器清单（§7.2）或定位（§1） | README 的"简介""处理器"两节 |
| 图 01、02 | README 里引用的这两张图 |
| 项目状态变化（如开始写代码） | README 顶部的"状态"与"当前状态"说明 |
| 待验证事项（§17） | `milestones.md` 中标 `verify` 的任务，两边保持一致 |
| 引入新的外部事实 | §18 参考资料，并在 §17 标明是否已核实 |
| 术语变更 | 所有文档与图，用 `grep` 查旧词，确认无残留 |

## logo 资源

- `assets/repo-aegis-mark-{16,32,64,256,512}.png` 与两个 SVG：方形画布，宝石居中并留约 6% 边距，用作图标、头像等需要方形的场合。
- `assets/repo-aegis-mark-tight.{svg,png}`：紧贴宝石裁切、四周几乎无留白，README 头部用它（`height="90"`），并放在 `<h1>` 里、用 `<br>` 与标题文字换行，避免 GitHub 给标题加的上边距造成大块留白。
- logo 不带描边：原设计的浅灰描边（标准版）和紫罗兰描边（反色版）已去掉，所以两个方形 SVG 现在图形相同。
- logo 的形状固定为原版的五个渐变切面（青、粉、蓝、橙、黄），保持尖角，不换成钻石、圆角、倒影、光谱七色等方案；这些方案已评估并否决，不要再提。
- 改 logo 时两套一起更新；透明底必须保留。

## 图的规则

- 图画成 PPT 风格的清晰矢量图，不写 Mermaid 代码块。
- 图的源文件是 `docs/diagrams/NN-name.svg`，SVG 根元素要有数值型 `width`、`height` 和 `viewBox`；`design-draft.md` 引用同名 `.png`。
- 改图时 `.svg` 与 `.png` 一起更新，并打开 `.png` 目视检查：文字不溢出，连线不穿框，与正文一致。
- 配色含义固定：蓝 = RepoAegis 自己的模块，紫 = agent 相关，绿 = 数据存储，橙 = 外部系统，灰 = 中性或流程步骤，红 = 丢弃、取消、失败、超时。
- 渲染 `.svg` 为 `.png`（Edge 无头模式，在 Git Bash 下验证可用；`W`、`H` 取 SVG 根元素的 `width`、`height`）：

```bash
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --disable-gpu \
  --hide-scrollbars --force-device-scale-factor=2 --window-size=W,H \
  --screenshot="D:/Repos/Agents/RepoAegis/docs/diagrams/NN-name.png" \
  "file:///D:/Repos/Agents/RepoAegis/docs/diagrams/NN-name.svg"
```

| 图 | 文件 | 对应章节 |
|---|---|---|
| 01 | `01-workflow` | §1.2 工作方式 |
| 02 | `02-architecture` | §2.1 总体架构 |
| 03 | `03-domain-model` | §3 领域模型 |
| 04 | `04-event-routing` | §4.2 事件路由 |
| 05 | `05-task-states` | §4.4 任务状态机 |
| 06 | `06-seq-triage` | §5.1 分诊与追问 |
| 07 | `07-seq-ci-repair` | §5.2 CI 失败维护 |
| 08 | `08-seq-outbox` | §5.3 事务与发件箱 |
| 09 | `09-er` | §6.1 ER 图 |
| 10 | `10-runtime-classes` | §8.1 Agent 运行时 |
| 11 | `11-responsibility-flow` | §9.4 责任模型 |
