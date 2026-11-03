# 调研报告：仓库里写给 agent 的说明文件要不要交给 agent

> 调研日期：2026-10-26。源码和文档均为本次亲自打开的一手资料，写明位置；样本仓库的文件统计是我用 GitHub 接口实测的。标"推断"的是我推出来的，原文没有直说。

## 一、为什么查

越来越多的仓库会在根目录放一个写给 AI 看的说明文件，最常见的是 `AGENTS.md`，也有 `CLAUDE.md`、`GEMINI.md`、`.github/copilot-instructions.md`。里面写着怎么装依赖、怎么跑测试、代码风格、哪些地方别动。agent 在这个仓库里改代码，这些信息很有用。问题是：要不要由程序主动读出来放进 agent 的第一条消息，还是让 agent 自己去读，或者不管。

已定的背景：agent 第一条消息的主体是打标签那一刻的 issue 标题和正文；代码不放进消息，仓库克隆在默认分支上，agent 用工具自己读；agent 的补丁不许改这类说明文件（[安全设计](../安全设计.md)"发布前检查产出"一节）。

## 二、方法和出处

- Codex：`openai/codex` 主分支 `codex-rs/core/src/agents_md.rs` 第 1–16、42–49、68、150–164 行，`codex-rs/core/src/config/mod.rs` 第 255–257 行。
- Copilot：`github/docs` 的 `content/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-repository-instructions.md` 第 28–45 行，`data/reusables/copilot/custom-instructions-agents.md`。
- OpenHands 新版 SDK：`OpenHands/software-agent-sdk` 主分支（提交 `ce5716a3c0`）`openhands-sdk/openhands/sdk/skills/skill.py` 第 346–351 行、`skills/utils.py` 第 338–380 行；包装成"不可信内容"的写法见 [开工前的判断与注入文字](开工前的判断与注入文字.md) 引用的 `sections/dynamic.py` 第 59–67、86–92 行。
- OpenHands 1.0.0 resolver：`openhands/resolver/issue_resolver.py` 第 596–603 行，模板 `prompts/resolve/basic-conversation-instructions.jinja`。
- SWE-agent、mini-swe-agent：在两个仓库里搜 `AGENTS.md`，SWE-agent 无结果；mini-swe-agent 只搜到它自己仓库给开发者用的 `CLAUDE.md`，没有读取这类文件的代码。
- claude-code-action：`docs/security.md` 第 54–58 行。
- 样本：上一份报告用的 12 个仓库（[issue引用与打标签前后的修改](issue引用与打标签前后的修改.md)），2026-10-26 用接口查根目录有没有这些文件、多大。

## 三、各家怎么做（一手）

**Codex：自动读，设总长上限。** 从项目根目录（默认以 `.git` 所在处为根）一路往下到当前工作目录，把沿途每一层的 `AGENTS.md` 拼在一起放进指令；还可以配置备用文件名。所有内容合计默认最多 32 KiB，超出部分"静默截断"，源码注释说是为了不占太多上下文。

**Copilot：自动读，好几种文件都认。** 仓库级的 `.github/copilot-instructions.md`；按路径生效的 `.github/instructions/*.instructions.md`；写给 agent 的 `AGENTS.md`，可以放在仓库任何位置，"离正在处理的文件最近的那个优先"；也可以只在根目录放一个 `CLAUDE.md` 或 `GEMINI.md`。文档没写长度上限。

**OpenHands 新版 SDK：自动读，但标成不可信。** 认 `.cursorrules`、`AGENTS.md`、`CLAUDE.md`、`GEMINI.md`，根目录和子目录都找。放进提示词时包在"不可信内容"的说明块里，写明这些是仓库贡献者写的，可能含有注入指令。

**OpenHands 1.0.0 resolver：只认自己的文件。** 只读仓库根目录的 `.openhands_instructions`，整篇原样放进"关于这个仓库的基本信息"一段，不截断。

**SWE-agent、mini-swe-agent：不读。** 它们为评测设计，评测仓库一般没有这类文件。

**claude-code-action：自己的 Claude 配置一律用基础分支的版本。** 处理 PR 时，它把 `CLAUDE.md`、`.claude/` 等文件恢复成 PR 目标分支上的版本，因为 PR 里的版本可能是外部人写的。

## 四、样本里有多常见（实测）

12 个仓库里，根目录有 `AGENTS.md` 的有 2 个：axios（15,005 字节）和 lazygit（27,624 字节）。lazygit 另有一个 58 字节的 `CLAUDE.md`，axios 另有一个 6,119 字节的 `.github/copilot-instructions.md`。按上一份报告的换算，lazygit 的 `AGENTS.md` 约 8,300 token，比样本里 99% 的 issue 正文还长。

`CONTRIBUTING.md`（写给人看的贡献指南）在 7 个仓库的根目录里有。没有一家官方 agent 会自动读它。

## 五、我们的情况有什么不同（推断）

**可信度。** 我们只克隆默认分支。默认分支上的文件，要么是有写权限的人直接提交的，要么是经过他们合并的，可信度和代码本身一样，比 issue 正文高。claude-code-action 担心的"PR 里的版本是外部人写的"，在我们这里不存在。但 OpenHands 的提醒仍然成立：文件内容由人写，可能写得不对，也可能被人夹带了东西混进去。

**会和我们的规则冲突。** 这类文件常写"提交前跑某某命令""PR 标题按某格式写""往某分支提 PR""发版步骤"等。在 RepoAegis 里，推送、开 PR 都由程序做，agent 碰不到；agent 也不许改 `.github/` 和这类说明文件。所以必须告诉 agent：我们的规则优先，说明文件里和这些冲突的部分不照做。

**时间差。** 检查作业和 agent 作业在两台机器上，各克隆一次。如果中间有人往默认分支推了代码，两次看到的说明文件和代码就不一样。检查作业要记下当时默认分支的提交号，agent 作业按这个提交号克隆。这一条不只是说明文件的问题，整个仓库都适用。

## 六、各项目借鉴什么、不借鉴什么

- **Codex：借鉴"自动读 `AGENTS.md`、设总长上限 32 KiB"。** 不借鉴"静默截断"：截断时要在内容里写明"后面省略了多少"，和我们对长内容的一贯做法一致。
- **Copilot：借鉴"认 `AGENTS.md`，没有的话认 `CLAUDE.md` 等"。** 不借鉴路径专用的 `.github/instructions/`，它要按正在改的文件去匹配，规则复杂，agent 需要时可以自己去读。
- **OpenHands SDK：借鉴"标明这是仓库里的人写的、优先级低于我们的规则"。** 不借鉴"标成不可信"的强度：我们只读默认分支，可信度比它面对的情况高。
- **OpenHands resolver：不借鉴。** 它只认自己发明的文件名。
- **SWE-agent：不借鉴"完全不读"。** 真实仓库里有这类文件，读了能少走弯路。
- **claude-code-action：借鉴"只用基础分支的版本"这条原则。** 我们只克隆默认分支，天然满足。

## 七、建议（2026-11-02 用户定："程序放进去"，按下面做）

程序在克隆后读仓库根目录的 `AGENTS.md`；没有的话，依次找 `CLAUDE.md`、`GEMINI.md`、`.github/copilot-instructions.md`，只用找到的第一个。把它放进第一条消息，标明"这是仓库维护者写的说明，和 RepoAegis 的规则冲突时以 RepoAegis 为准"，超过 32 KiB 就截断并注明。子目录里的 `AGENTS.md` 只列出路径，agent 改到那里时自己去读。`CONTRIBUTING.md` 不主动放。

## 八、尚未查清

1. 这类文件里常见哪些和我们规则冲突的写法，没有统计；可以在写提示词时再抽样看。
2. DeepSeek 对"仓库说明"和"系统规则"冲突时的服从程度，没有数据，要在测试仓库里试。
3. 只取一个文件是否够：axios 同时有 `AGENTS.md` 和 `.github/copilot-instructions.md`，两者内容是否重复，没有比对。
