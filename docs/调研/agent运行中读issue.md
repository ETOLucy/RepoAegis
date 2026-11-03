# 调研报告：让 agent 想读几层引用就读几层，怎么保证安全

> 调研日期：2026-11-01。两个子 agent 分头读源码和官方文档，主对话抽查了关键出处（下文标"已抽查"的）。每条事实标【一手】（附出处位置）、【推断】或【待验证】；没被抽查的一手条目是子 agent 读原文后的转述。

## 一、为什么查

现在的规则（[agent读到的内容](../agent读到的内容.md) 第五节，2026-10-26 定）：开工前拿令牌的检查作业只抓被打标签 issue 直接引用的那些 issue，被引用的 issue 里又引用了别的 issue 就不开工；agent 那边没有令牌，用 `read_issue_ref` 读预先抓好的文件。2026-11-01 用户希望给 agent 足够的自由度，"能调几层是几层"，同时保证安全，问别的项目怎么做、安全怎么保证。另问：GitHub 那边的引用是怎么传过来、怎么知道是 issue 的。

之前的相关调研：[issue引用与打标签前后的修改](issue引用与打标签前后的修改.md)（引用一般套几层、谁写的，实测）、[Actions里运行agent的安全做法](Actions里运行agent的安全做法.md)。

## 二、方法和出处

- **GitHub 官方 MCP 服务器**（MCP 是让 agent 调用外部工具的一种协议，这个服务器就是 GitHub 官方给 AI 读写 GitHub 的工具）：`github/github-mcp-server` 提交 `f2a07cd`（2026-10-28），读 `pkg/lockdown/`、`pkg/github/issues.go`、`pkg/sanitize/`、`cmd/github-mcp-server/main.go`、`README.md`，以及 2025-12-10 的 GitHub changelog。
- **gh-aw**：`github/gh-aw` 提交 `f648f58`（2026-10-30），和它的"MCP 网关"`github/gh-aw-mcpg` 提交 `0e74bbf`（2026-10-30）。网关是 agent 和 GitHub 工具之间转发请求的中间服务，按作者过滤就是在这里做的。
- **其他项目**：github/docs `5ecebc9`（Copilot cloud agent）、claude-code-action `6d5ae52`、OpenHands/docs `efb9076` 与 OpenHands/extensions `6fb2b67`、codex-action `c05cfd7`，以及 Codex cloud、Devin、Jules 的官方文档页（2026-11-01 读）。
- **安全研究**：Invariant Labs 2025-05-26 的 GitHub MCP 文章（原文）；RoguePilot、PromptPwnd、Trail of Bits 三篇只看了摘要或二手报道【待验证】。
- **GitHub 平台**：github/docs 的自动链接、REST 时间线事件两页；GraphQL 用接口自省查了类型；在测试仓库 `ETOLucy/repoaegis-sandbox` 只读地调了接口【实测】。

主对话抽查过：github-mcp-server `README.md:1702-1708`（锁定模式的定义和"不是权限边界"的原话）、`pkg/lockdown/lockdown.go:296-314`（按推送权限判断）和 `:110-111`（两个机器人账号直接放行）；gh-aw `pkg/workflow/mcp_github_config.go:323-327`（GitHub 工具写死只读）。

## 三、各家做法

### GitHub 官方 MCP 服务器：锁定模式

锁定模式（lockdown mode）默认不开，开了以后，对**公开仓库**的内容按作者过滤：只认作者现在有没有推送权限，调 `GET /repos/{o}/{r}/collaborators/{user}/permission`，`admin` 或 `write` 才算可信（`maintain` 在这个接口里显示为 `write`）（已抽查 `lockdown.go:296-314`）。私有仓库不过滤；`github-actions[bot]` 和 `copilot` 写的直接算可信（已抽查 `:110-111`）；令牌主人自己写的也算可信。

过滤只作用在读单个 issue 和 PR 的工具上【一手 `pkg/github/issues.go:929-933, 1046-1068, 1116-1138`】：读一个 issue 时作者不可信，整条请求返回错误 "access to issue details is restricted by lockdown mode"；读评论、子 issue 时，不可信作者的条目直接删掉、不提示；`list_issues`、`search_issues` 不受影响，而它们会返回正文【一手：grep 结果】。

README 原话说它是"尽力而为的内容过滤"，**不是权限边界**：被挡掉的内容，用同一个令牌换别的工具或直接调接口还是能拿到（已抽查 `README.md:1704`）。只看作者，不看后来是谁改过正文【一手：grep 无 lastEditedBy】。

正文只删不可见字符（Unicode 标签字符、双向控制符、零宽字符），不处理 HTML、链接【一手 `sanitize.go:46-48, 150-165`】。没有调用次数上限，也没有引用深度上限【推断：代码和参数里都没找到】。服务本身不能限定只访问某个仓库，官方建议在令牌上限定【一手 `docs/policies-and-governance.md:189-193`】。

### gh-aw：令牌放进网关，agent 只能托网关读

agent 在运行中可以随意调用 GitHub 的读工具，但令牌不在 agent 手里：

- GitHub MCP 服务器和网关在单独的容器里，令牌只交给它们；编译时把令牌和网关编号排除在 agent 容器的环境变量之外【一手 `pkg/workflow/awf_env.go:128-139`；`architecture.mdx:275-302`】。agent 容器上网只能走域名白名单代理【一手 `architecture.mdx:191`】。官方也说明，挂进 agent 容器的网关编号要当作"已经泄露"，安全靠容器隔离、网络规则和"写操作放到后面的作业做"【一手 `architecture.mdx:306`】。
- GitHub 工具一律只读，写死在代码里（已抽查 `mcp_github_config.go:323-327`）。
- 可以限定仓库范围：`allowed-repos` 设成 `current` 就只读当前仓库；公开仓库没配置时自动设成只读公开仓库，防止私有内容流进公开仓库【一手 `reference/cross-repository.md:63-88`；`determine_automatic_lockdown.cjs:11-12, 66-72`】。这正对应 Invariant Labs 那次事故（恶意公开 issue 让 agent 读私有仓库再写进公开 PR）的教训。
- **完整性过滤**（`min-integrity`）在网关里做：每条结果交给一个检查模块打"可信等级"标签，低于门槛的删掉【一手 `mcpg/internal/server/backend_call.go:411-449`】。等级从高到低是 merged、approved、unapproved、none；作者是 OWNER、MEMBER、COLLABORATOR 算 approved，公开仓库里再查一次推送权限，有 write 以上的也提到 approved【一手 `labels/constants.rs:14-27`；`helpers.rs:1940-2021`】。公开仓库默认门槛是 approved，私有仓库不过滤【一手 `integrity.md:277-281`】。它取代了 gh-aw 早先的 `lockdown: true` 开关（2026-03 周报）。
- **被滤掉时明确告诉 agent**：读单条时返回"[Filtered] … exists but is not accessible"，让 agent 不会误以为它不存在；读列表时附一段说明，列出最多 5 条被扣下的描述；每条过滤都写进日志【一手 `backend_call.go:453-507`；`integrity.md:359-368`】。
- **可以按工具设调用次数上限**，例如 `issue_read` 最多调 N 次，超了返回 "tool call limit reached"【一手 `github-tools.md:64-83`；`mcpg/internal/server/unified.go:267-299`】。没找到引用深度上限和总字数上限【推断】。

### 其他项目

- **Copilot cloud agent**：默认开着 GitHub MCP 服务器，令牌只读、只限当前仓库【一手 `configure-mcp-servers.md:358-360`；`give-access-to-resources.md:37`】。用户输入交给 agent 前会滤掉隐藏字符【一手 `risks-and-mitigations.md:54`】，只理会有写权限者的评论；agent 运行中通过工具读到的其他 issue 是否同样过滤，文档没写【待验证】。cloud agent 文档里没出现锁定模式。
- **claude-code-action**：要显式开放 `mcp__github` 才会启动 GitHub MCP 服务器，启动时没开只读、没开锁定模式；令牌是只限当前仓库的短期令牌，权限含 issue、PR、代码读写【一手 `src/mcp/install-mcp-server.ts:92-93, 213-230`；`docs/security.md:19-21`】。它的清洗只用在开工前拼进提示词的内容上，agent 运行中读到的不清洗【推断】。
- **OpenHands**：`GITHUB_TOKEN` 直接放进 agent 的命令环境，内置技能教 agent 用 GitHub 工具或 `curl` 调接口，读什么、读几层都不限【一手 `secrets-settings.mdx:65`；`skills/github/SKILL.md`】。没找到对读到的内容做清洗或作者过滤。
- **codex-action**：跑 Codex 的那一步没有传 GitHub 令牌，agent 默认读不了 GitHub【推断，`action.yml:149-152`】。**Codex cloud**：agent 阶段默认不能上网，文档拿"issue 里藏指令"举例，建议只放行可信地址和 GET 这类请求【一手 learn.chatgpt.com/docs/cloud/internet-access】。Devin、Jules：没找到运行中读其他 issue 的说明。
- **安全研究**：Invariant Labs 建议每个会话只允许访问一个仓库、用运行时规则强制执行，并认为光靠模型不够、要在系统层面防【一手】。专门讲"顺着 issue 引用读到恶意内容"的公开案例没找到。

### GitHub 平台：引用怎么传过来

- 通过接口拿到的 issue 正文是原始 Markdown 文字，`#3` 就是三个字符；GitHub 只在网页显示时才把它变成链接【一手 `autolinked-references-and-urls.md:29-37`】。所以程序要自己从文字里认出引用。
- 认出编号后，用 GraphQL 的 `issueOrPullRequest(number)` 查，返回类型只可能是 `Issue` 或 `PullRequest`，查不到返回空【一手：接口自省；实测：测试仓库里 3→Issue、7→PullRequest、9999→空】。RepoAegis 的 `content_check.py` 就是这么查的。
- GitHub 另外记录"被谁引用"：被引用的 issue 的时间线里有 `cross-referenced` 事件，写明是哪个 issue 或 PR 引用了它【一手 `issue-event-types.md:242-269`；实测】。但这是反方向的，没有"我引用了谁"的接口，所以要知道一个 issue 引用了谁，还是得解析它的正文【推断】。

### 不是指 issue 的 `#3` 怎么办（2026-11-01 补查）

**各家怎么认。** OpenHands 旧 resolver 的 `extract_issue_references`：先删代码块、行内代码、带 `#数字` 的网址，再用正则找前面不是字母数字、后面跟空白或标点的 `#数字`【一手 `openhands/resolver/utils.py:135-151`，提交 `e7934ea6`】。gh-aw 只在配置了允许的仓库时，把代码以外的 `#编号` 和 `owner/repo#编号` 包进反引号，用的正则是前面不是字母数字的 `#\w+`【一手 `actions/setup/js/sanitize_content_core.cjs:1288-1330`】。claude-code-action 不找引用。GitHub 文档只列了五种会自动变成链接的写法（完整网址、`#26`、`GH-26`、`用户名/仓库#26`、`组织/仓库#26`），没写边界规则【一手 `autolinked-references-and-urls.md:29-37`】。RepoAegis 的 `content_check.find_references` 按这五种写法认，先删代码和 HTML 注释。

**Copilot cloud agent**（闭源，只能看文档）：文档里没有任何"怎么从正文里认出引用"的说明【一手：grep `content/copilot` 无结果】。它默认开着 GitHub MCP 服务器，令牌只读、只限当前仓库【一手 `content/copilot/how-tos/copilot-on-github/customize-copilot/configure-mcp-servers.md:358`】，所以"#3 是不是引用、要不要读"是**模型自己看正文判断**，想读就调读 issue 的工具，读几层不限【推断】。它的防护写在 `risks-and-mitigations.md:34-56`：只有有写权限的人能触发，没有写权限者的评论"永远不交给 agent"，传给 agent 的用户输入会先滤掉隐藏字符（如 HTML 注释），外网有防火墙，只能推一个分支、只开草稿 PR。这些说的都是"触发它的 issue 和评论"；agent 运行中用工具读到的其他 issue 会不会同样按作者过滤、滤隐藏字符，文档没写，cloud agent 文档里也没出现锁定模式【待验证】。

**实测：GitHub 把哪些变成链接，我们认成哪些。** 用 GitHub 的 Markdown 渲染接口（只渲染、不写入）在测试仓库的上下文里渲染 13 种写法，再用 `find_references` 跑同样的 13 句【实测，脚本在 WSL `~/scratch/ref-render/`】。测试仓库里 #3 是 issue、#2 和 #7 是 PR、#333 和 #9999 不存在。

- 两边都**认**：`和 #3 类似`、`(#3)`、`#03`（都指向 #3）、`GH-3`、`见第 #2 步`。
- 两边都**不认**：`用 C#3 写的`、`第#3条`、`step#3`、`#3a`、`&#3; 实体`、写在反引号里的 `` `#3` ``。
- **只有我们认**：`颜色 #333 太浅`、`编号 #9999 不存在`。GitHub 不链接是因为这两个编号在仓库里不存在；我们认出来以后去问 GitHub，得到"不存在"，记成 `not_found`、不读。如果 #333 真的存在，GitHub 也会把它变成链接【推断】。

结论：我们的识别和 GitHub 网页上"哪些变成了链接"一致；剩下的歧义（`见第 #2 步` 里的 #2 其实想说"第 2 步"）GitHub 自己也分不出，网页上同样显示成链接，写 issue 的人当时能看到。误认的后果是多抓一篇无关的 issue 当背景资料（照样要过作者检查），在"多于一层就不开工"的规则下还可能因此不开工；改成按总量设上限后，只是占掉一点额度【推断】。

## 四、借鉴什么，不借鉴什么

- **核心思路三家一致，借鉴：安全按"每一篇"把关，不按"层数"把关。** gh-aw 和 GitHub MCP 服务器都允许 agent 想读多少读多少、不限深度，安全靠每一篇单独过滤（作者可信才给看）、只读、限定仓库、令牌不在 agent 手里。层数本身和安全关系不大：第三层的 issue 和第一层的一样，只要作者可信、内容清洗过，就一样安全；不可信的，哪怕在第一层也不该给看【推断】。我们现在"多于一层就不开工"其实是把"内容多了、远了、无关了"的问题当成了安全问题。
- **借鉴 gh-aw 的"被滤掉时明确告诉 agent"和"每条过滤留日志"。** 我们的 `read_issue_ref` 已经这么做（返回不读的原因，`details` 里记原因）。
- **借鉴 gh-aw 的调用次数上限**，改成我们的"篇数和总字节数上限"。
- **不借鉴"只看作者"。** 两家都不看后来是谁改过正文，也读的是当前最新版本。我们已经定了更严的：看打标签那一刻的版本、查最后修改人。Triage 角色能改别人的 issue（[issue引用与打标签前后的修改](issue引用与打标签前后的修改.md) 第六节），只看作者有漏洞。
- **不借鉴把 `github-actions[bot]` 直接当可信。** 仓库里任何工作流都可能用这个账号发内容，包括被陌生人触发的工作流【推断】。
- **暂不借鉴"运行中托网关去 GitHub 读"。** 它的好处是能读任何 issue、能搜索；代价是在 agent 旁边放一个拿着令牌的服务，要多搭一个容器、多维护一套过滤，而且这套过滤要逐条重写我们已经有的规则（打标签那一刻的版本、最后修改人、清洗、去图片）。我们的引用都能从正文顺藤摸瓜找到，开工前一次抓完就够了，不需要运行中去 GitHub。

## 五、建议（待用户确认）

把"只读一层、多于一层就不开工"改成"**顺着引用一层层往下抓，抓满上限为止，agent 想读哪篇读哪篇**"：

1. **开工前一层层抓。** 检查作业从被打标签的 issue 开始，按层往下找引用（先抓完第一层，再抓第二层……），每一篇照现在的规则单独把关：只抓本仓库的 issue（PR、讨论区、别的仓库只列编号）；读打标签那一刻的版本；最后修改人没有写权限的不读、也不顺着它往下找；清洗、去图片。这些代码 `content_check.py` 里都有，只是从"一层"改成循环。
2. **不再因为层数不开工。** 去掉"多于一层就不开工"这条。
3. **按总量设上限，不按层数。** 比如最多 20 篇、合计 32,000 字节（约 8,000 token），按层由近到远抓，抓满就停，没抓的只列编号和原因（"超出上限"）。之前的实测里，最长的链是 black 仓库，顺着"总目录"式 issue 能抓出 200 篇左右，上限就是防这种情况；具体数值待定。
4. **agent 一层层看。** 第一条消息仍只列第一层的编号、标题、状态。agent 用 `read_issue_ref` 读一篇时，返回里附上这篇又引用了哪些、各自能不能读，agent 想往下读就接着调。`read_issue_ref` 的参数和读不到时的说明不变，只是能读的范围从"第一层"变成"抓到的所有篇"。
5. **安全不变的部分。** agent 那边仍然没有任何令牌、上网受限；读到的所有内容都是开工前抓好、把过关的；产出照样经过发布前检查。

## 五之二、用户的决定和实测（2026-11-02）

用户 2026-11-01 不同意"开工前一层层抓"，要求和别家一样**按需读**："agent 想读才去 GitHub 读一篇，读多少抓多少。我们也这样。"于是改成：agent 作业里另起一个拿着只读令牌的"读 issue 服务"，放在 AWF 容器外面；agent 的 `read_issue_ref` 把编号交给它，它当场向 GitHub 查、还原打标签那一刻的版本、查最后修改人、清洗，再把正文或读不到的原因交回。开工前不再预抓引用，也不再有"多于一层就不开工"。

2026-11-02 定：**不设任何上限**，不限篇数，也不按剩余接口额度留余量（用户的理由：上限不能妨碍运行；各家默认也都不限，gh-aw 的 `max-calls` 要手动开，见第三节）；正文超过 16000 字符时分段读。代码见 `src/repoaegis/issue_service.py` 和 `src/repoaegis/tools/read_issue_ref.py`。

**实测：这条路在 AWF 里走得通，agent 找不到令牌【实测】。** 在测试仓库 `ETOLucy/repoaegis-sandbox` 加一个手动触发的工作流（权限 `contents`、`issues`、`pull-requests` 只读）：运行机上用 Python 起一个监听 18080 端口的小服务，令牌只放进它的环境变量；AWF v0.29.0 用 `--enable-host-access --allow-domains host.docker.internal --allow-host-ports 18080` 启动（AWF 文档 `docs/usage.md` "Host Access" 一节的写法），容器里跑探测脚本。运行 41403586035、41403796640，测完已删掉测试工作流。

- 容器里经 AWF 的代理访问 `http://host.docker.internal:18080/issue/3`、`/issue/7`，服务分别答 `Issue`"parse_date 遇到空字符串时崩溃"和 `PullRequest`"修复 parse_date 遇到空字符串崩溃"。
- 容器里直接访问 `api.github.com`：连不上（curl 退出码 60，证书校验失败，和 [AWF沙箱实测](AWF沙箱实测.md) 记录的一样）。agent 只能托服务读，不能自己去 GitHub。
- 容器里访问运行机上没放行的端口 22：AWF 的代理回 403。只放行了 18080 这一个端口。
- 容器里只看得到自己的 4 个进程，看不到运行机上的服务进程；运行机的 `_temp` 目录（服务脚本放在那里）在容器里不存在。
- 在容器里能看到的所有进程的环境变量和命令行、自己的环境变量、`/home/runner`、`/tmp`、工作区里，找所有像 GitHub 令牌的字符串（`gh[pousr]_` 开头），逐个算哈希和真令牌的哈希比：**一个都没找到**。第二次运行先往工作区放了一个假令牌作对照，扫描找到了它和它所在的文件，说明扫描本身有效。

**实测：现在的代码在 AWF 里整条走得通【实测】。** 2026-11-02 运行 41463869981（测试仓库的临时分支，测完已删）。启动服务那一步原样照抄 `agent-fix.yml`，AWF 的参数也一样，只把容器里跑的程序换成测试脚本。脚本在容器里调真的 `read_issue_ref`，假定正在处理 #6、打标签时间 2026-10-30T00:00:00Z：

- #3 给出正文；再读一次、从 `offset=100` 读、`offset=999` 读，服务日志都记 `cached: true`，只问了 GitHub 一次。`offset=100` 那段末尾写明到头了，`offset=999` 报 `bad_offset`。
- #4（截图）、#5（网上图片）给出正文，图片都换成了 `[image omitted]`。
- #7、#2 是 PR，#10 建于打标签之后，#9999 不存在，2147483648 超出编号范围：都按原因回答，原因和预期一致。
- #6 是正在处理的那篇，参数 `"3"` 是字符串：都在工具里就回答了，服务日志里没有这两条。
- 绕开工具直接访问服务，`/repos/…/issues/3`、`/issue/3/comments` 都回 404。
- 容器里没有 `GITHUB_TOKEN`。AWF 设的 `HTTP_PROXY` 是 `http://172.30.0.10:3128`；`NO_PROXY` 里没有 `host.docker.internal`，所以这一版 AWF 下 urllib 其实也会走代理。工具仍然自己按代理的规矩发请求，免得以后版本改了 `NO_PROXY` 就悄悄绕开代理。

还没测的：修改人没有写权限、打标签前改过正文或标题、正文超过 16000 字符要分段，这三种在测试仓库里造不出来（只有一个账号，现有 issue 都没改过、正文都很短），只有单元测试；`--enable-host-access` 和以后要加的网络白名单、模型密钥代理放在一起是否冲突；运行机上同一端口有没有别的服务（AWF 文档提醒放行的端口上任何服务都能被访问到）。

## 六、尚未查清

1. 不设上限后，一次运行实际用多少次接口、会不会碰到仓库共用的额度（`GITHUB_TOKEN` 每仓库每小时 1000 次，待核对 GitHub 文档），要等真实运行后看。
2. Triage 角色能不能改别人写的 issue 正文：之前的调研是按"能"来定规则的，这次子 agent 说没查 GitHub 文档，待再确认。
3. Copilot 对 agent 运行中读到的 issue 有没有过滤隐藏字符。
4. RoguePilot、PromptPwnd、Trail of Bits 三篇原文没打开。
5. gh-aw 网关里 `allowed-repos` 范围之外的内容是被滤掉还是整条拒绝，没跑过。
