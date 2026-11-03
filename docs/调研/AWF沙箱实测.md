# 调研报告：AWF 沙箱在 GitHub 免费运行机上的实测

> 实测日期：2026-10-28。地点：私有测试仓库 `ETOLucy/repoaegis-sandbox`，用一个临时工作流（只能手动触发）跑完后已删除。AWF 版本 v0.29.0（提交 `b8eaea2`），运行机镜像 ubuntu-24.04 `20261012.334.1`。全程没有调用模型，也没有用任何密钥。下文标【实测】的是日志里直接看到的，标【一手】的是读 AWF 源码或文档得到的，标【推断】的是从现象推出来的、没有单独验证。工作流原文和完整日志在 WSL 的 `~/scratch/awf-probe/`。

## 一、为什么测

[安全设计](../安全设计.md)定了让 agent 跑在 AWF 里，但"容器里实际是什么样"一直是待实测项。这些情况决定了接下来几块沙箱代码怎么写：容器里有哪些环境变量（核对 [agent 命令的环境变量](agent命令的环境变量.md) 定下的白名单），用什么用户、有没有 sudo，能读写哪里，网络白名单是否真的生效，装依赖能不能通，能不能换一个受限用户来跑命令，以及启动要花多久。模型和密钥代理这次不测（2026-10-28 定：先不碰模型）。

## 二、方法

一个作业里依次做：拉代码（不留令牌）→ 用 AWF 官方安装步骤装 v0.29.0 并预拉镜像 → 看运行机本身 → 往 `/tmp` 写一个探测脚本 → **探测 A**：`sudo -E awf` 以默认身份跑脚本，运行机这一步的环境里故意放一个假密钥 `fake-secret-123`，再用 `-e` 明着传一个变量 → **探测 B**：在运行机上新建普通用户 `probeagent`，把 `SUDO_UID`/`SUDO_GID` 换成它的编号再启动 AWF（AWF 按 `SUDO_UID` 决定命令用哪个用户跑，见 `src/host-identity.ts`），跑同一个脚本。

探测脚本只打印变量名和少数不敏感的值，检查：身份、环境变量、假密钥能否在环境和 `/proc/*/environ` 里找到、能看到的进程、特殊权限和 sudo、往几个目录写文件、访问白名单内外的网址、建虚拟环境用 pip 装一个小包。

一共跑了三次：第一次 AWF 安装失败，第二次探测 A 找不到 `awf` 命令，第三次全部成功。下面的数据都来自第三次（运行编号 40684757357）。

## 三、结果

### 能用，耗时可以接受【实测】

- 安装 AWF 并预拉两个镜像：33 秒。
- 探测 A 从启动 AWF 到结束：44 秒，其中包括现场下载密钥代理那个镜像（安装步骤只预拉了另外两个）。
- 探测 B：27 秒，此时镜像都已就绪；这 27 秒里还包括建虚拟环境和 pip 装包。
- AWF 这次用的出网控制方式是"靠 Docker 网络结构"，日志原话 "enforcing egress via Docker network topology (no host iptables, no sudo)"。

### 两个坑【实测】

- **官方安装步骤偶尔失败。** 第一次运行时，它安装前对 `gh` 的检查没过（报 "A trusted gh CLI with attestation verify support is required"），后两次都过了。运行机上的 `gh` 是 2.102.0，`--signer-workflow` 参数确实存在，同一条检查在第二次运行里连跑 5 次都通过。原因 2026-10-30 查清，见下面两条；正式用时要准备重试，或者查清后再定。
  - **2026-10-30 追查：** 失败那次和成功那次用的是同一版运行机镜像（`20261012.334.1`），检查花了约 0.2 秒，说明 `gh` 确实跑了【实测，运行 40684245761 的日志】。这句检查运行在 `set -euo pipefail` 下（【一手】`action.yml` 第 51 行）。`grep -q` 一找到匹配就退出，`gh` 此时如果还没写完帮助文字（约 8.9KB，`--signer-workflow` 出现 4 次），下一次写就会被系统用 SIGPIPE 信号结束，退出码 141；`pipefail` 让整条管道取这个 141，于是被当成"gh 不合格"。会不会撞上取决于两边谁先跑完，所以时好时坏【推断】。在 WSL 里用替身复现了这个机制：开着 `pipefail`、分多次写的替身 200 次全被判失败，退出码 `141 0`；关掉 `pipefail` 0 次；即使一次写完 7.4KB 的 `cat`，500 次里也有 1 次被判失败【实测，替身不是真的 gh】。真的 `gh` 在运行机上多久撞一次，没测。
  - **2026-10-30 用真的 `gh` 复现【实测】：** 在 WSL（8 核）里下载运行机上同版本的 `gh` 2.103.2（官方发布页，校验和对过），把这句检查原样跑 1000 次：**41 次被判失败（4.1%），每次都是 `gh` 退出码 141、`grep` 退出码 0**，即 grep 已经找到、gh 被 SIGPIPE 结束。帮助文字 8858 字节，`--signer-workflow` 第一次出现在第 36 行。改成 `grep -- '--signer-workflow' >/dev/null`（读完再退出）后跑 1000 次，0 次失败。原因从推断改为已证实；运行机（4 核）上的比例没测。脚本在 WSL 的 `~/scratch/gh-race/`。AWF 最新的 main（2026-10-29 `6b5573a8`）这句没改，仓库里也没有相关 issue。**2026-10-30 已报告并修复【一手】：** 我们提了 [github/gh-aw-firewall#10840](https://github.com/github/gh-aw-firewall/issues/10840)（该仓库只允许协作者开 PR，外部 PR 提不了）；维护者把 issue 交给 Copilot，Copilot 开的 #10852 用了同样的一行修法并补了测试，当天合并，已包含在 v0.29.1（2026-10-31 发布）。应对：工作流里失败就再装一次；根本修法是 AWF 把检查改成不提前退出的写法（例如 `grep -- '--signer-workflow' >/dev/null`，或先把帮助文字存进变量再查），可以给 AWF 提 issue。（已提 #10840，AWF v0.29.1 修复，经过见 [Copilot修复AWF的过程](Copilot修复AWF的过程.md)；我们现在固定 v0.29.0、装失败重装一次。）
- **文档里的 `sudo awf` 直接用会报 "command not found"。** 安装步骤把 AWF 放进 `$RUNNER_TEMP/awf-bin` 并加进 `PATH`，但 `sudo` 用自己固定的 `PATH`，看不到它。改成 `sudo -E "$(command -v awf)"` 就好了。

### 身份和权限【实测】

- 命令以运行机上的 `runner` 用户（uid 1001）运行，不是 root。它所在的组里有 `docker`（118），但 Docker 的接口在容器里被藏起来了（【一手】`docs/chroot-mode.md`）。
- 特殊权限全部拿掉（`CapEff` 为 0），`NoNewPrivs: 1`（之后不能再提权），开了系统调用过滤（`Seccomp: 2`）。
- `sudo` 用不了，报错是 "no new privileges" 标志阻止了它。

### 进程和假密钥【实测】

- `/proc` 以 `hidepid=2` 方式挂载：只看得到同一个用户自己的进程。`ps` 只列出 4 个：AWF 的启动脚本、探测脚本、`ps` 和 `head`。运行机上的其他进程完全看不到。
- 运行机这一步环境里的假密钥，在容器里自己的环境和所有 `/proc/*/environ` 里都找不到。也就是说，没有用 `--env-all` 时，宿主机上的变量不会被带进来。
- 用 `-e PROBE_PASSED=...` 明着传的变量能收到。

### 环境变量【实测】

容器里命令的环境（只列名字）：

```
AWF_API_PROXY_IP AWF_CHROOT_ENABLED AWF_DNS_SERVERS AWF_HOST_PATH AWF_INIT_SIGNAL_DIR AWF_JAVA_HOME
AWF_NETWORK_ISOLATION AWF_ONE_SHOT_TOKENS AWF_USER_GID AWF_USER_UID AWF_WORKDIR CURL_CA_BUNDLE
GITHUB_API_URL GITHUB_SERVER_URL GIT_SSL_CAINFO HOME HOSTNAME HTTPS_PROXY HTTP_PROXY JAVA_HOME
JAVA_TOOL_OPTIONS LD_LIBRARY_PATH LD_PRELOAD LOGNAME NODE_EXTRA_CA_CERTS NODE_OPTIONS NO_COLOR NO_PROXY
NPM_CONFIG_PREFIX OLDPWD PATH PROBE_PASSED PWD REQUESTS_CA_BUNDLE SHLVL SQUID_PROXY_HOST SQUID_PROXY_PORT
SSL_CERT_DIR SSL_CERT_FILE TERM USER XDG_CONFIG_HOME _ https_proxy no_proxy
```

和白名单对照：

- `PATH`、`HOME`、`USER`、`LOGNAME` 都有；`SHELL`、`TMPDIR` 没有（白名单里没有就跳过，不影响）。
- 代理：`HTTP_PROXY`、`HTTPS_PROXY`、`https_proxy` 都指向 `http://172.30.0.10:3128`，`NO_PROXY`/`no_proxy` 也有；小写 `http_proxy` 没有，和 AWF 文档一致。白名单都能接住。
- **白名单会丢掉的、可能有用的：** 一组指向 AWF 自己整理过的证书文件的变量：`SSL_CERT_FILE`、`SSL_CERT_DIR`、`CURL_CA_BUNDLE`、`REQUESTS_CA_BUNDLE`、`GIT_SSL_CAINFO`、`NODE_EXTRA_CA_CERTS`。值是 `/run/awf-lib/trust/ca-certificates.crt`，日志说这是"过滤过的宿主机证书"。丢掉后，工具会改用系统默认的证书目录（只读挂载着），大概率也能连上，但会失去 AWF 对证书做的过滤【推断，未测】。
- 其他会被丢掉的：`LD_PRELOAD`（AWF 的"令牌读一次就清掉"小程序，我们不给任何令牌，用不上）、`JAVA_TOOL_OPTIONS`（给 Java 工具设代理）、`NODE_OPTIONS`、`NPM_CONFIG_PREFIX`、`GITHUB_API_URL`/`GITHUB_SERVER_URL` 和一批 `AWF_*` 内部变量。Java、Node 项目要不要这些，和"装用户项目依赖"一起定。

### 能读写哪里【实测】

- 能写：仓库目录、整个家目录 `/home/runner`（包括 `/home/runner/work`）、`/tmp`。
- 不能写：`/usr/local`、`/opt`、`/etc`。
- 家目录里预装了一堆工具的配置目录：`.anthropic`、`.azure`、`.claude`、`.config`、`.copilot`、`.cargo`、`.npm`、`.nvm` 等，agent 都能读写。AWF 文档自己把"家目录可读写"列为 HIGH 风险（【一手】`docs/chroot-mode.md`）。按[安全设计](../安全设计.md)的四个作业拆分，agent 作业结束后整台机器丢弃，发布在另一台新机器上做，所以这里被改了也影响不到发布【推断】。
- AWF 启动时会往家目录写 Maven、Gradle 的代理配置（`~/.m2/settings.xml`、`~/.gradle/gradle.properties`）。

### 网络【实测】

- 白名单内 `https://pypi.org/simple/six/` 返回 200。
- 白名单外 `https://example.com`、`https://api.github.com` 都连不上，`curl` 退出码是 60（证书校验失败），而不是一个清楚的"被拒绝"。agent 看到的会是"SSL certificate problem"，容易误以为是证书配置问题【推断】。工具说明或提示词里可能要写明"只能访问白名单里的网址"。
- 白名单只放 `pypi.org` 和 `files.pythonhosted.org` 时，建虚拟环境再 `pip install six` 成功。

### 换一个用户跑（探测 B）【实测】

- 把 `SUDO_UID`/`SUDO_GID` 换成 `probeagent` 的编号后，命令确实以 `probeagent`（uid 1002）运行，它不在 `docker` 组里，其余权限情况和探测 A 一样。
- **但它照样能读写 `/home/runner`**，而且容器里看到 `/home/runner` 的属主变成了 `probeagent`。日志里还有一句 "Adjusting awfuser UID:GID from 1001:1001 to 1002:1002"。【推断】AWF 挂载的是原来那个 sudo 用户（`runner`）的家目录，并把属主改成了新用户；运行机上这个目录事后的属主没有单独查。另外 pip 报"缓存目录不可写"，`/home/probeagent` 本身也不可写。
- 结论：靠改 `SUDO_UID` 换用户能把整个容器换成另一个用户，但挡不住它碰 runner 的家目录，还有副作用。这条路不是现成可用的。

## 四、对之前设计的影响

1. **"让 agent 的命令换一个用户、读不到主程序的 `/proc`"在 AWF 里做不到。** 容器里没有任何特殊权限，也不能再提权，所以无法在容器内部切换用户；探测 B 换的是整个容器的用户，主程序和命令仍是同一个用户。`hidepid=2` 只挡别的用户的进程，同一用户的照样能看。所以如果主程序和 agent 的命令都跑在同一个 AWF 容器里（2026-10-30 定为这种跑法，见[安全设计](../安全设计.md)），主程序手里有什么，agent 就能读到什么。剩下的办法：主程序里不放任何真密钥（模型的 key 交给 AWF 的密钥代理）；或者主程序启动后关掉"允许被读取"的开关（之前在 WSL 里测过有效，见 [agent 命令的环境变量](agent命令的环境变量.md) 第五节），这在 AWF 容器里有没有效果还没测。
2. **白名单加上了那组证书变量**（2026-10-30 定，见下一节）。
3. **网络白名单至少要有装依赖用的网址**，具体名单和"装用户项目依赖"一起定。

## 五、借鉴和建议

- 照 AWF 默认，不用 `--env-all`，需要的变量用 `-e` 明着传：实测宿主机上的变量确实不会漏进去。
- 调用时用完整路径：`sudo -E "$(command -v awf)"`。
- 版本、安装步骤固定到完整提交号，镜像用安装步骤给出的带摘要的 `image-tag`（这次就是这样用的）。
- 白名单加上那 6 个证书变量：它们只是文件路径，不是秘密，留着能保住 AWF 的证书过滤，也避免某些工具在没有它们时连不上。**已采纳（2026-10-30），已加进 `src/repoaegis/sandbox/env.py` 的 `KEEP`。**
- 换用户这条路先放下；保护主程序靠"里面不放真密钥"。

## 六、尚未查清的

- AWF 安装步骤偶发失败的原因。
- 探测 B 之后运行机上 `/home/runner` 的属主是否真的被改了。
- 丢掉证书变量后 pip、git、curl 是否仍能正常连接白名单内的网址。
- "关掉允许被读取的开关"在 AWF 容器里是否有效。
- 密钥代理和模型：按 2026-10-28 的决定，先不测。

## 七、补充实测（2026-10-31）：容器里看不到运行机的 `_temp`

在测试仓库跑 agent 作业（测试分支 `test/langfuse-wrapup`，AWF v0.29.0）时，下载到 `$RUNNER_TEMP/input/` 的快照，容器里的程序读不到：`FileNotFoundError: '/home/runner/work/_temp/input/snapshot.json'`。日志里 AWF 是"chroot 模式，直接在主机文件系统里运行"，工作目录 `/home/runner/work/<仓库>/<仓库>/repo` 能正常读写。【实测：运行 41067788697】

把输入、输出目录都挪到 `$GITHUB_WORKSPACE/agent-io/` 之下后，容器里读到了快照和内容，写出的轨迹文件也被后面的上传步骤拿到了。【实测：运行 41152741753】

【推断】AWF 有意遮住了 `/home/runner/work/_temp`（这里放着步骤脚本、git 凭据配置等），日志里没有直接说明，AWF 文档也还没查。**影响**：agent 作业的输入（`SNAPSHOT_PATH`、`CONTENT_PATH`）和输出（`OUTPUT_DIR`、产物上传路径）已改到 `$GITHUB_WORKSPACE/agent-io/` 下（2026-10-31）。不在容器里的作业（开工前检查、收尾作业）仍用 `$RUNNER_TEMP`。第三节里"能写整个家目录 `/home/runner`（包括 `/home/runner/work`）"对 `_temp` 不成立。

## 八、补充实测（2026-11-03）：经密钥代理流式调用 DeepSeek

**为什么测。** README 把"AWF 能不能接上 DeepSeek（流式输出和工具调用）"列为待验证；读源码的结论是代理不缓冲、直接转发（见 [调用DeepSeek](调用DeepSeek.md) 第四节），这次真跑一次。

**方法。** 测试仓库里放一个临时工作流（运行 41640831067，跑完已删，提交 `5b9653a` 加、`b854cc4` 删）：AWF v0.29.0，真钥匙只放在启动 `awf` 那一步的 `OPENAI_API_KEY`，加 `--openai-api-target api.deepseek.com`，**不写** `--allow-domains`；容器里用 RepoAegis 当时的 `model.py`（含 11-03 的三处改动）跑三轮：不带工具；带 bash 工具并要求一次调两个；把第二轮的回答（含思考内容）和假的工具结果发回去再问。测试脚本只在测试仓库里放过，没有进 RepoAegis 仓库。

**结果【实测】：**
- 三轮都成功，没有重试：每轮 1.0–1.4 秒，结束原因依次 `stop`、`tool_calls`、`tool_calls`；思考内容、正文、用量（含缓存命中和思考 token）都收到了。
- 两个工具调用各自拼成合法 JSON（`{"command": "echo a"}`、`{"command": "echo b"}`），编号 `call_00_…`、`call_01_…`。
- 第三轮带着思考内容回传，没有 400；输入 444 token 里 256 命中缓存。
- 容器里 `OPENAI_BASE_URL=http://172.30.0.30:10000`，`OPENAI_API_KEY` 是 28 个字符的占位值 `sk-placeholder-for-api-proxy`；容器内所有进程的环境里，不是占位值的 `sk-` 字符串 0 个。AWF 日志："Unset OPENAI_API_KEY from /proc/1/environ"。
- 没写 `--allow-domains`，日志里 "Allowed domains: https://api.deepseek.com"：**代理会自动把上游放进白名单**，源码和文档不一致的那一处以源码为准。【推断】因此容器里的命令也能经 Squid 访问 `api.deepseek.com`，但只有占位钥匙，会被拒；没有单独测。
- 从启动 `awf` 到脚本第一行输出约 25 秒，其中包括现场拉密钥代理的镜像（安装步骤的 `pull-images` 不拉它）。

**没覆盖到的。** 回答都很短：没有排队、没见到 `: keep-alive`，没有长时间思考，也没有半路断开或中途报错；两个工具调用的碎片是交错到达还是先后到达，没有记录原始流。这些只有单元测试覆盖（`tests/test_model.py`）。
