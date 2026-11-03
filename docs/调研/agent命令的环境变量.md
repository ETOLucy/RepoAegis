# 调研报告：agent 执行的命令能看到哪些环境变量

> 调研日期：2026-10-27。源码由两个调研子 agent 读取各仓库当天的主分支（mini-swe-agent 04d809c、SWE-agent 3ea751c、SWE-ReX 5c995c3、OpenHands software-agent-sdk d0d9d81、codex 6fe77fc，其余用 `gh api` 直接读）；文档读的是原文。本报告转述其结果。我亲自复核过三处：AWF 的排除名单（`excluded-vars.ts`）、Codex 的 core 名单（`shell_environment.rs` 178–181 行）、下文第五节的 `/proc` 实测。其余行号未逐条复核。标【一手】的是子 agent 打开过原文，标【实测】的是在 WSL 里跑出来的，标【推断】的是从原文推出来的，标【待验证】的是没查到原文。

## 一、为什么查

bash 工具（`src/repoaegis/tools/bash.py`）每条命令都新开一个 `bash -c` 子进程，构造函数的 `env` 参数决定这个子进程能看到哪些环境变量。现在不传时，子进程会继承主程序的全部变量。agent 能执行任意命令，敲一句 `env` 就能看到所有变量，所以这里要定清楚：哪些变量留着，哪些去掉，另外再加哪些。

已定的安全设计（[安全设计](../安全设计.md)）：agent 作业不放 GitHub 令牌、不给 sudo；DeepSeek 密钥交给 AWF 的密钥代理，agent 只拿到一个占位值。

## 二、方法和出处

- agent 程序这一侧：mini-swe-agent `environments/{local,docker}.py`、`config/default.yaml`、`config/benchmarks/swebench.yaml`；SWE-agent `config/default.yaml`、`sweagent/tools/tools.py`、`sweagent/environment/swe_env.py`；SWE-ReX `runtime/local.py`；OpenHands SDK `utils/command.py`、`tools/terminal/{impl.py,terminal/subprocess_terminal.py}`、`conversation/secret_registry.py`；Codex `codex-rs/protocol/src/shell_environment.rs`、`config/src/shell_environment_policy.rs`、`core/src/unified_exec/process_manager.rs`，以及 developers.openai.com/codex/config-reference；Claude Code 文档 code.claude.com/docs/en/env-vars 的 "What the subprocess environment scrub removes" 一节。
- Actions 这一侧：github/docs `content/actions/reference/workflows-and-actions/variables.md` 20–79 行、`reference/security/oidc.md` 560–608 行；actions/runner `ScriptHandler.cs` 314–319 行、`NodeScriptActionHandler.cs` 55–73 行、`GitHubContext.cs` 9–68 行；gh-aw-firewall（AWF）`docs/environment.md`、`src/services/agent-environment/{excluded-vars,env-passthrough,core-environment,github-actions-environment}.ts`、`src/services/credentials/openai-credential-env.ts`；gh-aw `pkg/workflow/awf_env.go` 107–217 行、`awf_command_builder.go` 608–630 行；codex-action `action.yml` 215–242、346–382 行；claude-code-action `action.yml` 225–247 行、`base-action/src/parse-sdk-options.ts` 286–294 行；Copilot 文档 `configure-secrets-and-variables.md`、`customize-the-agent-environment.md`。
- Linux：man7.org `proc_pid_environ(5)`、`ptrace(2)` 的 "Ptrace access mode checking" 一节，kernel.org Yama.rst；实测脚本在 WSL `~/scratch/env-research-a/probe.py`。

## 三、各家怎么做

**整体分两派。** 一派默认把父进程的变量全部继承下来，最多删掉自己用的几个凭据：mini-swe-agent、SWE-ReX、OpenHands、Codex（默认配置）、Claude Code（默认配置）。它们大多跑在容器里或开发者自己的电脑上，安全不靠这一层。另一派只放一份白名单里的变量：AWF 默认就这样做，Codex 设 `inherit=core` 时也是。

- **mini-swe-agent**【一手】：在本机运行时用 `os.environ | config.env`，全部继承；在 docker 里运行时，只传 `forward_env` 名单上的变量和 `env` 里写死的值。默认会加 `PAGER=cat`、`MANPAGER=cat`、`LESS=-R`、`PIP_PROGRESS_BAR=off`、`TQDM_DISABLE=1`。它的 SWE-bench 配置还加了 `BASH_ENV`，原因是 `bash -c` 不读 `.bashrc`，不加就激活不了 conda 环境。
- **SWE-agent**【一手】：宿主机上的变量要写进 `propagate_env_variables` 名单才会带进容器，源码注释提醒这些值会出现在调试日志里。默认加的变量比 mini 多一个 `GIT_PAGER`，另外还加 `LANG`、`LC_ALL=C.UTF-8`。
- **OpenHands SDK**【一手】：全部继承，只删 SDK 自己用的几个凭据。注释写明，删掉是为了"防止 agent 通过终端命令拿到凭据"。其他密钥放在一个登记表里，命令文本里出现某个密钥的名字时才临时 `export`，输出里再把密钥的值遮掉。另外会加 `GIT_PAGER=cat`、`PAGER=cat`、`TERM`，以及 `AI_AGENT=openhands`。
- **Codex CLI**【一手】：默认 `inherit=all`，并且按名字过滤的功能默认关闭（`ignore_default_excludes=true`）。打开这个功能后，名字里含 `KEY`、`SECRET`、`TOKEN` 的变量都会删掉。`inherit=core` 只留 `PATH SHELL TMPDIR TEMP TMP HOME LANG LC_ALL LC_CTYPE LOGNAME USER`。不管怎么配置，最后都会删掉 Codex 自己的几个令牌。执行命令时还会加 `NO_COLOR=1`、`TERM=dumb`、`LANG`/`LC_CTYPE`/`LC_ALL=C.UTF-8`、`COLORTERM=`（设为空），以及 `PAGER`/`GIT_PAGER`/`GH_PAGER=cat`。
- **Claude Code**【一手，只看了文档】：默认不清理。设了 `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` 后，会按变量名和值的样子删掉凭据，但 `GITHUB_TOKEN`、`GH_TOKEN` 和代理设置会保留。在 Linux 上，它还把命令放进单独的进程编号空间（PID namespace），这样命令就看不到主进程的 `/proc`。claude-code-action 在允许没有写权限的人触发时，会自动打开这个开关；无论怎么配置，它都会删掉两个 OIDC 变量。
- **AWF**【一手】：默认只把一份固定名单上的变量传进 agent 容器。加 `--env-all` 会全部传进去，再剔掉一份写死的排除名单：`PATH`、`SUDO_*`、`ACTIONS_RUNTIME_TOKEN`、`ACTIONS_RESULTS_URL`、两个 OIDC 变量、各种代理变量；开了密钥代理时，再加上各家模型的 key 和 7 种 GitHub 令牌。官方文档说 `--env-all` 只适合本地开发。AWF 自己会往容器里放 `HTTP_PROXY`/`HTTPS_PROXY`（都指向它自带的 Squid 代理）、`NO_PROXY`、`HOME`、写死的 `PATH`，以及 `OPENAI_BASE_URL`（指向密钥代理）和占位 key `sk-placeholder-for-api-proxy`。这几个 OpenAI 变量最后在哪一步合进 agent 的环境，没追到【待验证】。gh-aw 固定用 `--env-all`，编译时把引用了 `secrets.*` 的变量都算进排除名单。
- **codex-action**【一手】：真密钥从标准输入交给一个单独的代理进程，启动时用 `env -u` 把它从环境里删掉。Codex 本身继承整个步骤的环境，只删 `ENV`、`BASH_ENV`、`SHELLOPTS`、`SUDO_*`，再重设 `HOME`、`USER`、`PATH`。
- **Copilot**【一手】：文档只写了"Agents 类型的密钥和变量会作为环境变量交给 agent"。agent 具体看得到哪些 `GITHUB_*`，文档没写【待验证】。

## 四、Actions 的 `run:` 步骤里本来就有什么

- 默认有 `CI`、一批 `GITHUB_*` 和一批 `RUNNER_*` 变量，内容都是路径、名字、编号。`GITHUB_TOKEN` 不会自动出现，要工作流自己写进 `env:` 才有【一手】。
- `ACTIONS_RUNTIME_TOKEN`、`ACTIONS_CACHE_URL`、`ACTIONS_RESULTS_URL` 只发给 JS 写的 action，`run:` 步骤里没有【一手，runner 源码】。这回答了[上一份安全调研](Actions里运行agent的安全做法.md)末尾留下的第 10 个问题。
- 只有作业声明了 `id-token: write`，`run:` 步骤里才会有 `ACTIONS_ID_TOKEN_REQUEST_URL` 和 `ACTIONS_ID_TOKEN_REQUEST_TOKEN`【一手】。runner 源码里，后者和 `ACTIONS_RUNTIME_TOKEN` 取的是同一个值【代码一手，"所以等于拿到了 runtime 令牌"是推断】。

## 五、只给子进程一份干净的环境，够不够

不够。【一手】`/proc/<进程号>/environ` 里存的是进程启动那一刻的环境，进程之后在自己内存里删掉某个变量，这个文件里还有。同一个用户的进程互相读这个文件，只需要通过"读取"级别的检查：uid、gid 相同，并且对方允许被读取（内核把这个开关叫 dumpable）就行。Yama 的 `ptrace_scope=1` 只拦"附加调试"级别的操作，拦不住读 environ。

【实测】在 WSL（`ptrace_scope=1`）里做了两组对照：

- Python 进程先把假密钥从 `os.environ` 里删掉，再给子进程一份干净的环境。子进程自己的环境里看不到密钥，但读 `/proc/<父进程号>/environ` 照样能读出来。
- Python 调用 `prctl(PR_SET_DUMPABLE, 0)` 关掉"允许被读取"之后，子进程再读就是 Permission denied。

【推断】在 Actions 里，用 `env:` 传进步骤的密钥同样存在于那一步的外层 shell 进程里，agent 也能去读它。所以真正的防线是：**agent 所在的容器里根本没有真密钥**（这一点由 AWF 的密钥代理做到），或者让命令用另一个用户身份跑、放进单独的进程编号空间。给子进程一份干净的环境只是第二层：它防的是 agent 敲一句 `env`、或者把环境顺手打进日志和补丁里。

## 六、借鉴什么，不借鉴什么

- **借鉴 AWF 和 Codex `core` 的白名单做法**：只放命令运行必需的变量，其余一概不带。不借鉴"全部继承再删几个"：漏一个就是泄露，而我们的主程序以后会加什么变量（例如监控的 key）现在说不准。
- **借鉴各家都加的"别等人"变量**：`PAGER`、`GIT_PAGER`、`MANPAGER` 设成 `cat`，`NO_COLOR=1`，`TERM=dumb`，`LANG`/`LC_ALL=C.UTF-8`，`PIP_PROGRESS_BAR=off`，`TQDM_DISABLE=1`。我们的命令没有标准输入，分页器一旦等人按键，命令就只能卡到超时。
- **不借鉴 OpenHands 的"按命令文本临时注入密钥"**：agent 不需要任何密钥。
- **不借鉴 mini-swe-agent 的 `BASH_ENV`**：用户项目的虚拟环境，留到讨论"agent 怎么跑测试"时再定，到时直接往 `PATH` 里加。

## 七、据此的建议

1. bash 工具的环境改成白名单：从主进程环境里只取 `PATH`、`HOME`、`USER`、`LOGNAME`、`SHELL`、`TMPDIR`，以及 AWF 设的 `HTTP_PROXY`、`HTTPS_PROXY`、`NO_PROXY`（大小写两套都取），再加上第六节那组"别等人"的变量和 `CI=true`。（2026-10-30 又加了 AWF 的 6 个证书变量，见 [AWF沙箱实测](AWF沙箱实测.md)。）
2. 交给子进程之前再查一遍：名字里含 `KEY`、`SECRET`、`TOKEN`、`PASSWORD`、`CREDENTIAL` 的变量，直接报错停下，不悄悄删掉。白名单里本来就不该有这种变量，出现了说明配置有问题，要让人看见。
3. 工作流里，agent 作业不声明 `id-token: write`，`env:` 里不放任何密钥（DeepSeek 密钥只交给 AWF）。
4. 主程序自己也不在环境里放真密钥。监控用的 Langfuse key 要放在哪里，单独讨论（见下节）。

## 八、尚未查清的

- AWF 的 `OPENAI_BASE_URL` 和占位 key 怎么进入 agent 环境、容器里的 `PATH` 具体是什么，要在测试仓库里实跑 `env` 看【待验证】。
- **（2026-10-30 已定：Langfuse 的 key 只给容器外的收尾作业，运行结束后一次性上传。）Langfuse 的 key 和"容器里没有真密钥"有冲突。** 实时上报需要主程序拿着 key，而主程序和 agent 的命令在同一个容器里，由同一个用户运行，agent 能从 `/proc` 读到。可选的做法有：主程序启动后调 `prctl` 关掉"允许被读取"，同时保证外层进程的环境里也没有这个 key；让 agent 的命令用另一个用户身份跑；或者放弃实时，等 agent 作业结束后由别的作业上报。要等写监控时再定。
- 要不要额外加 `GIT_TERMINAL_PROMPT=0`、`PIP_NO_INPUT=1`。没有标准输入时，这两种提示会直接失败而不是卡住【推断，未实测】。
