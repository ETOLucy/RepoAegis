# 调研：agent 要不要查 git 历史、该基于哪个分支工作

> 本报告 2026-10-24 写在调研会话的笔记目录（原文件名 `research-git-history-branch.md`），2026-10-25 原样搬进 `docs/调研/`，正文没有再复核，行号和版本以当时为准。据此的决定见 [agent 怎么干活](../agent怎么干活.md)：只在默认分支上工作，不给查历史和看其他分支的工具。补充调研见 [克隆深度补充调研](克隆深度补充调研.md)。

调研日期：2026-10-24。所有源码都是当天从 GitHub 拉取的最新版本（另有说明的除外），行号是近似值。

标注约定：
- 【一手】：本次亲自打开了源码、官方文档或论文原文，写的是原文的意思。
- 【推断】：根据一手材料推出来的，原文没直接这么说。
- 【待验证】：只看到二手说法，或者没有核对原文。

几个术语先说明一次：
- 浅克隆（shallow clone）：克隆时只下载最近一个或几个提交，不带更早的历史。`git clone --depth 1` 就是浅克隆，这时 `git log` 只能看到一条记录，`git blame` 也基本没用。
- 基础分支（base branch）：agent 从哪个分支切出新分支，PR 最后也合回这个分支。
- 默认分支（default branch）：仓库设置里的主分支，通常叫 `main` 或 `master`。

---

## 问题甲：要不要给 agent 一个"查看 git 历史"的工具

### 1. 各家 agent 有没有专门的 git 历史工具

结论先说：**被查的这几家都没有专门的"查历史"工具**。能查历史的，都是让 agent 自己在命令行里跑 `git log` / `git blame`。区别在于两点：提示词里提没提，运行环境里有没有历史可查。

**SWE-agent**
- 【一手】默认配置 `config/default.yaml` 只挂了三组工具：`tools/registry`、`tools/edit_anthropic`（文件查看和编辑工具 `str_replace_editor`）、`tools/review_on_submit_m`（提交工具 `submit`），另外 `enable_bash_tool: true` 开了命令行（约第 43–47、66 行）。`tools/` 目录下其余工具（search、windowed、filemap、diff_state、forfeit 等）也没有一个是查 git 历史的。git 只在两个地方出现：设置 `GIT_PAGER: cat`（第 40 行，防止 `git log` 卡在翻页界面），以及提交前让 agent 用 `git checkout --` 还原测试文件（第 56 行）。
  出处：https://github.com/SWE-agent/SWE-agent/blob/main/config/default.yaml
- 【一手】直接修 GitHub 仓库时，SWE-agent 克隆仓库用的是浅克隆：`sweagent/environment/repo.py` 里 `GithubRepoConfig.copy()` 执行 `git fetch --depth 1 origin <base_commit>`（约第 177 行）。也就是说，**这种用法下 agent 根本没有历史可查**。
  出处：https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/environment/repo.py
- 【推断】跑 SWE-bench 时，SWE-agent 用的是 SWE-bench 预先做好的镜像，能看到多少历史由镜像决定（见下文第 3 节）。

**mini-swe-agent**
- 【一手】只有一个能力：每轮执行一条 bash 命令。默认提示词 `src/minisweagent/config/default.yaml` 和 SWE-bench 提示词 `config/benchmarks/swebench.yaml` 都没有提 `git log` 或 `git blame`。SWE-bench 那份里，git 只用来在最后生成补丁（约第 77–81 行，`git diff ... > patch.txt`）。
  出处：https://github.com/SWE-agent/mini-swe-agent/tree/main/src/minisweagent/config

**OpenHands**（主体现在在 `OpenHands/software-agent-sdk` 仓库；`OpenHands/OpenHands` 已经变成前端仓库）
- 【一手】工具目录 `openhands-tools/openhands/tools/` 里有 terminal、file_editor、grep、glob、apply_patch、browser_use、delegate、task_tracker 等，没有 git 历史工具。
- 【一手】系统提示词片段在 `openhands-sdk/openhands/sdk/context/prompts/sections/static.py`：
  - `EfficiencySection`（约第 184 行）：探索代码时可以用 find、grep 和带过滤条件的 git 命令，目的是少走几步。原文："use efficient tools like find, grep, and git commands"。
  - `VersionControlSection`（约第 225–230 行）：讲的是提交规范、别推到 main，以及跑 `git log` / `git show` 时要加 `--no-pager`。
  - 没有任何一句讲"什么情况下该去查历史"。
  出处：https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/context/prompts/sections/static.py

**Codex CLI（openai/codex）**
- 【一手】默认基础提示词 `codex-rs/protocol/src/prompts/base_instructions/default.md` 的 "Task execution" 一节，约第 141 行，原文是："Use `git log` and `git blame` to search the history of the codebase if additional context is required."（意思：需要更多背景时，用这两个命令查代码库历史。）紧接着第 144 行：没有明确要求时，不要提交、不要建新分支。
  同一句话也出现在 `codex-rs/core/gpt_5_1_prompt.md`（约第 154 行）和 `gpt_5_2_prompt.md`（约第 128 行）。旧路径 `codex-rs/core/prompt.md` 已经没了（2026-04 被挪到 `codex-rs/models-manager/prompt.md`，内容和 default.md 一样）。专门给 codex 系列模型的提示词 `gpt_5_codex_prompt.md`、`gpt-5.2-codex_prompt.md`、`gpt-5.1-codex-max_prompt.md` 里**没有**这句。
  出处：https://github.com/openai/codex/blob/main/codex-rs/protocol/src/prompts/base_instructions/default.md
- 【一手】Codex 也没有专门的 git 工具，靠 shell 命令。

**Claude Code / claude-code-action**
- 【一手】Claude Code 的官方最佳实践文档（https://code.claude.com/docs/en/best-practices ，"Provide specific context in your prompts" 一节的 "Point to sources" 那一行）举了个例子：不要直接问"为什么这个 API 这么怪"，而是让它去翻 git 历史。示例原文："look through ExecutionFactory's git history and summarize how its api came to be"。这是写给**人**看的使用建议，不是系统提示词。
- Claude Code 本身的系统提示词：没找到官方公开的原文，所以无法判断里面有没有"何时查历史"的指导。
- 【一手】claude-code-action 的提示词 `src/create-prompt/index.ts` 里，git 只出现在提交/推送说明，以及处理 PR 时 `git diff origin/<base>...HEAD` 的用法（约第 590、739、834 行）。没有提 `git log` 或 `git blame` 用来查历史。
- 【一手】claude-code-action 在标签/@ 提及模式下默认放行的工具是 `["Glob", "Grep", "LS", "Read"]`（第 34 行），bash 只放行 `git add`、`git commit`、推送脚本、`git rm`（约第 54–60 行）。
  【推断】所以默认配置下 Claude 跑不了 `git log`，除非用户自己通过 allowed tools 把它加进去。
- 【一手】它的示例工作流 `examples/claude.yml` 用 `actions/checkout` 时设了 `fetch-depth: 1`（第 31 行）；`branch.ts` 拉基础分支时，如果原来就是浅克隆，也只拉 `--depth=1`（约第 350、368 行；`fetch-depth.ts` 的注释专门解释了这样做的原因）。
  【推断】所以就算放行了 `git log`，issue 场景下多半也只能看到一个提交。

**Aider**
- 【一手】repo map（仓库地图）的代码 `aider/repomap.py` 用 tree-sitter 解析代码里的定义和引用，再用类似 PageRank 的打分排序，重点参考聊天里提到的文件和符号（约第 365–430 行 `get_ranked_tags`）。没有用 git 历史。`aider/repo.py` 里唯一读历史的地方（约第 382 行 `iter_commits`），只是判断当前分支有没有提交，用来生成 diff。git 在 Aider 里主要用来自动提交和 `/undo` 撤销。
  出处：https://github.com/Aider-AI/aider/blob/main/aider/repomap.py

**Agentless**
- 【一手】`get_repo_structure/get_repo_structure.py` 先克隆仓库、切到 base commit（第 27–61 行），再把仓库转成"文件/类/函数"的结构给模型看。模型自己不跑命令。用 `gh search code` 在仓库里搜 `git log` 和 `blame`，结果为零。
- 【推断】Agentless 完全不用 git 历史。

### 2. 提示词里有没有讲"什么时候该查历史"

- 唯一明确写进系统提示词的是 **Codex**，而且只有一句：需要更多背景时，用 `git log` / `git blame`（见上文）。它没有细分场景，比如没说"回归问题先看最近改动"或"想知道某行为什么这么写就 blame"。
- OpenHands 只说可以用 git 命令高效探索，没说什么时候用。
- Claude Code 文档里那条是给用户的提问建议，场景是"想知道一个 API 为什么长成现在这样"。
- SWE-agent、mini-swe-agent、claude-code-action、Aider、Agentless：都没提。
- 我没找到任何一家官方提示词写"定位回归问题时先查最近改动"这种具体指导。

【一手】侧面数据：SWE-bench 的维护者 john-b-yang 在 issue #465 的评论里（2025-09-09）说，他在最近 20 个提交成绩的约 10000 条运行记录里搜带 `git log` 的命令，只命中 5 条。说明当时 agent 在 SWE-bench 上很少主动查历史。这是他自己说的初步估计，不是正式统计。
出处：https://github.com/SWE-bench/SWE-bench/issues/465

### 3. SWE-bench 的 git 历史作弊问题，以及 SWE-bench 怎么处理

**报告**
- 【一手】SWE-bench issue #465 "Repo State Loopholes During Agentic Evaluation"，2025-09-03 由 jacobkahn 提出，2026-03-24 关闭。内容是：评测镜像里留着"未来"的提交（也就是修复这个 issue 之后才出现的提交），agent 用 `git log --all`、`git log --grep=<issue 号>` 就能直接看到官方修复。例子有：Claude 4 Sonnet 在 `pytest-dev__pytest-6202` 上，Qwen3-Coder 480B 在 `django__django-15572`、`django__django-13513` 上。提出人建议的补救办法是：删掉 origin 远程、删掉所有分支、清掉 reflog（git 记录分支指向变化的日志，里面也可能留着未来提交的说明）。
- 【一手】同一个 issue 里，jlko（2025-09-05）贴了初步统计：4 个提交成绩里，有若干实例被判为"作弊"或"看到了未来信息"。另一位参与者 xeophon 指出，`pytest-dev__pytest-5840` 是个回归问题，可能**只有**查看过去的 tag 才能解。所以过去的历史不该删，只删未来的。
- 【一手】NIST 下属 CAISI 的页面 "Examples of cheating in CAISI's agent evaluations"（https://www.nist.gov/caissi/2-examples-cheating-caisis-agent-evaluations ，页面写的是 2025-11-28 创建）也说，他们在 SWE-bench Verified 上发现 Opus 4 通过仓库的 git log 看到了未来的提交。这一条是通过网页抓取工具读到的摘要，没有逐字核对原文。

**SWE-bench 的处理**
- 【一手】维护者 closji 在 #465 里说（2025-09-11），所有 Verified 镜像都已重建，修复合并在 PR #471（"Fix git log leakage in environment images"，2025-09-11 合并，对应 swebench v4.1.0）。处理原则是：删掉所有未来的历史，保留过去的历史，以及 base commit 之前打的 tag。
- 【一手】现在的实现在 `swebench/image_builder/docker_utils.py` 的 `git_clone_timesafe()`（约第 58–78 行）。步骤依次是：单分支克隆，`git reset --hard <base_commit>`，删除 origin 远程，删掉时间晚于 base commit 的 tag，清空 reflog，`git gc --prune=now` 清除无主对象，最后检查 `git log --all --after=<base 时间>` 必须为 0 条，否则镜像构建失败。
  出处：https://github.com/SWE-bench/SWE-bench/blob/main/swebench/image_builder/docker_utils.py
- 【一手】后续仍有相关的未关闭 issue。#640（2026-08-18）：通过 Conda 缓存可能看到 base commit 之后的源码。#669（2026-09-24）：9 个 Verified 实例从题目描述本身就能找到答案。只看了标题，没细读。
- 【待验证】网上还有几篇二手文章，说 SWE-rebench、DeepSWE 里也有 agent 用 `git log --all` 拿答案，以及某数据集里 87% 的作弊记录涉及 git。都没找到原文，不作结论。

### 4. git 历史对修 bug 有没有帮助：能读到原文的研究

- 【一手】**HAFix**（arXiv 2501.09135，v4，已被 EMSE 2026 期刊接收；作者 Yu Shi、Bram Adams、Ahmed E. Hassan 等）。用 PyDriller（一个读 git 历史的 Python 库）找到"最后一次修改出错那一行的提交"（论文叫 blame commit，第 3 节），从中提炼 7 种历史信息塞进提示词。在 BugsInPy 的 51 个、Defects4J 的 116 个单行 bug 上测试。摘要说，把 7 种合起来用（HAFix-Agg），修复率比不带历史的基线平均提高约 45%（BugsInPy）和 50%（Defects4J）。
  重要前提：论文明确说，他们**直接把出错的那一行告诉了模型**（第 3 节 "Buggy Line Location"），只研究"修"，不研究"找"。用的是 7B 级别的老一代代码模型（Code Llama、DeepSeek-Coder）。
  【推断】这和 RepoAegis 的场景差别很大：我们的 agent 不知道错在哪一行，模型也强得多。结论不能直接照搬。
  出处：https://arxiv.org/abs/2501.09135
- 【一手】**RepoMem: Improving Code Localization with Repository Memory**（arXiv 2510.01003，ICLR 2026）。给负责定位问题代码的 agent（LocAgent）加了 4 个工具：按关键词搜提交说明的 `SearchCommit`、查看某个提交完整 diff 和关联 issue 的 `ExamineCommit`，外加两个查"常改文件摘要"的工具（第 3 节）。
  为了防止看到未来，记忆库里只放 issue 之前的提交（第 3.1 节原文："only includes commits made prior to the issue to be resolved"）。实验时取 base commit 之前的 7000 个提交（第 4.1 节），还过滤掉了和测试题文字重合的 issue 及其提交。
  结果：在 SWE-bench Verified 上，文件级定位前 5 名准确率从 71.6 提到 76.5（表 1），端到端解决率从 37.0% 提到 40.4%（附录 B.2 表 7，模型是 GPT-4o）。提升主要来自提交历史丰富的仓库，比如 pytest、sphinx；历史少的那组反而下降（表 2）。以上数字已在论文 HTML 原文里核对。
  出处：https://arxiv.org/abs/2510.01003
- 【推断】这两篇都说明：历史信息有用，但都是**由框架整理好再交给模型**，而且都严格切在"问题发生之前"。没找到研究"让 agent 自己随手跑 git log"能提高多少解决率的原文数据。

---

## 问题乙：仓库有多个分支时，agent 基于哪个分支工作

### 1. GitHub Copilot coding agent（现在官方文档叫 cloud agent）

- 【一手】文档 `content/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github.md` 的 "Assigning an issue to Copilot" 一节（约第 69–110 行）：把 issue 指派给 Copilot 时会弹出对话框，可以在下拉菜单里选它在哪个仓库工作、从哪个分支切出来。如果不选分支，就用所选仓库的默认分支。原文："If you don't specify a branch ... work from the default branch"（中间有省略）。从对话框或聊天发起任务时，也能选基础分支，PR 会指向这个分支（约第 45、63 行）。
  在线页面：https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/use-cloud-agent-on-github
- 【一手】通过 API 指派时有可选参数，REST 里叫 `base_branch`，GraphQL 里叫 `baseRef`，含义是"Copilot 从哪个分支切出来"（`use-cloud-agent-via-the-api.md` 约第 100–105 行）。
- 【一手】顺带一提：Copilot 运行环境里，你在 `copilot-setup-steps.yml` 给 `actions/checkout` 设的 `fetch-depth` 会被强制覆盖，原因是"让 agent 能按要求回滚提交"（`customize-the-agent-environment.md` 约第 97 行）。具体覆盖成多少没写。

### 2. claude-code-action

- 【一手】`action.yml` 第 19–21 行：输入项 `base_branch`，说明是"创建新分支时用作来源的分支"，默认是仓库默认分支；没有写死的默认值。还有 `branch_prefix`，默认 `claude/`。
- 【一手】`src/github/operations/branch.ts` 约第 266–279 行：如果设了 `baseBranch` 就用它，否则通过 API 查仓库的 `default_branch`。然后拉取这个分支、切过去，再 `git checkout -b` 建新分支（约第 365–372 行）。如果触发它的是一个开着的 PR，就直接切到 PR 的分支（约第 179–260 行）。
- 【推断】这是工作流级别的固定配置，每个 issue 不能单独选分支。

### 3. OpenHands resolver（旧版，`openhands/resolver/`）

- 【一手】这个目录在 `OpenHands/OpenHands` 的 main 分支上已经不存在了，最后能找到的是 tag `1.6.0`（v1.10.0 起就没有了）。以下都按 1.6.0 版本读：
  - `issue_resolver.py` 约第 585–597 行：直接 `git clone <仓库>`，不带 `--depth`，也不指定分支。所以是**完整历史**，处在**默认分支**上。
  - `send_pull_request.py` 约第 342–351 行：PR 的目标分支，如果传了 `--target-branch` 就用它（先检查这个分支存在），否则用默认分支。新分支名是 `openhands-fix-issue-<号>`（约第 337 行）。
  - `.github/workflows/openhands-resolver.yml` 第 14–18 行：工作流输入 `target_branch`，默认值写死 `"main"`，说明是"要拉取并对其开 PR 的分支"。但在这个文件里，`TARGET_BRANCH` 只在第 192 行被设置、第 294 行传给 `send_pull_request`，解决问题那一步没用到它。
  - 【推断】也就是说：agent 是在默认分支上改的代码，PR 却可能开到另一个 `target_branch` 上。两者可能对不上。
  出处：https://github.com/OpenHands/OpenHands/tree/1.6.0/openhands/resolver

### 4. OpenHands/extensions 的 github-issue-to-pr

这个目录里现在有两套实现。

- 【一手】手动安装版（`skills/github-issue-to-pr/SKILL.md` 和 `scripts/main.py`，文档说新安装已不推荐用这一版）：
  - `main.py` 约第 1159 行：基础分支 = 仓库的 `default_branch`，没有参数能改。
  - `_prepare_repository()`（约第 558–576 行）用 `--depth 1 --single-branch --branch <默认分支>` 克隆。函数注释原文："the agent needs the tree, not the history"（agent 要的是代码文件，不是历史）。
  - PR 用 `--base <默认分支>` 打开，默认是草稿（约第 53、487–489、840 行）。SKILL.md 第 321 行也写了"浅克隆、单分支"。
- 【一手】目录里推荐的新版（`scripts/worker.py`）：约第 124 行 `base_branch = self.config.get("base_branch") or repository["default_branch"]`，也就是配置里能指定基础分支，不配就用默认分支。但它的表单 `automations/catalog/github-issue-to-pr/manifest.json` 里只有分支前缀，没有基础分支这一项。
  【推断】要改基础分支只能手改配置。
  出处：https://github.com/OpenHands/extensions/tree/main/skills/github-issue-to-pr

### 5. SWE-agent 的 `open_pr` 钩子

- 【一手】`sweagent/run/hooks/open_pr.py`：
  - 第 48 行：在当前检出的提交上 `git checkout -b swe-agent-fix-#<号>-<随机数>`。
  - 第 100、107 行：PR 的目标分支用 API 查到的 `default_branch`；第 109 行：`draft=True`（开草稿 PR）。
  - 当前检出的是哪个提交，由 `GithubRepoConfig.base_commit` 决定。默认是 `"HEAD"`，即远程默认分支的最新提交；文档注释说也可以填分支名、tag 或提交号（`repo.py` 约第 129–133 行）。
- 【推断】如果用户把 `base_commit` 设成 `dev`，代码是在 dev 上改的，PR 却仍然开到默认分支，两者对不上。这和旧版 OpenHands resolver 是同一类问题。文件开头也写着这部分实现"很快会变"。
  出处：https://github.com/SWE-agent/SWE-agent/blob/main/sweagent/run/hooks/open_pr.py

### 6. GitHub 平台本身

- 【一手】文档 "Creating a branch to work on an issue"（`content/issues/tracking-your-work-with-issues/using-issues/creating-a-branch-for-an-issue.md`，第 19–38 行）：
  - 从 issue 侧栏的 "Development" 区域可以给 issue 建分支，这个功能还在公开预览阶段。
  - 第 27 行：默认在当前仓库、从默认分支创建。原文："from the default branch"。
  - 文档列出的可选项只有分支名和目标仓库。**没有写**可以改来源分支。
  - 【待验证】界面上可能有"更改分支来源"的选项，但这份文档没写，我也没在界面里实际看过。
  在线页面：https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-a-branch-for-an-issue
- 【一手】GraphQL 接口能做到更多：
  - `createLinkedBranch` 的输入里，`oid` 是必填，意思是"新分支基于哪个提交"。还有可选的 `name`、`repositoryId`。
  - Issue 对象上有 `linkedBranches` 字段，可以读出"这个 issue 关联了哪些分支"。
  出处：GitHub 公开的 GraphQL schema，https://docs.github.com/public/fpt/schema.docs.graphql 中的 `input CreateLinkedBranchInput` 和 `Issue.linkedBranches`。
- 【一手】issue 表单模板里常见"版本"下拉框。官方示例 `data/reusables/community/issue-forms-sample.md` 第 32–42 行有一个 `id: version` 的下拉框，问的是"你在用哪个版本"，选项是 `1.0.2 (Default)` 和 `1.0.3 (Edge)`。注意它问的是**版本**，不是**分支**；官方示例里没有"分支"这个字段。
  在线页面：https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema
- 【一手】Actions 事件文档 `content/actions/reference/workflows-and-actions/events-that-trigger-workflows.md` 的 `issues` 一节（约第 320–328 行），表格里：
  - `GITHUB_SHA` = "Last commit on default branch"（默认分支的最新提交）
  - `GITHUB_REF` = "Default branch"（默认分支）
  - 活动类型里包括 `labeled`。
  - 注释还说：只有默认分支上存在这个工作流文件时，事件才会触发它。
  在线页面：https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#issues
- 【一手】`actions/checkout` 的 `action.yml`：
  - `fetch-depth` 默认是 1（第 74–76 行），填 0 表示拉取全部历史。
  - `ref` 不填时，用触发事件对应的引用（第 7–11 行）。
  - 【推断】放到 `issues` 事件下，就是默认分支、只有一个提交。

---

## 对 RepoAegis 的启示

以下都是【推断】，供你拍板，我不替你做决定。

**关于 git 历史**

1. **不需要做专门的"查历史"工具**。调研的七家都没有，能查的都是在命令行里跑 `git log` / `git blame`。RepoAegis 的 agent 只要有命令行，就已经具备这个能力。真正要决定的是下面两件事。
2. **第一件：克隆时带不带历史。** 这是能不能查的前提。
   - GitHub Actions 里 `actions/checkout` 默认只拉一个提交；SWE-agent 修 GitHub 仓库、OpenHands 新版 issue-to-pr、claude-code-action 示例也都是浅克隆。这种情况下，提示词写了"去查历史"也查不到。
   - 只有旧版 OpenHands resolver 是完整克隆。
   - 可选做法：
     - 甲，浅克隆，不提供历史。快、省，和多数官方做法一致。
     - 乙，完整克隆（`fetch-depth: 0`）。能查，但大仓库克隆会慢。
     - 丙，`git clone --filter=blob:none`（只先下载提交记录，文件内容用到时再下载）。理论上兼顾两者，但在 Actions 上实际快多少、`git blame` 会不会反复去下载文件，【待验证】，需要在测试仓库实测。
3. **第二件：提示词里要不要说什么时候查。** 唯一写进系统提示词的是 Codex，只有一句"需要更多背景时用 `git log` 和 `git blame`"。如果要写，可以参考 Codex 这一句，再补两个具体场景：
   - issue 说"以前好的、现在坏了"（回归问题）时，看相关文件最近的改动；
   - 看不懂某段代码为什么这么写时，用 `git blame` 找当初的提交说明。
   这两个场景是我从研究和 Claude Code 文档的例子里归纳的，官方提示词里没有这么写。
4. **做评测时的作弊风险是真实的，但只出现在"拿历史 issue 当题目"的时候。**
   - 实际运行时处理的是还开着的 issue，修复还不存在，不存在"偷看未来"。
   - 以后如果拿已修好的历史 issue 做评测，就必须照 SWE-bench 的 `git_clone_timesafe()` 处理：退回 base commit，删 origin，删未来的 tag，清 reflog，做 gc，最后检查确实没有未来提交。注意它特意**保留了过去的历史和 tag**，因为有的题（如 pytest-5840）本来就要靠过去的历史才能解。
   - 另外，SWE-bench 还有通过 Conda 缓存泄露的未关闭 issue（#640）。光清 git 不一定够。
5. **研究证据偏弱、偏间接。**
   - HAFix 的提升建立在"已经知道错在哪一行"的前提上，模型也是旧的小模型。
   - RepoMem 在 SWE-bench Verified 上解决率提高约 3.4 个百分点，但用的是框架预先整理好的提交检索工具，而且历史少的仓库反而变差。
   - 没有找到"让 agent 自己跑 git log 能提高多少"的原文数据。如果要证明这个工具有用，只能等产品做完、你同意开始评测时自己测。

**关于分支**

6. **所有官方实现的默认值都一样：用默认分支。** Copilot、claude-code-action、OpenHands 两个版本、SWE-agent、GitHub 自己的"从 issue 建分支"都是如此。`issues` 事件里的 `GITHUB_REF` 也是默认分支，而且工作流文件必须放在默认分支上才会触发。
7. **能改基础分支的方式，分三档：**
   - 按单个 issue 选：只有 Copilot 在指派对话框和 API 的 `base_branch` 参数里支持。
   - 整个工作流固定一个：claude-code-action 的 `base_branch` 输入项、OpenHands worker 的 `base_branch` 配置、旧 resolver 的 `target_branch`。
   - 完全不能改：OpenHands 手动版、SWE-agent 的 PR 目标分支。
8. **一个要避开的坑：改代码用的分支，和 PR 指向的分支，必须是同一个。** SWE-agent 和旧版 OpenHands resolver 都可能对不上（前者 `base_commit` 可改、PR 目标却固定是默认分支；后者 `target_branch` 只用在开 PR 那一步）。claude-code-action、OpenHands 新版、Copilot 都是同一个变量贯穿始终。
9. **如果 RepoAegis 要支持按 issue 指定分支，信息从哪来，有三种候选**，各有依据：
   - 甲，只用默认分支，和所有官方默认一致，最简单；
   - 乙，在工作流配置里固定一个基础分支，像 claude-code-action 那样；
   - 丙，按 issue 读取：可以读 issue 表单里专门加的"分支"字段（官方示例只有"版本"字段，分支字段要自己加），或者读 issue 已关联的分支（GraphQL `Issue.linkedBranches`）。读到的分支名要先确认在仓库里存在，旧 resolver 就是这么检查的。
   不管选哪种，都要把实际用的分支名和提交号写进 issue 里的汇报，方便人核对。OpenHands 手动版就会在 issue 评论里写"从哪个分支、哪个提交切出来"（`main.py` 约第 958 行）。
