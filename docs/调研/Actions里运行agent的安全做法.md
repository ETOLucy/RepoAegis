# 在 GitHub Actions 里跑"能执行命令的 agent"：官方项目的安全做法与对 RepoAegis 的建议

> 本报告 2026-10-24 写在调研会话的笔记目录（原文件名 `research-security.md`），2026-10-25 原样搬进 `docs/调研/`，正文没有再复核，行号和版本以当时为准。据此的决定见 [安全设计](../安全设计.md)：四个作业的结构、必做项、DeepSeek 密钥走 AWF。

调研日期：2026-10-24。所有源码都是当天浅克隆到 WSL `~/scratch/sec/` 后逐个文件读的，行号对应下面列出的提交，以后文件变动行号可能会偏。

## 标记说明

- 【一手】：本次打开过原文（源码或官方文档），写了位置。
- 【推断】：根据一手材料推出来的，原文没有直接这么说。
- 【待验证】：没找到一手来源，或者要实际跑一遍才能确认。
- 【找不到】：找过了，原文里没有。

## 读过的材料和版本

| 项目 | 仓库 | 提交 | 主要文件 |
|---|---|---|---|
| GitHub Agentic Workflows（简称 gh-aw） | github/gh-aw | `86f50bf9` (2026-10-24) | `docs/src/content/docs/introduction/architecture.mdx`、`reference/{safe-outputs-pull-requests,sandbox,network,threat-detection,permissions}.md`、`pkg/workflow/awf_*.go` |
| gh-aw 的网络防火墙（简称 AWF） | github/gh-aw-firewall | `de3c6b98` (2026-10-22) | `README.md`、`action.yml`、`docs/api-proxy-sidecar.md`、`docs/chroot-mode.md` |
| Copilot cloud agent 文档 | github/docs | `02c25d16` (2026-10-23) | `content/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations.md` 等 |
| Actions 安全文档 | github/docs | 同上 | `content/actions/reference/security/secure-use.md`、`content/actions/concepts/security/{script-injections,github_token}.md`、`data/reusables/actions/*` |
| codex-action | openai/codex-action | `c05cfd74` (2026-10-20) | `README.md`、`docs/security.md`、`action.yml` |
| Codex 的密钥代理 | openai/codex | `970111f6` | `codex-rs/responses-api-proxy/README.md` |
| claude-code-action | anthropics/claude-code-action | `925afb53` (2026-10-24) | `docs/security.md`、`docs/faq.md`、`src/create-prompt/index.ts`、`src/github/utils/sanitizer.ts`、`src/github/operations/git-config.ts`、`scripts/git-push.sh` |

下文链接里的文件路径都相对这些仓库根目录。完整链接格式例如 `https://github.com/github/gh-aw/blob/86f50bf9/docs/src/content/docs/introduction/architecture.mdx#L132`。

## 先讲清几个词

- **提示注入（prompt injection）**：攻击者把指令藏在 issue、网页、代码注释等地方，模型读到后当成命令照做。
- **运行机（runner）**：GitHub Actions 分配的一台临时虚拟机，免费的是 `ubuntu-latest`。
- **`GITHUB_TOKEN`**：每个 Actions 作业（job）开始时 GitHub 自动发的临时令牌，作业结束就失效，权限由工作流里的 `permissions:` 决定。
- **作业（job）**：一个工作流里可以有多个作业，每个作业默认跑在一台新运行机上，彼此不共享内存和文件，只能通过"工件（artifact）"传文件。
- **出口白名单（egress allowlist）**：只允许程序访问名单里的域名，其他网络请求一律拦下。
- **密钥代理（credential proxy）**：一个单独的进程或容器拿着 API key，agent 只把请求发给代理，由代理加上 key 再转发，agent 自己永远看不到 key。
- **沙箱（sandbox）**：用操作系统机制（容器、命名空间、bubblewrap 等）把一个进程关起来，限制它能读写的文件、能访问的网络。

---

## 第一部分：按"一层一层"整理各家做法

### 第 1 层：谁能触发

**gh-aw**
- 工作流开跑前先有一个"预激活作业（pre-activation job）"检查触发者的角色，默认只允许 `admin`、`maintainer`、`write` 三种角色，而且是精确匹配。【一手】`docs/src/content/docs/reference/compilation-process.md` 约 L269；`reference/glossary.md` 约 L1174-1176。
- 对公开仓库，默认把 issue、评论等内容按作者身份过滤（`min-integrity: approved`），只让仓库所有者、成员、协作者写的内容进到 agent 眼前。【一手】`introduction/architecture.mdx` "Integrity Filtering" 一节 L581-587。

**Copilot cloud agent**
- 只有对仓库有写权限的人能让它开工；没有写权限的人发的评论"从不"交给 agent。【一手】`content/copilot/concepts/security-governance-and-network-settings/risks-and-mitigations.md` L36。
- 自动化场景下，默认忽略没有写权限的用户触发的事件。【一手】同文件 L71。

**codex-action**
- 默认只允许对仓库有写权限的用户运行；可以用 `allow-users`、`allow-bots` 放宽。【一手】`docs/security.md` L5-7；`README.md` L120-121。
- 它还专门提醒：即便设计成"有写权限的人给外人开的 issue 打标签才触发"，issue 内容本身仍是不可信输入。【一手】`docs/security.md` L9。这正是 RepoAegis 的情况。

**claude-code-action**
- 对 issue、PR、评论等事件检查触发者是否有写权限；源码里调 `getCollaboratorPermissionLevel`，只认 `admin` 或 `write`。【一手】`docs/security.md` L5；`src/github/validation/permissions.ts` 约 L124-133。
- 可以用 `allowed_non_write_users` 绕过检查，官方标为"RISKY"。【一手】`docs/security.md` L11-18。Clinejection 事故就是开了 `allowed_non_write_users: "*"`（见第二部分）。

**GitHub 平台本身**
- 能给 issue 打标签的角色是 Triage、Write、Maintain、Admin，Read 不行。【一手】docs.github.com "Repository roles for an organization" 权限表 "Apply/dismiss labels" 一行（网页读取）。
- 也就是说在组织仓库里，**只有 triage 权限的人也能打标签**。"打标签 = 有写权限"这个前提并不成立。【推断】所以 RepoAegis 在触发后还要再查一次打标签人的权限。个人账号下的仓库没有 triage 角色，协作者就是写权限。【待验证】

**分歧**：大家都把"写权限"当默认门槛，没有分歧。差别只在于能不能放宽：claude-code-action 和 codex-action 允许配置放宽，gh-aw 的 strict 模式会拒绝某些放宽组合（`reference/triggers.md` L189）。

### 第 2 层：输入怎么处理

这一层包括两件事：一是工作流脚本本身不能被 issue 标题"注入命令"，二是交给模型的文字要先清洗。

**A. 不能把 `${{ github.event.issue.title }}` 直接写进 `run:`**
- GitHub 官方解释：`run:` 里的 `${{ }}` 会在 shell 脚本生成之前被原样替换，所以标题里带 `"; 命令` 就能跑任意命令。【一手】`content/actions/concepts/security/script-injections.md` L27-52，文中用的就是 PR 标题的例子。
- 以 `body`、`title`、`head_ref`、`label`、`message`、`name` 等结尾的上下文都应视为不可信。【一手】同文件 L17。
- 正确写法有两种：交给一个 action 当参数，或者先放进 `env:` 再在脚本里用 `"$TITLE"`。【一手】`content/actions/reference/security/secure-use.md` "Use an intermediate environment variable" 约 L57-93。
- codex-action 的安全文档给了同样的建议。【一手】`docs/security.md` L48-60。
- gh-aw 在"编译"阶段（把 `.md` 工作流转成 `.lock.yml` 时）只允许白名单里的表达式，并用 actionlint、zizmor、poutine 扫描。【一手】`architecture.mdx` "Compilation-Time Security" L427-490。

**B. 交给模型前清洗文字**
- **claude-code-action**：`sanitizeContent()` 依次去掉 HTML 注释、不可见字符、图片 alt 文本、链接标题、隐藏属性，规范化 HTML 实体，最后抹掉 GitHub 令牌。【一手】`src/github/utils/sanitizer.ts` L68-77；调用点在 `src/github/data/formatter.ts` L22-99（标题、正文、评论都会过一遍）。文档也承认"可能出现新的绕过手法"。【一手】`docs/security.md` L78。
- **gh-aw**：在"激活（activation）"阶段清洗，内容包括：把 `@用户` 加反引号使其不触发通知，把 `fixes #123` 加反引号，把 `<tag>` 改成 `(tag)`，不在白名单里的 URL 换成 `(redacted)`，Unicode 规范化，限制 0.5MB / 6.5 万行，去掉控制字符。【一手】`architecture.mdx` "Content Sanitization" L492-579。
- **Copilot**：交给 agent 前过滤隐藏字符，例如 HTML 注释里的文字不会传进去。【一手】`risks-and-mitigations.md` L54。
- **codex-action**：没有内置清洗，只在文档里列出 PR 正文的 HTML 注释、提交信息、`AGENTS.md`、截图等都是注入来源。【一手】`docs/security.md` L11-18。

**分歧**：Copilot、claude-code-action、gh-aw 都做清洗；codex-action 不做，交给使用者。gh-aw 多做了一步"URL 白名单"，claude-code-action 没有。所有人都承认**清洗只能减少注入，挡不住**，真正兜底的是后面几层。【推断】

### 第 3 层：agent 运行时能做什么

这一层分开看文件系统、网络、密钥、GitHub 令牌、能执行的命令。

#### 3.1 文件系统和权限提升

- **codex-action** 默认 `safety-strategy: drop-sudo`：在启动 Codex 之前撤掉 `runner` 用户的 sudo，撤掉对 Docker 等 root 套接字的访问，并以 `no_new_privs`、无额外组、无任何 capability 的方式启动 Codex。这个改动不可逆，所以官方建议放在作业最后一步。【一手】`README.md` L172；`action.yml` L317-344。
  - 其他选项：`unprivileged-user`（用你自建的低权限用户跑）、`read-only`（只读沙箱）、`unsafe`（不降权，只在完全信任提示词时用）。【一手】`README.md` L173-175。
  - Codex 自己还有沙箱模式：`read-only`、`workspace-write`（默认，只能写工作目录）、`danger-full-access`。Linux 上用 bubblewrap 加 seccomp 实现，`workspace-write` 默认关网，工作目录里的 `.git` 是只读的。【一手】learn.chatgpt.com/codex/agent-approvals-security "Network access""OS-level sandbox""Protected paths in writable roots" 三节（网页读取）。
- **gh-aw / AWF**：agent 跑在 Docker 容器里，Docker 套接字被藏起来，agent 不能自己起容器；`$HOME`、工作目录、`/tmp` 可写，`/usr` 等系统目录只读。【一手】gh-aw `reference/sandbox.md` L125-140。进入用户命令前会丢掉 `CAP_NET_ADMIN`、`CAP_NET_RAW`、`CAP_SYS_ADMIN` 等能力，进程以普通用户身份运行，有独立的 PID 命名空间。【一手】gh-aw-firewall `docs/chroot-mode.md` L214-258。官方也写明 `$HOME` 可读写是已知风险。【一手】同文件 L266-289。
- **claude-code-action**：默认只给 `Glob`、`Grep`、`LS`、`Read` 加几条固定 git 命令，文件编辑通过 `acceptEdits` 模式只允许写在工作目录里。【一手】`src/create-prompt/index.ts` L31-61。只有开了 `allowed_non_write_users` 时，才会装 bubblewrap，给子进程做 PID 隔离和环境变量清理（"尽力而为"）。【一手】`action.yml` L225-247；`docs/security.md` L16。
- **Copilot**：跑在 GitHub 托管的一次性虚拟机上，官方推荐用托管运行机，保证每次都是新机器。【一手】`content/copilot/tutorials/cloud-agent/build-guardrails.md` L57-59。没有公开它对文件系统的具体限制。【找不到】

#### 3.2 网络

- **gh-aw / AWF**：agent 放进只能经过 Squid 代理（一个按域名放行的 HTTP 代理）出网的 Docker 网络，用 iptables（Linux 防火墙规则）把流量强制导到 Squid；只放行白名单里的域名。【一手】`architecture.mdx` "Agent Workflow Firewall (AWF)" L176-199；`reference/network.md` "Implementation" L218-239。默认开启，关掉要显式写 `dangerously-disable-sandbox-agent`，strict 模式下一律拒绝。【一手】`reference/sandbox.md` L34-53。
  - **AWF 可以单独拿来用**：它有自己的 action（`github/gh-aw-firewall@v1`），用法是 `sudo awf --allow-domains 域名 -- 你的命令`；需要 Docker 20.10+、Ubuntu 22.04+、x86_64。【一手】gh-aw-firewall `README.md` L13-31、L76-85；`action.yml` L29-42（只支持 Linux x64）。
- **Copilot**：默认有防火墙，加一份"推荐白名单"（各语言的包仓库、系统包源、证书机构等）。【一手】`content/copilot/how-tos/copilot-on-github/customize-copilot/customize-the-firewall.md` L26-59。但官方列出局限：只管 agent 通过 Bash 工具启动的进程，不管 MCP 服务器和准备步骤；"复杂攻击可能绕过"。【一手】同文件 L39-47。这个防火墙是 Copilot 自带的，别人用不了。【推断】
- **codex-action**：网络由 Codex 沙箱控制，`workspace-write` 默认关网（见 3.1）。没有域名级白名单。【推断】
- **claude-code-action**：默认禁用 `WebSearch`、`WebFetch` 两个工具。【一手】`src/create-prompt/index.ts` L79-84。没有网络层的防火墙，一旦允许 Bash，`curl` 就能出网。【推断】
- **GitHub 托管运行机本身**：只有一个 `/etc/hosts` 文件屏蔽了一些挖矿和恶意网站，没有通用的出口白名单。【一手】`data/reusables/actions/runners-etc-hosts-file.md` L1；`secure-use.md` "Denying access to hosts" 约 L234。

#### 3.3 模型 API 密钥

关键问题："agent 能执行命令，就能读环境变量，那它是不是一定能拿到 key？"各家答案不同。

- **codex-action**：用 `codex-responses-api-proxy` 当密钥代理。key 通过标准输入传进去，启动时用 `env -u` 把环境变量里的副本去掉，然后 shell 用 `exec` 把自己替换掉，不留副本。【一手】`action.yml` L215-242。代理本身把 key 放进一块锁定的内存，清零临时缓冲区，只接受 `POST /v1/responses`，其余请求一律 403。【一手】openai/codex `codex-rs/responses-api-proxy/README.md` L31-39、L145-153、L184-197。
  - 官方特别警告：GitHub 托管运行机默认有免密码 sudo，只要有 sudo，"只读 + 断网"也能通过 `/proc` 把 key 读出来，所以必须配合 `drop-sudo` 或 `unprivileged-user`。【一手】codex-action `docs/security.md` L68-72。
  - `read-only` 模式下 key 仍然经过代理，Codex 如果能读到进程内存就能拿到 key。【一手】`README.md` L174。
- **gh-aw / AWF**：key 放在单独的 "api-proxy" 容器里，agent 容器里只有占位值，agent 通过 `OPENAI_BASE_URL=http://172.30.0.30:10000` 这类地址把请求发给它。【一手】gh-aw-firewall `README.md` L17；`docs/api-proxy-sidecar.md` L207-270、"Credential isolation" L275-284。gh-aw 编译器会把所有引用 `secrets.*` 的环境变量用 `--exclude-env` 从 agent 容器里排除。【一手】gh-aw `pkg/workflow/awf_env.go` L87-105、`awf_helpers.go` L93-99、`awf_command_builder.go` L578-594。
  - **AWF 支持 OpenAI 兼容的自定义上游**：`--openai-api-target <主机>` 或 `--openai-base-url-env`，后者会顺手把这个主机加进白名单，并对 agent 隐藏这个变量。【一手】`docs/api-proxy-sidecar.md` L408-419。代理代码能识别 `/chat/completions` 路径。【一手】`containers/api-proxy/body-handler.js` L47-56、`proxy-utils.js` L158-161。所以 DeepSeek 理论上可以接；实际能不能跑通【待验证】。
  - 官方提醒：api-proxy 自己不受白名单限制，它是可信组件。【一手】`docs/api-proxy-sidecar.md` L295-297。
- **Copilot**：你配的"Agents 密钥"会**作为环境变量直接暴露给 agent**，只是在会话日志里打码。普通 Actions 密钥 agent 拿不到。【一手】`content/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/configure-secrets-and-variables.md` L25、L57-64；`build-guardrails.md` L51-55。Copilot 自己的模型凭据怎么保管，文档没说。【找不到】
- **claude-code-action**：Anthropic key 就在 Claude 进程的环境里。只有开了 `allowed_non_write_users` 时，才对子进程做"尽力而为"的密钥清理。【一手】`docs/security.md` L16。

**分歧（重要）**：codex-action 和 gh-aw 都认为"agent 不该能读到 key"，用密钥代理解决；Copilot 和 claude-code-action 默认让 key 和 agent 在同一个环境里，靠"只有写权限的人能触发"兜底。codex-action 和 gh-aw 的区别在于隔离方式：前者靠撤 sudo + 降权让同一台机器上的 agent 读不到代理的内存，后者靠把代理放进另一个容器。

#### 3.4 GitHub 令牌

- **gh-aw**：agent 作业只有只读权限，写操作全部交给后面的作业（见第 4、5 层）。【一手】`architecture.mdx` "Safe Outputs: Permission Isolation" L132-174；`reference/permissions.md` L8-19。
- **Copilot**：agent 的凭据只能做"简单的推送"，不能直接执行 `git push` 或其他 git 命令。【一手】`risks-and-mitigations.md` L38。
- **claude-code-action**：agent 自己推分支。默认把令牌写进 `origin` 的远程地址（`https://x-access-token:令牌@github.com/...`）；只有开了 `allowed_non_write_users` 才改用凭据助手（credential helper）。【一手】`src/github/operations/git-config.ts` L108-135。`git push` 被包了一层脚本，只允许 `origin <分支>`、不允许带参数，用来防 `--receive-pack` 这类远程执行。【一手】`scripts/git-push.sh` L4-36。
  - 【推断】默认配置下令牌写在 `.git/config` 里，agent 默认就有 `Read` 工具，理论上能读到。它靠"令牌是作业级、作业结束就失效、触发者都有写权限"来兜底。
- **codex-action**：action 本身不给 Codex GitHub 令牌；官方示例把 Codex 的输出交给下一个作业处理。【一手】`docs/security.md` L76-84。
- **actions/checkout**：`persist-credentials` 默认是 `true`，会把令牌留在本地 git 配置里；v6 起改存到 `$RUNNER_TEMP` 下的单独文件。【一手】actions/checkout `README.md`（网页读取，Usage 一节和 v6 说明）。【推断】agent 作业里检出代码时必须设 `persist-credentials: false`，否则 agent 能从磁盘读到令牌。

#### 3.5 能执行的命令

- **claude-code-action**：默认不能跑任意 Bash，需要用 `allowed_tools` / `--allowedTools` 显式放开。【一手】`docs/capabilities-and-limitations.md` L22；`src/create-prompt/index.ts` L34-61。
- **gh-aw**：MCP 服务器（给 agent 提供工具的独立进程）跑在单独的容器里，工具按 `allowed:` 白名单开放。【一手】`architecture.mdx` "MCP Server Sandboxing" L295-346。
- **codex-action**：用 `permission-profile` 限制 Codex 能跑的命令能碰哪些文件、能不能上网；会拒绝那些削弱保护的 `codex-args`。【一手】`docs/security.md` L20-46。
- **Copilot**：自动化场景下由你选择它能用哪些工具。【一手】`risks-and-mitigations.md` L70。

**分歧**：claude-code-action 靠"工具白名单"；codex-action 和 gh-aw 靠"系统级沙箱"（就算命令放开了，沙箱也兜得住）。对 RepoAegis 来说，agent 本来就要跑测试、跑任意命令，工具白名单作用有限，必须靠系统级隔离。【推断】

### 第 4 层：agent 的产出怎么检查后才发出去

**gh-aw（最完整）**
- **写操作先缓存再执行**：agent 想开 PR、留言，只是往 `agent_output.json` 和 `aw.patch` 里写记录，不直接调 GitHub API；agent 结束后再由独立作业处理。【一手】`architecture.mdx` L52-58、L132-168。
- **威胁检测作业（threat detection）**：另一个作业下载这些工件，用同一个模型加安全专用提示词判断有没有泄露密钥、恶意补丁、越权操作；判"不安全"就整条工作流失败，什么都不发。这个作业没有写权限，看不到 agent 运行时的状态。也可以加 TruffleHog、Semgrep 等扫描步骤。【一手】`architecture.mdx` "Threat Detection Pipeline" L348-425；`reference/threat-detection.md` L9-40。
- **受保护文件（protected files）**：PR 补丁默认不允许动 `.github/`、`.agents/`、`.githooks/`、`.husky/`、任何以 `.` 开头的顶层目录，依赖清单（`package.json`、`pyproject.toml`、`requirements.txt`、各种锁文件等），agent 指令文件（`AGENTS.md`、`CLAUDE.md`），`CODEOWNERS`，以及 `README.md`、`SECURITY.md` 等。默认策略是照样开 PR 但自动提交一个 "REQUEST_CHANGES" 审查；也可以设成 `blocked`（直接失败）或 `fallback-to-issue`（改成开 issue）。【一手】`reference/safe-outputs-pull-requests.md` "Protected Files" L558-583、L728-772。
- **补丁大小限制**：默认最多 100 个文件、4096KB。`allowed-files` 可以限定只能改哪些路径。【一手】同文件 L44-48、L134-136、L669-705。
- **PR 默认是草稿，且这是"策略"不是"默认值"**，agent 运行时改不了。【一手】同文件 L24、L140。
- **输出文字清洗**：XML 转义、只允许 HTTPS、域名白名单（默认只有 GitHub）、大小限制、去控制字符、删掉 HTML 注释。【一手】`reference/safe-outputs.md` "Text Sanitization" L1982-1986。
- **密钥打码**：上传工件前，扫描 `/tmp/gh-aw` 下的所有文件，把工作流里用到的每个密钥的值精确替换掉；用 `if: always()` 保证失败时也执行。【一手】`architecture.mdx` "Secret Redaction" L589-650。

**Copilot**
- 用 CodeQL 查安全问题，新引入的依赖对照 GitHub 公告库查恶意包和高危漏洞，用密钥扫描查有没有提交 API key。【一手】`risks-and-mitigations.md` L21-28。
- **Copilot 可以改 `.github/workflows`**：官方教程就是让 Copilot cloud agent 自己写 `.github/workflows/copilot-setup-steps.yml` 再开 PR。【一手】`content/copilot/tutorials/cloud-agent/improve-a-project.md` L76-95。它靠第 6 层（人工审查、批准后才跑工作流）兜底，并建议用 `CODEOWNERS` 保护敏感配置文件。【一手】`build-guardrails.md` L45。

**claude-code-action**
- 不能改工作流文件，因为它的 GitHub App 没有 workflows 写权限。【一手】`docs/faq.md` L60-62。
- 但 `docs/security.md` 又写着这个 App "申请了" Workflows 读写权限，只是暂未使用。【一手】`docs/security.md` L92-99。**两份文档互相矛盾**，实际权限【待验证】。
- 输出方面：对外显示的内容会用 `redactSecrets()` 抹掉 GitHub、Anthropic、AWS、Slack 令牌和 JWT 格式的字符串。【一手】`src/github/utils/sanitizer.ts` L79-119。

**codex-action**
- 没有内置的 diff 检查。只有一个 `final-message` 输出，建议交给下一个作业处理。【一手】`README.md` L183-197；`docs/security.md` L76-84。

**分歧**：要不要拦工作流文件的修改，三家做法不同：gh-aw 默认拦（并标出需要人审），claude-code-action 靠 App 没权限从根上拦，Copilot 允许改但靠人审和"批准后才跑工作流"。只有 gh-aw 有"AI 复查一遍产出"这一步。

### 第 5 层：发布时用什么身份和权限

- **`GITHUB_TOKEN` 改不了工作流文件**：它可设置的权限里根本没有 `workflows` 这一项。【一手】`data/reusables/actions/github-token-available-permissions.md` L1-25。改 `.github/workflows` 需要 GitHub App 的 "Workflows" 权限。【一手】`content/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app.md` L92。gh-aw 文档直接写明这是只有 GitHub App 才有的权限。【一手】gh-aw `reference/safe-outputs-pull-requests.md` L707-726。
  - 【推断】对 RepoAegis 来说这是天然的第二道防线：就算检查漏了，用 `GITHUB_TOKEN` 推送改了 `.github/workflows` 的分支也会被 GitHub 拒绝。具体报错和是否整个推送失败【待验证】。
- **用 `GITHUB_TOKEN` 开的 PR 不会自动跑别的工作流**：`GITHUB_TOKEN` 产生的事件一般不触发新的工作流运行。新规则是：用它创建或更新 PR 时，`pull_request` 工作流会进入"需批准"状态，有写权限的人点"Approve workflows to run"才跑。【一手】`data/reusables/actions/actions-do-not-trigger-workflows.md` L1-6。这个功能的开关文件写着"正在 github.com 上逐步上线"。【一手】`data/features/actions-github-token-pull-request-approval.yml` L1-7。你的仓库是否已生效【待验证】。
- **个人仓库默认不允许工作流开 PR**，`GITHUB_TOKEN` 默认只有 `contents`、`packages` 的读权限。【一手】docs.github.com "Managing GitHub Actions settings for a repository"（网页读取）。所以 RepoAegis 要用 `GITHUB_TOKEN` 开 PR，就得在仓库设置里打开"允许 GitHub Actions 创建和批准拉取请求"。
- **最小权限**：官方建议默认只给 `contents: read`，再按作业单独加；写了任何一项权限，没写的就全是 `none`。【一手】`secure-use.md` "Use secrets for sensitive information" 约 L32-34；`github-token-available-permissions.md` L25。
- **gh-aw**：每个写作业只给自己需要的权限，例如开 PR 的作业给 `contents: write` + `pull-requests: write`，留言的作业给 `issues: write`。【一手】`architecture.mdx` L148-153。
- **Copilot**：只能推到自己的 `copilot/` 分支（或被 @ 的那个 PR 的分支），受分支保护约束；提交带签名，作者是 Copilot，发起人是共同作者，提交信息里附会话日志链接。【一手】`risks-and-mitigations.md` L37、L60-63。
- **claude-code-action**：从 issue 触发时总是新建分支；系统提示要求它不推别的分支、不强推（这是提示词约束，不是权限约束）。【一手】`docs/capabilities-and-limitations.md` L10-13；`docs/faq.md` L64-71。

**分歧**：Copilot 和 gh-aw 是平台或外层程序替 agent 发布；claude-code-action 是 agent 自己 `git push`，但 PR 要人手动点链接创建（`docs/security.md` L66-74）。

### 第 6 层：人工审查

- **Copilot**：开的是草稿 PR，自己不能标为"可审查"，不能批准或合并；默认要有写权限的人点"Approve and run workflows"才跑 CI；发起任务的人不能批准这个 PR；用 App 身份开的 PR 还要多一个批准。【一手】`risks-and-mitigations.md` L39-42。可以关掉"跑工作流要批准"，但官方警告这可能让没审过的代码拿到仓库写权限或读到 Actions 密钥。【一手】`content/copilot/how-tos/use-copilot-agents/cloud-agent/configuring-agent-settings.md` L28-39。
- **gh-aw**：PR 草稿是强制策略；改了受保护文件会自动要求修改；用它推的分支默认不触发 CI。【一手】`reference/safe-outputs-pull-requests.md` L140、L576。
- **claude-code-action**：默认不自动开 PR，给一个预填好的 PR 链接让人去点。【一手】`docs/security.md` L66-74。
- **GitHub 官方**：建议用 `CODEOWNERS` 监控对工作流文件的修改，并考虑禁止工作流创建或批准 PR。【一手】`secure-use.md` "Using CODEOWNERS to monitor changes" 约 L163、"Preventing GitHub Actions from creating or approving pull requests" 约 L201。

**分歧**：没有实质分歧，都认为"合并前必须有人审"。

### 横向补充：工作流本身的供应链和运行机

- **第三方 action 固定到完整提交号**：同一个作业里的任何 action 被攻破，都能拿到仓库的全部密钥并用 `GITHUB_TOKEN` 写仓库。官方首选工作流依赖锁文件（lockfile），用不了就固定到完整提交号；标签可以被挪动。【一手】`secure-use.md` "Using third-party actions" 约 L129-153。锁文件功能已在 github.com 开放。【一手】`data/features/actions-workflow-lockfiles.yml`。gh-aw 编译时也会自动把 action 固定到提交号。【一手】`architecture.mdx` L443-446。
- **同一个工作流里的作业会互相影响**：例如读后续作业的环境变量、往共享目录写文件。【一手】`secure-use.md` 约 L131。codex-action 进一步说明：agent 跑完后，机器状态不可信（可能留后台进程、改了别的 action 的源码、改了 `.git/hooks`），所以要把 Codex 放在作业最后一步，后续工作放进新作业。【一手】codex-action `docs/security.md` L76-84。
- **`issues` 事件的运行环境**：工作流用默认分支上的最后一次提交，`GITHUB_REF` 是默认分支。【一手】`content/actions/reference/workflows-and-actions/events-that-trigger-workflows.md` "issues" 一节 L320-325。只有 fork 触发的工作流拿不到密钥。【一手】`data/reusables/actions/forked-secrets.md` L1。【推断】`issues` 事件能拿到仓库密钥，Clinejection 的作者也证实那个工作流里暴露了 Anthropic key（见下）。

---

## 第二部分：公开事故，以及哪一道防线能挡住

### Clinejection（2026 年 2 月，Cline 仓库）

经过【一手：Adnan Khan 原文 adnanthekhan.com/posts/clinejection/；二手：Simon Willison 2026-03-06 链接博客、Snyk 分析】：
- Cline 用 claude-code-action 做 issue 分类，设了 `allowed_non_write_users: "*"`（任何人开 issue 都触发），放开了 Bash、Write、Edit、WebFetch 等工具，提示词里直接带 issue 标题。`GITHUB_TOKEN` 权限很小，暴露的密钥只有 Anthropic key。
- 攻击者用标题注入让 Claude 执行命令，然后**污染 Actions 缓存**：先用垃圾数据把缓存撑爆，逼 GitHub 按最近最少使用规则清掉旧条目，再按夜间发布工作流的缓存键写入恶意内容。夜间发布工作流恢复缓存后执行了恶意代码，拿到了 npm、VS Code 插件市场的发布令牌。之后真的有人发布了被篡改的 `cline@2.3.0`。

哪一道防线能挡住：
1. **第 1 层**：只让有写权限的人触发。设了 `"*"` 等于没有这一层。
2. **第 3 层**：如果 agent 跑在没有 sudo、出网受限的沙箱里，执行命令后也很难去写缓存、往外发数据。【推断】
3. **横向**：手握发布令牌的工作流不要用缓存（作者原文建议）。RepoAegis 的 agent 作业**不要读写缓存**，也不要和仓库里其他工作流共用缓存键。【推断】
4. 清洗输入挡不住，因为注入在标题的正常文字里，不是隐藏字符。【推断】

### PromptPwnd（Aikido Security，2025-12-04）

经过【一手：aikido.dev/blog/promptpwnd-github-actions-ai-agents】：
- 一类问题：工作流把 `${{ github.event.issue.title }}`、PR 正文、提交信息塞进提示词，agent 又有 shell 和 `gh issue edit` 等写 issue 的工具，环境里还有带写权限的 `GITHUB_TOKEN` 和模型 key。
- gemini-cli 的例子：issue 里藏一段伪装成配置文件的指令，让 agent 执行 `gh issue edit --body "$GEMINI_API_KEY $GITHUB_TOKEN"`，密钥就被贴到公开的 issue 里。文章特别指出，通过环境变量传标题能防 shell 注入，**防不了提示词注入**。

哪一道防线能挡住：
1. **第 3 层**：agent 没有 GitHub 令牌、读不到模型 key（密钥代理），就没东西可泄露。
2. **第 4 层**：agent 不能直接写 issue，它想说的话先经过检查和密钥打码，再由外层程序发出去。
3. **第 5 层**：发布作业里的令牌只有必要权限。

### GitHub MCP 数据泄露（Invariant Labs，2025-05-26）

经过【一手：invariantlabs.ai/blog/mcp-github-vulnerability】：
- 用户让 Claude Desktop 通过官方 GitHub MCP 服务器看某个公开仓库的 issue，并设了"总是允许"工具调用。恶意 issue 让 agent 去读用户的私有仓库，把内容写进公开仓库的 PR。
- 作者强调这是架构问题，不是 MCP 服务器的代码 bug。

哪一道防线能挡住：
1. **第 3 层**：令牌只覆盖一个仓库（`GITHUB_TOKEN` 天然只限当前仓库，见 `content/actions/concepts/security/github_token.md` L17）。
2. **第 4 层**：发出去之前检查产出（例如 gh-aw 的威胁检测会看"越权操作"）。
3. **第 6 层**：人工审查后才公开。不过这个案例里 PR 一开出来就公开了，所以人工审查挡不住"开 PR 本身就是泄露"。【推断】RepoAegis 的目标仓库如果是公开的，草稿 PR 和 issue 汇报也是公开的，**所以第 4 层必须在发布前做完，不能指望人审兜底**。

---

## 第三部分：对 RepoAegis 的建议

前提：GitHub Actions 免费运行机（`ubuntu-latest`），个人项目，模型 DeepSeek（OpenAI 兼容接口），issue 作者可能是任何人。

### 建议的整体形状：拆成四个作业

参照 gh-aw 的"只读 agent + 独立写作业"和 codex-action 的"agent 放最后一步、后续工作换新作业"，把一次运行拆成四个作业，每个作业跑在一台新机器上，只靠工件传文件：

```
作业 1 检查（权限: issues: read）
  ├─ 再查一次打标签的人的权限（必须是 write/maintain/admin）
  ├─ issue 必须是 open、不带图片
  ├─ 从事件里取标题和正文（通过 env: 传，不写进 run:），做清洗
  └─ 输出：清洗后的文字（工件）

作业 2 agent（权限: {} 或 contents: read；拿 DeepSeek key，但 key 只给代理）
  ├─ checkout 时 persist-credentials: false
  ├─ 在沙箱里跑 agent：没有 sudo、出网只允许白名单、读不到 key
  ├─ 不读写 Actions 缓存
  └─ 输出：git diff 补丁 + 汇报文字（工件），不做任何 GitHub 写操作

作业 3 检查产出（权限: {}；不拿任何密钥）
  ├─ 补丁检查：禁止改 .github/、依赖清单、agent 指令文件等；文件数、大小上限
  ├─ 密钥扫描：补丁和汇报里不能出现 DeepSeek key、GitHub 令牌等格式
  └─ 汇报文字清洗：去 HTML 注释、@ 提及加反引号、非白名单 URL 替换

作业 4 发布（权限: contents: write, pull-requests: write, issues: write）
  ├─ 应用补丁，推到 repoaegis/issue-<编号> 新分支
  ├─ 开草稿 PR，在 issue 里留言，撤标签
  └─ 不运行仓库里的任何代码
```

### 必须做（每条附依据）

1. **工作流脚本里不出现 `${{ github.event.issue.* }}`，一律走 `env:`**。依据：`script-injections.md` L27-52；`secure-use.md` 约 L67-93。这挡的是"标题里藏 shell 命令"，和提示词注入是两回事。
2. **触发后再查一次打标签人的权限**，只认 write / maintain / admin。依据：triage 角色也能打标签（GitHub 角色权限表）；claude-code-action `permissions.ts` 约 L124-133 和 codex-action 的做法相同。可调 REST 接口 `GET /repos/{owner}/{repo}/collaborators/{username}/permission`；`GITHUB_TOKEN` 需要哪项权限才能调【待验证】。
3. **`permissions:` 按作业最小化**，顶层写 `permissions: {}`，每个作业单独声明。依据：`secure-use.md` 约 L32-34、L105；gh-aw `architecture.mdx` L148-153。
4. **agent 作业不拿任何 GitHub 写权限，checkout 设 `persist-credentials: false`，不往 agent 的环境里放 `GITHUB_TOKEN`**。依据：gh-aw 的只读 agent 作业（`architecture.mdx` L132-134）；actions/checkout 默认会把令牌留在磁盘上；AWF `--env-all` 会把 `GITHUB_TOKEN` 一起带进容器（`docs/chroot-mode.md` L120-131）。
5. **agent 不能有 sudo**。依据：codex-action `docs/security.md` L68-72，有 sudo 就能通过 `/proc` 读出同机器上其他进程内存里的密钥。用 AWF 时 agent 在容器里且能力已丢弃（`chroot-mode.md` L214-246）；不用 AWF 时，参照 codex-action 的 `drop-sudo`（`action.yml` L317-344）。
6. **DeepSeek key 走密钥代理，agent 只拿到占位值**（详见下面"key 怎么保管"）。依据：AWF `api-proxy-sidecar.md` L275-284；codex-action `action.yml` L215-242。
7. **agent 的产出在发布前做规则检查**：禁止改 `.github/`（以及 gh-aw 列的其他受保护文件），限制文件数和补丁大小，扫密钥，清洗汇报文字。依据：gh-aw `safe-outputs-pull-requests.md` L558-583、L728-772；`safe-outputs.md` L1982-1986；claude-code-action `sanitizer.ts` L79-119。对公开仓库而言，PR 一开出来就公开了，这一步不能指望人审兜底（MCP 事故的教训）。
8. **发布作业不运行仓库代码，只做 `git apply`、`git push`、调 API**。依据：codex-action `docs/security.md` L76-84（agent 跑过的机器不可信）；`secure-use.md` 约 L131（同一工作流里的作业会互相影响）。
9. **agent 作业不用 Actions 缓存**。依据：Clinejection 的缓存污染路径（Adnan Khan 原文）。【推断】
10. **所有第三方 action 固定到完整提交号**（包括 AWF 的 action）。依据：`secure-use.md` 约 L129-153。AWF 自己的 README 也要求固定到审过的提交（`README.md` L33-34）。
11. **PR 永远是草稿，合并只能由人做**。依据：Copilot `risks-and-mitigations.md` L39；gh-aw 的草稿策略（`safe-outputs-pull-requests.md` L140）。

### 可选（有用但不是必需，或者要付额外代价）

1. **AI 复查产出（照 gh-aw 的威胁检测作业）**：再调一次 DeepSeek，用安全提示词看补丁和汇报。依据：gh-aw `architecture.mdx` L348-425。代价：多一次模型调用；而且复查模型本身也可能被补丁里的文字注入。【推断】建议先把规则检查（必须做第 7 条）做扎实，再决定要不要加。
2. **保护分支 + `CODEOWNERS`**：给默认分支设"合并前必须审查"，`.github/` 由你本人负责审。依据：`secure-use.md` 约 L163；Copilot `build-guardrails.md` L45。个人仓库能用哪些分支规则【待验证】。
3. **出口白名单之外再加 URL 级过滤（SSL bump）**：AWF 支持，但要做中间人解密，配置复杂。依据：gh-aw `reference/network.md` L256-270。个人项目没必要。
4. **用 GitHub App 代替 `GITHUB_TOKEN` 发布**：好处是不用打开"允许 Actions 创建 PR"这个仓库级开关，提交和 PR 用独立身份；坏处是要管理 App 私钥。用 `GITHUB_TOKEN` 的好处是天然改不了 `.github/workflows`、作业结束就失效。这个选择需要你来定。
5. **issue 内容的 URL 白名单清洗**（照 gh-aw）：把非 GitHub 域名的链接替换掉，减少"引导 agent 去访问某网址"的注入。依据：`architecture.mdx` L544-569。

### 做不到，或在免费运行机上有限制

1. **GitHub 托管运行机没有现成的出口白名单**。平台只有 `/etc/hosts` 屏蔽挖矿网站（`runners-etc-hosts-file.md` L1）。Copilot 的防火墙只给 Copilot 用（`customize-the-firewall.md`）。Azure 私有网络只对大型付费运行机开放【待验证】。
   - **但 gh-aw 的防火墙（AWF）可以单独拿来用**：有自己的 action，需要 Docker 和 sudo（运行机都有），只支持 Linux x64。依据：gh-aw-firewall `README.md` L13-31、L76-85；`action.yml` L29-42。它同时带密钥代理，支持自定义 OpenAI 兼容上游（`api-proxy-sidecar.md` L408-419）。**这是免费运行机上最现成的方案**。DeepSeek 能否完整跑通（包括流式输出、工具调用格式）【待验证】，要在 `repoaegis-sandbox` 里试。
   - 第三方的 step-security/harden-runner 也宣称能在托管运行机上限制出口，本次没读原文【待验证】。
2. **白名单挡不住"通过允许的域名外传"**：至少 `api.deepseek.com` 必须放行，agent 理论上可以把仓库内容塞进发给模型的请求里。这对 RepoAegis 不是新风险，因为模型本来就要看代码。若放行 `pypi.org` 等包仓库，agent 也能下载任意包。【推断】
3. **密钥代理挡不住"冒用 key"**：agent 读不到 key，但可以通过代理随便调模型，把你的余额花光。【推断】需要在 DeepSeek 侧控制额度（见下）。
4. **清洗挡不住提示词注入**：所有项目都承认这一点（claude-code-action `docs/security.md` L78；Aikido 原文）。只能靠第 3-6 层限制注入成功后的后果。
5. **Cloud Hypervisor 微虚拟机隔离**（gh-aw 预览功能）要求带 KVM 的运行机。依据：`architecture.mdx` L190-197。免费运行机是否支持 KVM【待验证】。普通 Docker 容器和宿主机共享内核，内核漏洞挡不住（`chroot-mode.md` L246）。

### DeepSeek API key 怎么保管

**问题本身**：agent 能执行命令，如果 key 在它的环境变量里，`env` 一下就出来了；就算不在环境变量里，只要有 sudo，也能读别的进程内存（codex-action `docs/security.md` L72）。所以"key 只给 agent 那一步"**不够**，同一步里的 agent 照样读得到。

**各家的解法**：
- 不解决：Copilot（Agents 密钥直接进 agent 环境）、claude-code-action（默认在 Claude 进程环境里）。
- 同机隔离：codex-action 先以有权限的用户起代理（key 通过标准输入传入，然后从环境中去掉），再撤掉 sudo，让 agent 以无特权方式运行。
- 容器隔离：AWF 把 key 放在单独的 api-proxy 容器里，agent 容器里只有占位值。

**建议（二选一，需要你决定）**：

- **方案 A：用 AWF**（推荐，现成）
  - DeepSeek key 只出现在"启动 AWF"那一步的 `env:` 里（例如 `OPENAI_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}`），AWF 把它交给 api-proxy 容器。
  - 用 `--openai-api-target api.deepseek.com` 指向 DeepSeek，`--allow-domains` 只放行必需的域名。
  - agent 代码里用 OpenAI SDK，读 `OPENAI_BASE_URL`（AWF 自动设成代理地址），不用改代码（`api-proxy-sidecar.md` L207-270）。
  - 这一步的 `env:` 里不要放 `GITHUB_TOKEN`，或者用 `--exclude-env` 排除。
  - 依据见上文 3.2、3.3。
- **方案 B：照 codex-action 自己搭**
  - 写一个只转发 `POST /chat/completions` 的小代理，key 从标准输入读入；先启动代理，然后撤掉 `runner` 用户的 sudo，再以无特权方式起 agent。
  - 出口白名单还是得自己想办法（例如自己写 iptables 规则），等于重做 AWF 的一部分。
  - codex 的代理只支持 Responses API（`responses-api-proxy/README.md` L31-35），DeepSeek 是否支持 Responses API【待验证】，所以大概率不能直接复用。

**不论选哪种都要做**：
- 给 RepoAegis 单独建一个 DeepSeek key，不和别的项目共用；DeepSeek 后台是否支持单 key 额度或用量上限【待验证】，不支持的话至少控制账户余额。
- 存成仓库的 Actions 密钥，只在需要的那一步引用，绝不写进工作流文件。
- 发现泄露就立刻作废重建（`secure-use.md` 约 L37-38 的"删除并轮换"原则）。
- 不要开 `ACTIONS_STEP_DEBUG` 之类的调试输出，免得把命令输出全打进公开日志（claude-code-action `docs/security.md` L179-192 的同类警告）。

---

## 第四部分：待验证清单（建议在 `ETOLucy/repoaegis-sandbox` 里实测）

1. AWF + `--openai-api-target api.deepseek.com` 能否跑通 DeepSeek 的 chat completions，包括流式输出和工具调用。
2. 在 AWF 容器里执行 `env`、`cat /proc/*/environ`、`sudo -n true`，确认看不到真 key、没有 sudo。
3. 免费运行机上 AWF 的启动耗时，以及 `apt`、`pip` 等常用域名需要加哪些进白名单。
4. 用 `GITHUB_TOKEN` 推送一个改了 `.github/workflows/x.yml` 的分支，看是否被拒绝、报什么错。
5. 你的仓库里，"用 `GITHUB_TOKEN` 开的 PR 的工作流需要批准"是否已经生效。
6. 用 `GITHUB_TOKEN` 查打标签人权限，需要声明哪项 `permissions`。
7. claude-code-action 的 GitHub App 到底有没有 workflows 权限（它的两份文档说法矛盾）。
8. step-security/harden-runner 能不能作为 AWF 之外的另一个选择（本次没读原文）。
9. DeepSeek 后台有没有按 key 的额度控制。
10. `run:` 步骤的环境里有没有 `ACTIONS_RUNTIME_TOKEN`（决定 agent 不用 sudo 时能否直接写缓存）。
