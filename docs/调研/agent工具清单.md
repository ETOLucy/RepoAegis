# 调研报告：给 coding agent 的工具清单

> 调研日期：2026-10-25。源码由调研子 agent 用 `gh api` 读取当时的主分支，文章和文档读的是原文；本报告转述其结果，数字未逐条再复核。标"自报"的是项目自己公布的成绩，不是对照实验；标"推断"的是从源码或文档推出来的，原文没有直说。

## 一、为什么做这次调研

RepoAegis 的 agent 跑在 GitHub Actions 的 agent 作业里，要读代码、改代码、跑测试，最后交出一份补丁和一份固定格式的汇报。它手里有哪些工具，决定了它能做什么、容易在哪里出错，也决定了我们要写多少代码。工具清单没有标准答案，所以先看官方项目实际给了什么、有没有实验数据说明哪种更好，再结合我们的约束来定。

我们的约束（见 [安全设计](../安全设计.md)、[agent 怎么干活](../agent怎么干活.md)）：agent 作业里没有 GitHub 令牌、没有 sudo，网络只放行白名单；仓库浅克隆（深度 1，默认分支）；模型主要用 DeepSeek（`deepseek-flash`，OpenAI 兼容的工具调用格式）。已经定下的：要有"读取被引用 issue"的工具；长内容只给首尾，全文用工具读；不做查看提交历史和其他分支的工具；用结构化的提交工具交出汇报。

## 二、方法

读了五家的工具定义源码或官方工具文档：mini-swe-agent（`actions_toolcall.py`、`agents/default.py`、`config/mini.yaml`、`config/benchmarks/swebench.yaml`），SWE-agent（`config/default.yaml` 及 `tools/` 下的编辑、提交、搜索、查看工具），OpenHands 新版 SDK（`OpenHands/software-agent-sdk` 的各工具定义、`preset/default.py`、`gpt5.py`、`gemini.py`、`utils/truncate.py`），Codex CLI（`codex-rs/core/src/tools/` 下的工具规格、`features/src/lib.rs`），Claude Code（官方 tools reference 页）。

读了有数字的对照来源：SWE-agent 论文（arXiv 2405.15793）Table 1、Table 3；arXiv 2609.20804（2026-09-17，现代模型上的 harness 设计实证，读了前 10 万字）Table 3；Anthropic 工程文章《The "think" tool》（2025-03-20）、《Writing effective tools for agents》（2025-09-11）、SWE-bench Sonnet 文章（2025-01-06）；OpenAI GPT-4.1 prompting guide；SWE-bench 排行榜数据文件 `data/leaderboards.json`。

另读了：OpenHands 1.0.0 resolver 的引用抽取代码（`resolver/utils.py`、`resolver/interfaces/github.py`），claude-code-action 的取数和 MCP 安装代码（`fetcher.ts`、`install-mcp-server.ts`），DeepSeek 官方文档的 Tool Calls、Thinking Mode、Chat Completion API 参考页。

没读到或没读全：arXiv 2609.20804 后半部分；arXiv 2607.10569、《Is Bash All You Need?》、Verdent 报告（检索到未读）；Codex exec 输出的截断策略。

## 三、各家的工具清单

| 项目 | 默认工具 | 输出过长时 |
|---|---|---|
| mini-swe-agent | 只有 `bash`；每条命令在新的子 shell 里执行，`cd` 和环境变量不保留；SWE-bench 配置单条命令超时 60 秒 | 不足 1 万字符原样返回；超过则只给开头 5000 和结尾 5000 字符，写明省略多少，并提示换个命令 |
| SWE-agent（默认配置） | `bash`、`str_replace_editor`（查看、新建、替换、插入、撤销）、`submit` | 编辑器输出超过 16000 字符截断并标注；Python 文件过长时查看改为显示文件骨架（省略长函数体）；所有输出另有 10 万字符上限 |
| OpenHands SDK（默认预设） | `terminal`（持续存在的 shell 会话）、`file_editor`（同上的编辑器）、`task_tracker`（待办清单），每个 agent 另自带 `finish` 和 `think` | terminal 上限 30000 字符，保留首尾、截掉中间，全文另存文件并把路径和截断位置告诉模型；编辑器上限 16000 字符 |
| OpenHands（GPT-5 预设） | 用 `apply_patch`（补丁格式）替换编辑器 | — |
| OpenHands（Gemini 预设） | 用读、写、改、列目录四个工具替换编辑器 | — |
| Codex CLI | `exec_command` 加 `write_stdin`、`view_image` 默认开；`apply_patch` 按模型决定；网页搜索默认缓存模式；`update_plan` 默认不注册（推断） | 单条命令默认最多返回 1 万 token |
| Claude Code | Bash、Read、Edit、Write，以及 Glob、Grep、任务类工具等 | Read 每次最多 2.5 万 token，大文件要分页读；Bash 约 3 万字符以内直接返回，更长存文件给预览 |

几点补充。OpenHands 的 glob、grep 工具有定义，但三个预设都没放进去。Claude Code 在 macOS、Linux、WSL 上默认**不提供** Glob 和 Grep，让模型在 Bash 里用 find、grep；它的待办和任务类工具也只在较旧的模型上默认提供（tools reference 页，原因未写）。共同的底子是 **bash + 编辑器 + 结束/提交工具**。

## 四、有数据的取舍

### 只给 bash，还是加专用工具

SWE-agent 论文（SWE-bench Lite，GPT-4 Turbo，旧模型）：

| 配置 | 解决率 |
|---|---|
| 完整工具界面 | 18.0% |
| 只给 shell | 11.0% |
| 去掉编辑命令 | 10.3% |
| 有编辑命令但编辑后不做语法检查 | 15.0% |

arXiv 2609.20804 在现代模型上重新比较（SWE-bench Verified；完整工具集为读、写、改、列目录、按名找文件、搜文本、bash、取网页）：

| 模型 | 完整工具集 | 只给 bash |
|---|---|---|
| Nemotron-3 30B | 25.2% | 10.2%（显著） |
| Nemotron-3 120B | 44.0% | 42.4% |
| Nemotron-3 550B | 65.8% | 69.4%（显著；成本 $2.33 降到 $1.11） |
| Mistral-3.5-128B | 68.6% | 45.4%（显著） |

作者的结论：专用工具能扶一把"用不好 bash 的模型"，对擅长 bash 的模型可能是负担。论文里没有 DeepSeek。

mini-swe-agent 只给 bash，排行榜上 DeepSeek V3.2 为 70.0%（mini 2.0.0，2026-02-17，自报），Claude 4.5 Opus 为 76.8%（自报）。

### 编辑工具的写法

Anthropic 的 SWE-bench 文章：试过的编辑方式里字符串替换"highest reliability"，并强制用绝对路径，因为模型 `cd` 之后会把相对路径弄错（当时 Verified 49%）。OpenAI GPT-4.1 指南：模型在补丁格式上"extensively trained"，搜索-替换格式和伪 XML 格式成功率也高；这几种格式的共同点是不靠行号定位、同时给出原文和替换文。OpenHands 按模型换编辑工具，只有源码注释作依据，没有评测数字。整文件重写没有对照数字。DeepSeek 只找到 Aider 给旧版 deepseek-chat/reasoner 用搜索-替换格式的配置，`deepseek-flash` 没有资料。

### 查看文件

SWE-agent 论文：一次看 30 行 14.3%，100 行 18.0%，整文件 12.7%，"太少或太多都会降低表现"。后来的主流做法是带行号、可以分页、过长截断并提示先用 `grep -n` 定位；这种做法和 100 行窗口之间没有对照数据。

### 搜索

SWE-agent 论文：汇总式搜索 18.0%，迭代式（逐条翻）12.0%，不给搜索 15.7%；迭代式变差是因为模型把每条匹配都翻一遍，耗尽预算或上下文。它的目录搜索匹配超过 100 个文件就报错要求缩小范围。

### 计划工具和思考工具

Anthropic《The "think" tool》：τ-bench 航空场景 pass^1 从 0.332 到 0.404（只加工具）、0.584（加工具和优化提示词）；SWE-bench 平均提升 1.6%（Claude 3.7）。2025-12-15 补注：多数情况改推荐模型自带的思考能力。计划工具（task_tracker、update_plan、TodoWrite）没有找到效果数字；GPT-4.1 指南里"先做计划"提升 4% 是写在提示词里，不是工具。

### 提交工具

mini-swe-agent：执行一条特殊 echo 命令，后面的输出就是提交内容。SWE-agent：第一次调用不提交，返回完整改动和复查清单（删掉复现脚本、还原改过的测试），第二次调用才交出 `git diff` 生成的补丁。OpenHands：`finish` 默认只有一个 message 字段，可挂结构化格式；实验性的任务结果字段为 status（success / partial_success / blocked / failed / unknown）加摘要。

### 写工具的通用建议

Anthropic《Writing effective tools for agents》："More tools don't always lead to better outcomes."；功能重叠的工具会让 agent 分心；返回有意义的内容，少用底层标识；分页、范围选择、过滤、截断要有合理默认值（Claude Code 默认把工具结果限制在 2.5 万 token）；错误信息要写"具体、可操作"的改法。

## 五、"读取被引用 issue"怎么实现

OpenHands 1.0.0 resolver 的 `extract_issue_references`：先去掉代码块、行内代码和带 `#` 的网址，再用正则抽 `#数字`，用令牌取回对方正文拼进提示词，是开工前预取。注意：这一步只在处理 PR 评审意见的分支里做，处理 issue 的同名函数直接返回空。claude-code-action 不预取引用，只有用户显式开放 GitHub MCP 工具时，模型才能在运行中自己查（要把令牌交给运行中的工具）。

在我们的约束下（agent 作业无令牌、网络受限），运行中自己查走不通（推断）。可行做法：由有令牌的检查作业按上面的正则抽出引用、预取、清洗、截断，存成文件交给 agent 作业，agent 的工具只读这份文件；没预取到的编号返回明确错误。（2026-11-01 改为 agent 运行中按需读，令牌只在容器外的读 issue 服务里，见 [agent运行中读issue](agent运行中读issue.md)。）DeepSeek 文档写明不能在对话中途插入"非模型生成的工具调用"，所以预取内容只能放进用户消息或文件，不能伪造成一次工具调用的结果。

## 六、DeepSeek 的工具调用限制（api-docs.deepseek.com 原文，2026-10-25）

- 接口参考里的模型只列 `deepseek-flash` 和 `deepseek-v4-pro`。
- 工具个数上限当前文档没写；函数名只能用字母、数字、下划线、短横线，不超过 128 字符。
- 思考模式下不支持"必须调用工具"和"指定调用某个工具"，会返回 400。所以没法强制模型最后一定调用提交工具，要自己处理"没调用工具"的回复。
- 只要请求带了工具，之前所有轮次的思考内容（`reasoning_content`）都必须原样回传，否则返回 400。
- 严格模式（Beta）：保证工具参数严格符合格式定义；要换到 `https://api.deepseek.com/beta`，所有函数都要设为严格，每个对象的属性都必须列为必填并禁止多余属性，不支持长度和个数限制，支持枚举。

## 七、各项目：借鉴什么，不借鉴什么

**mini-swe-agent。** 借鉴它的整体设计：主循环 190 行，结构清楚；每条命令单独执行、不保留 `cd`，最不容易出状态错乱；输出过长保留首尾并提示换命令；模型没按格式调用工具时打回重来、连续 3 次退出，这正好用来处理 DeepSeek 不能强制调用工具的问题。不把它直接当库用：它的工具列表、回复解析、动作执行、交卷判断四处都写死了只有 bash，交卷靠检查输出第一行文字，加我们的三个工具至少要改三处核心代码；它经 litellm 调模型，能否原样回传 DeepSeek 的思考内容未核实。

**SWE-agent。** 借鉴它的字符串替换编辑器（原文必须唯一，绝对路径，带行号查看，16000 字符截断）和"两次提交"的交卷方式，这两样都有论文消融或长期使用作依据。不借鉴它的整套工具包和配置体系（为研究多种界面设计而做，对一个工具清单固定的产品太重），也不照搬它为旧模型设计的 100 行查看窗口和自定义搜索命令，改用带行号查看加 bash 里的 `grep`。

**OpenHands SDK。** 借鉴它的截断做法：首尾保留、全文另存文件、把路径和截断位置告诉模型，和我们"长内容给首尾、全文用工具读"的决定一致；借鉴结束工具挂结构化字段、任务结果用固定状态值的思路。不借鉴持续存在的 shell 会话（要处理输入、超时、重置，复杂度高，收益没有数据）、待办清单工具（没有效果数据）、浏览器、按模型家族切换预设（没有评测数字，我们只用一个模型家族）。

**Codex CLI。** 补丁格式编辑保留为备选，等实测发现 DeepSeek 用字符串替换出错多时再试。不借鉴它的语法约束式补丁工具（不走 JSON，DeepSeek 的 Chat Completions 接口能否支持未知）、网页搜索（网络受限）、看图工具（暂不处理带图片的 issue）、计划工具（它自己也默认不开）。

**Claude Code。** 借鉴编辑时"原文不唯一就报错、可选全部替换"的规则，读文件的分页参数，命令输出的长度上限；它在 Linux 上默认不给专用搜索工具，是我们不单设搜索工具的先例。不借鉴它的大工具集、子 agent 和任务工具、网页工具；它闭源，只能参考文档写明的行为。

**claude-code-action 和 OpenHands resolver。** 借鉴前者的开工前预取、清洗、只用触发那一刻的内容（已写进安全设计），借鉴后者抽取 `#数字` 引用的规则。不借鉴前者让运行中的 agent 拿着令牌通过 MCP 工具查 GitHub，这和"agent 手里没有令牌"冲突。（2026-11-01 改为 agent 运行中按需读，令牌只在容器外的读 issue 服务里，见 [agent运行中读issue](agent运行中读issue.md)。）

**Anthropic 的思考工具。** 不单设思考工具：它的提升数字来自 Claude 3.7，Anthropic 后来自己也改推荐模型自带的思考能力，而 DeepSeek 默认开着思考模式。

## 八、据此的决定（用户已采纳，2026-10-25）

已定：给 agent 四个工具：`bash`（执行命令，单独执行、有超时、输出首尾截断）、`str_replace_editor`（带行号查看、新建、唯一字符串替换、插入，绝对路径，拒绝修改受保护文件）、`read_issue_ref`（只读检查作业预取的引用文件；2026-11-01 改为 agent 运行中按需读，令牌只在容器外的读 issue 服务里，见 [agent运行中读issue](agent运行中读issue.md)。）、`submit`（结构化汇报，两次确认，补丁由程序用 `git diff` 生成）。不给专用搜索、计划、思考、上网、查历史、查其他分支的工具。agent 循环照 mini-swe-agent 的设计自己写，不直接当库用。编辑器的写法和 DeepSeek 的实际表现，等真实代码跑起来后在测试仓库里验证；每个工具的细节（截断长度、超时、受保护文件名单、提交时的确认话术等）在写到那一块代码时再细查。

## 九、尚未查清

1. `deepseek-flash` 的 bash 能力，以及它在字符串替换和补丁格式上的表现：没有公开数据。
2. DeepSeek 当前有没有工具个数上限。
3. litellm 是否原样回传 DeepSeek 的思考内容。
4. arXiv 2609.20804 后半部分（各模型 bash 熟练度的定义、其他基准上的对比），以及检索到未读的几篇。
5. Codex 命令输出的截断策略；OpenHands 按模型换编辑工具有无评测；Claude Code 默认去掉搜索工具和任务工具的原因。
6. 经 AWF 防火墙匿名访问 api.github.com 是否可行。
