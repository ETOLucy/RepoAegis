# GitHub Actions 平台事实调研：检查点、重来、定时对账

> 本报告 2026-10-24 写在调研会话的笔记目录（原文件名 `research-actions-platform.md`），2026-10-25 原样搬进 `docs/调研/`，正文没有再复核，行号和版本以当时为准。调研时还在考虑"失败后接着做"，后来范围定为每个 issue 只处理一次，检查点不再需要；仍然用上的是第 4、5、6 节的事实：收尾放进单独的 `always()` 作业、定时兜底工作流以接口查到的实时状态为准（见 [暂停存档与监控](../暂停存档与监控.md)"收尾和留言"和 [触发与叫停](../触发与叫停.md)）。

调研日期：2026-10-24。

## 读了哪些材料、怎么标出处

- **GitHub 官方文档源文件**：浅克隆 `github/docs` 仓库（提交 `02c25d1`，2026-10-23）。下文写"docs: 路径 Lxx"，指这个仓库里的文件和大致行号。文件里出现的 `{% data reusables.xxx %}` 是"把另一段公共文字插到这里"的写法，被插入的文字在 `data/reusables/xxx.md`，下文会直接写出这个文件的路径。
- **REST 接口说明**：同一仓库里的 `src/rest/data/fpt-2022-11-28/*.json`，是网页版 REST 文档的数据来源，里面写着每个接口需要什么权限、是否允许匿名读（字段名 `allowsPublicRead`）。
- **三个官方 action 的源码**：`actions/upload-artifact`（v7.0.2，提交 `cf430e0`）、`actions/download-artifact`（v8.0.2，提交 `9000827`）、`actions/cache`（v6.1.0，提交 `3edfce9`）。
- **运行机程序源码**：`actions/runner` 仓库的 `src/Runner.Worker/JobRunner.cs` 和 `StepsRunner.cs`，通过 `gh api` 读的是当天主分支版本。
- **实测**：用 `gh api`（已登录、对目标仓库没有写权限）和不带令牌的 `curl` 调了公开仓库的接口。只读，没有改动任何仓库。

标注约定：**[文档]** 指官方文档原文；**[源码]** 指官方 action 或运行机源码；**[实测]** 指本次亲手调接口看到的结果；**[推断]** 指由上述材料推出来的、官方没有明说；**[待验证]** 指没找到一手来源。

先说明两个术语：
- **运行（workflow run）**：一个工作流被触发一次就是一次运行，有自己的编号 `run_id`。一次运行里可以有多个 **作业（job）**，每个作业分到一台单独的机器。
- **运行机（runner）**：执行作业的机器。用 GitHub 提供的机器时，作业结束后机器就销毁。

---

## 1. Actions 产物（artifact）

**产物**是作业运行时上传、存放在 GitHub 上的一组文件，运行结束后还能下载。

**保留期限**
- 默认保留 90 天，到期后自动删除。[文档] `data/reusables/actions/about-artifact-log-retention.md` L1
- 保留期可以在仓库设置里调整：公开仓库可设 1–90 天，私有仓库可设 1–400 天。改设置只影响之后新建的产物，已有的不变。[文档] 同文件 L13-21
- 单个产物可以用 `retention-days` 单独设置更短的期限，但不能超过仓库设置的上限。[文档] `content/actions/tutorials/store-and-share-data.md` L93-106。upload-artifact 的 README 写的是"最长 90 天，除非在仓库设置里改过"，README L88-92；不过 README L360 又写"必须在 1 到 90 之间"，和官方文档"私有仓库最长 400 天"对不上，应以官方文档为准，**[待验证]** 私有仓库里设 `retention-days: 400` 能否生效。
- 删除一次运行时，这次运行的产物会一起删掉。[文档] `data/reusables/actions/artifacts/artifacts-from-deleted-workflow-runs.md`
- 用 REST 接口查产物，能看到 `expires_at`（到期时间）和 `expired`（是否已过期）。[文档] `content/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts.md` L43-45；[实测] 列表返回里确实有这两个字段。

**大小和数量**
- 官方文档没有写单个产物的大小上限，**未找到**。
- 一个作业最多创建 500 个产物。[源码/README] upload-artifact README "Limitations › Number of Artifacts"，约 L470
- 产物存储空间的免费额度：免费个人账号 500 MB，Pro 1 GB，免费组织 500 MB，Team 2 GB，企业云 50 GB。[文档] `data/reusables/billing/actions-included-quotas.md` L1-7
- 这份额度**与 GitHub Packages（包仓库）共用**，和缓存额度分开计算。[文档] `content/billing/concepts/product-billing/github-actions.md` L50-51、L134
- 存储费用按小时累计，按 GB·小时计费；删掉产物只能让以后不再计费，本月已经累计的不会退。用量 6–12 小时更新一次。[文档] 同文件 L43-74、L193
- 公开仓库用标准运行机时，Actions 的用量免费。[文档] 同文件 L24。**[推断]** 公开仓库的产物存储也不占这 500 MB 额度：原文说的是"usage is free"，没有单独提产物存储。
- 超出存储额度后，可能无法再创建产物。[源码/README] upload-artifact README "Limitations"

**谁能下载**
- 文档说法："登录了 GitHub、且对仓库有读权限的人"可以下载产物。[文档] `content/actions/how-tos/manage-workflow-runs/download-workflow-artifacts.md` L4
- 公开仓库的实际情况：
  - 列出产物不需要登录。[实测] 不带令牌调 `GET /repos/actions/upload-artifact/actions/artifacts` 返回 200，并带出产物名和 `workflow_run.id`。接口数据也写着 `allowsPublicRead: true`。
  - 下载需要登录。[实测] 不带令牌下载 `.../artifacts/{id}/zip` 返回 401 "Requires authentication"；带一个对该仓库没有任何权限的普通账号令牌，返回 200。也就是说，**公开仓库里任何登录用户都能下载产物**。
  - upload-artifact 输出的 `artifact-url` 也要求登录才能用。[源码/README] upload-artifact README "Outputs" 表
- 私有仓库：需要读权限。令牌要有 Actions 读权限；如果是经典个人令牌（classic PAT），要有 `repo` 范围。[文档] REST 数据 `actions.json` 中 "Download an artifact" 的说明和 `progAccess`

**能否从另一次运行下载**
- 可以。download-artifact 默认只能下载本次运行的产物；要下载别的运行或别的仓库的产物，需要同时提供 `github-token`、`run-id` 和 `repository`。[源码/README] download-artifact README L80-93、"Download Artifacts from other Workflow Runs or Repositories" L287-299。README 示例的注释写的是令牌要有目标仓库的 `actions:read` 权限。
- 源码里，只要传了 token，就会改用公开 REST 接口，按 `workflowRunId` 去查。[源码] `download-artifact/src/download-artifact.ts` L66-79
- **[推断]** 在同一个仓库里，用 `GITHUB_TOKEN` 并在工作流里声明 `permissions: actions: read` 就够了，不需要个人令牌。依据是 REST 接口只要求"Actions 读权限"。
- 注意 download-artifact 要求**知道 run-id**。要按名字找到最新的检查点，得先调 `GET /repos/{owner}/{repo}/actions/artifacts?name=XXX`，从结果的 `workflow_run.id` 拿到运行编号。

**能否覆盖同名产物**
- v4 以后，产物一旦创建就**不能修改**，同一次运行里重名会报错。[源码/README] upload-artifact README "(Not) Uploading to the same artifact" L253-299
- 设置 `overwrite: true` 后，会先删掉同名的旧产物再上传新的；新产物的 ID 也是新的。[源码/README] README "Overwriting an Artifact" 约 L397
- **只在本次运行范围内生效**。[源码] 删除时用的是当前作业令牌里带的运行编号（`workflowRunBackendId`），只在这个范围里按名字找（`upload-artifact/dist/upload/index.js` 中的 `deleteArtifactInternal`，约 L129106-129135）。**[推断]** 所以它覆盖不了**另一次运行**里的同名产物。跨运行时，同名产物会并存，按名字查会查到多条。

**按名字列出整个仓库的产物**
- 可以：`GET /repos/{owner}/{repo}/actions/artifacts?name=...`。说明原文为"only artifacts with this name will be returned"，最多每页 100 条，需要 Actions 读权限，公开仓库可匿名读。[文档] REST 数据 "List artifacts for a repository"；[实测] 返回字段有 `id, name, size_in_bytes, expired, expires_at, created_at, digest, workflow_run{id, head_branch, head_sha}`。
- 删除产物要 Actions 写权限。[文档] REST 数据 "Delete an artifact"

---

## 2. Actions 缓存（cache）

**缓存**是按"键"（一个字符串）存放的一包文件，原本用来让后面的运行跳过重复下载依赖。

- **容量**：每个仓库默认 10 GB，超出后从最久没用过的开始删。[文档] `content/actions/reference/workflows-and-actions/dependency-caching.md` L356，以及 `data/reusables/actions/cache-eviction-policy.md`。仓库管理员可以调大（个人仓库最多 10 TB），但前提是账号绑定了付款方式并主动开启，超出 10 GB 的部分要付费。[文档] 同文件 L365-376；`managing-github-actions-settings-for-a-repository.md` L189-202
- **单个缓存条目**：超过 10 GB 时不保存。[源码] `cache/dist/save/index.js` 约 L94694-94699，报错原文为 "is over the 10GB limit, not saving cache"
- **多久不用会被删**：超过 7 天没被访问就删除。[文档] dependency-caching.md L356。付费用户可以把这个期限改长：公开仓库最多 90 天，私有仓库最多 365 天。[文档] `managing-github-actions-settings-for-a-repository.md` L200
- **按分支隔离的规则**：[文档] dependency-caching.md "Restrictions for accessing a cache" L240-249
  - 一次运行能读到两处缓存：当前分支的，和默认分支的。由拉取请求（PR）触发的运行，还能读到 PR 目标分支（base 分支）的缓存。
  - 读不到子分支、兄弟分支、其他标签的缓存。
  - PR 触发的运行写入的缓存，归属于 `refs/pull/N/merge`，只有这个 PR 的重跑能读到。
  - 查找顺序：先在当前分支找完全匹配的键，再找前缀匹配，然后找 `restore-keys`；当前分支都没有，再到默认分支按同样顺序找。L170
- **issues 事件触发的运行属于哪个分支**：`GITHUB_REF` 是默认分支，`GITHUB_SHA` 是默认分支的最后一次提交。[文档] `content/actions/reference/workflows-and-actions/events-that-trigger-workflows.md` "issues" L320-327
- **关键限制：issues 触发的运行默认只能读缓存，不能写**。[文档] dependency-caching.md "Cache access for low-trust workflow triggers" L264-281
  - 为了防止"缓存投毒"（不受信的人写进恶意缓存，之后被有权限的工作流读出来执行），只有以下事件可以在默认分支范围里创建或覆盖缓存：`push`、`workflow_dispatch`、`repository_dispatch`、`delete`、`registry_package`、`page_build`、`schedule`。
  - 其他事件，凡是落在默认分支上的，都只有只读权限。原文点名了 `issue_comment`、`pull_request_target`、`workflow_run`。**`issues` 不在可写名单里，所以是只读。**
  - 只读运行尝试保存缓存时，这一步不会失败，只在日志里给一条警告，缓存实际上没存。L281
  - 可以在工作流或作业上声明 `cache-mode: write` 来解除这条限制，但文档警告这样会重新引入投毒风险。[文档] dependency-caching.md L287-338；`workflow-syntax.md` "cache-mode" L409-427。这个功能在 github.com 上可用（`data/features/actions-cache-mode.yml`：fpt、ghec）。
- **能否覆盖同一个键**：不能。原文："A cache today is immutable and cannot be updated"。想更新，只能每次换一个新键，再用 `restore-keys` 按前缀取最新的。[源码/README] `cache/tips-and-workarounds.md` L9-11。如果另一个作业正在用同一个键保存，会报 "another job may be creating this cache"。[源码] dist/save/index.js 约 L94724。可以用 REST 接口按键删除缓存，需要 Actions 写权限。[文档] REST 数据 "Delete GitHub Actions caches for a repository (using a cache key)"
- **谁能读**：
  - 缓存的**内容**只能在工作流运行里通过缓存服务读取；REST 接口只能列出键、大小、时间这些元数据，也只能删除。[文档] REST 数据 `cache` 分类；[实测] 匿名调 `GET /repos/nodejs/node/actions/caches` 返回 200。
  - 文档明确说："任何对仓库有读权限的人都可以开一个 PR，并读到缓存内容；复刻仓库（fork）也能对目标分支开 PR，读到目标分支的缓存。"所以**不要往缓存里放敏感信息**。[文档] dependency-caching.md L69、L344
  - **[推断]** 公开仓库里，等于任何人都能读到默认分支范围的缓存。
- 频率限制：每个仓库每分钟最多 200 次上传、1500 次下载、400 次删除。[文档] `content/actions/reference/limits.md` L52-54

---

## 3. 把检查点存成 git 分支

- `GITHUB_TOKEN` 是 GitHub 在每个作业开始前发的一个临时令牌，身份是安装在仓库上的"GitHub Actions"应用；作业结束时失效，最长 24 小时。[文档] `content/actions/concepts/security/github_token.md` L17-19；`data/reusables/actions/github-token-expiration.md`
- **推送分支需要 `contents: write`**。文档原文举的例子是"contents: write 可以创建 release"；`contents` 这一项管的是仓库内容。[文档] `data/reusables/actions/github-token-scope-descriptions.md` "contents" 一行。**[推断]** git 推送也归它管；GitHub 应用权限文档说"用安装令牌通过 HTTP 访问 git，要申请 Contents 权限"（`content/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app.md` L92）。
- 个人账号下新建的仓库，`GITHUB_TOKEN` 默认只有 `contents` 和 `packages` 的读权限，要在工作流的 `permissions:` 里显式申请写权限。[文档] `managing-github-actions-settings-for-a-repository.md` L100
- **`GITHUB_TOKEN` 不能修改 `.github/workflows/` 下的文件**。[推断] 可以给 `GITHUB_TOKEN` 设的权限清单里没有 "workflows" 这一项（`data/reusables/actions/github-token-available-permissions.md`），而 GitHub 应用要改这个目录必须有 "Workflows" 权限（上面那个应用权限文档 L92）。所以 agent 改动的内容里如果有工作流文件，推送会被拒绝，**[待验证]** 实际的报错。
- **开 PR**：个人账号下新建的仓库，默认不允许工作流创建或批准 PR，要在仓库 Settings → Actions → General → Workflow permissions 里勾选 "Allow GitHub Actions to create and approve pull requests"。[文档] `managing-github-actions-settings-for-a-repository.md` L108-118
- **推送不会触发其他工作流**：用 `GITHUB_TOKEN` 产生的事件不会新建运行，以免无限循环。例外有两个：
  - `workflow_dispatch` 和 `repository_dispatch` 总会新建运行。
  - `GITHUB_TOKEN` 创建或更新 PR 时，产生的 `pull_request`（opened/synchronize/reopened）事件会新建运行，但这些运行处于"等待批准"状态，要有写权限的人点 "Approve workflows to run" 才会跑。
  
  [文档] `data/reusables/actions/actions-do-not-trigger-workflows.md`；特性开关 `data/features/actions-github-token-pull-request-approval.yml`（fpt、ghec，注释写着"currently rolling out on dotcom"，即仍在逐步上线）。如果想让 PR 上的 CI 自动跑，要改用 GitHub 应用令牌或个人令牌。[文档] `content/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow.md` L28；`github_token.md` L34
- **分支对所有能读仓库的人可见**：分支是仓库内容的一部分，公开仓库任何人（不用登录）都能克隆、看到。[推断] 属于常识，没有专门的一句原文。
- **删分支也删不干净**：提交一旦被推上去，即使删了分支，GitHub 上"按 SHA 访问的缓存页面"以及别人的克隆、复刻里都可能还在，要彻底清除只能找 GitHub 客服。[文档] `content/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository.md` L50-62
- 建议上限：单个仓库磁盘占用 10 GB、分支数 5000、一次推送 2 GB（强制）、单个文件 100 MB（强制，建议 1 MB 以内）。[文档] `content/repositories/creating-and-managing-repositories/repository-limits.md` L22-48
- 分支没有自动过期机制。[推断] 文档里没有任何关于分支过期的规定。

---

## 4. 重跑（re-run）

- **可以重跑什么**：整个运行、只重跑失败的作业，或者重跑某一个作业。重跑某个作业时，依赖它的后续作业也会一起重跑。[文档] `content/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs.md`；REST 数据中 "Re-run failed jobs" 的说明为 "Re-run all of the failed jobs and their dependent jobs"
- **重跑用的是原来的事件内容还是新的**：
  - 文档原文：重跑使用原触发事件的 `GITHUB_SHA` 和 `GITHUB_REF`，并且沿用**最初触发者**（`github.actor`）的权限，而不是点重跑的人的权限。[文档] re-run-workflows-and-jobs.md L20；`contexts.md` L181（`github.actor`、`github.triggering_actor`）
  - 事件内容（`github.event`）是否也是原来那份：文档**没有明说**。[推断] 是原来那份，理由有两条：一是 SHA 和 REF 都沿用原值；二是 WordPress 的工作流在重跑时还在读原来的 `inputs` 和 `github.run_attempt`（见第 5 节）。对我们的影响是：**重跑时 `github.event.issue.labels` 是触发那一刻的快照，不是现在的状态**。想知道标签现在还在不在，必须调接口查。
  - **[推断]** 工作流文件用的也是原 SHA 那一版，所以重跑之前改过的工作流不会生效。
- **`github.run_attempt` 是什么**：同一次运行的第几次尝试，第一次是 1，每重跑一次加 1，以字符串形式给出。[文档] `contexts.md` L209；`variables.md` L62（`GITHUB_RUN_ATTEMPT`）。`run_id` 在重跑前后不变。[推断] 文档只说 attempt 递增；接口 `GET .../runs/{run_id}/attempts/{attempt_number}/jobs` 需要用同一个 `run_id` 才能查各次尝试，佐证了这一点。
- **期限**：最初运行之后 **30 天内**可以重跑。[文档] re-run-workflows-and-jobs.md L4（intro）。所以 90 天之后肯定不行，30 天之后也不行。
- 另外，从 2026-10-01 起，保留期设置也作用于运行记录本身，到期后运行会被删除，以前是不管设置一律保留 400 天以上。[文档] `data/reusables/actions/about-artifact-log-retention.md` L5-6
- **次数上限**：一次运行最多重跑 50 次，全量重跑和部分重跑都算在内。[文档] `content/actions/reference/limits.md` L32；re-run 文档 L22
- 工作流被停用时不能重跑。[文档] re-run 文档 L24
- **权限**：页面上手动重跑需要仓库写权限。[文档] re-run 文档 L5。**[推断]** 重跑时 `GITHUB_TOKEN` 会按作业重新签发，权限仍然按工作流文件里的 `permissions:` 来；文档只说"沿用最初触发者的权限"。
- **能否用接口重跑**：可以。
  - `POST /repos/{o}/{r}/actions/runs/{run_id}/rerun-failed-jobs`
  - `POST .../runs/{run_id}/rerun`
  - `POST .../actions/jobs/{job_id}/rerun`
  
  这三个都要 **Actions 写权限**。[文档] REST 数据 `actions.json` "workflow-runs" 分类。`GITHUB_TOKEN` 可以申请 `actions: write`，文档原文举的例子是"可以取消运行"。[文档] github-token-scope-descriptions.md "actions" 一行
  - 实际例子：WordPress 的 `phpunit-tests.yml` 在失败时用 `GITHUB_TOKEN`（`actions: write`）触发 `failed-workflow.yml`（一个 `workflow_dispatch` 工作流），由后者调 `reRunWorkflowFailedJobs` 重跑原运行，并用 `run_attempt > 1` 时跳过的方式保证只自动重跑一次。[实测/源码] `WordPress/wordpress-develop` 仓库 `.github/workflows/phpunit-tests.yml` L627-658、`failed-workflow.yml` L19-59。（这是第三方项目，不是 GitHub 官方。）
  - **[推断]** 运行还没结束时不能重跑，所以 WordPress 让另一个运行去发重跑请求，并给接口调用设置了 15 次重试。
- **重跑是否还受并发组约束**：**未找到**官方说明。并发组（concurrency group）是指同一个组名下同一时间只允许一个运行或作业在跑。[推断] 重跑会按工作流里的 `concurrency` 重新排队，但没有原文。

---

## 5. 作业失败的种类

- **运行机丢失或关机**：
  - 运行机程序收到关机信号时，会在日志里写 "The runner has received a shutdown signal..."，把**作业结果设为 Failed**（失败，不是取消），并且**不再评估后续步骤的 `if` 条件**，直接跳过。也就是说，**同一个作业里写了 `if: always()` 的步骤也不会执行**。[源码] `actions/runner` 的 `src/Runner.Worker/JobRunner.cs` L125-143；`StepsRunner.cs` L149-207，原注释为 "Skip Re-evaluate condition on runner shutdown"
  - "lost communication with the server"（机器直接消失、程序来不及上报）是服务器端判定的，运行机源码里搜不到这句话。这种情况下作业状态是什么：**[待验证]**，社区普遍说法是 failure，没找到官方原文。
  - **[推断]** 因为作业结果是 failure，**另一个作业**如果写了 `needs: 那个作业` 加 `if: failure()` 或 `if: always()`，会被分到另一台新机器上执行。`failure()` 的定义是"依赖链上任何一个祖先作业失败就返回 true"。[文档] `content/actions/reference/workflows-and-actions/expressions.md` L362-364。所以**收尾和汇报要放在一个单独的作业里，不能只放在同一作业的 `always()` 步骤里**。
- **`timeout-minutes` 到时**：
  - 文档原文是"GitHub 会自动**取消**这个作业"（"automatically cancels it"），默认 360 分钟。[文档] `workflow-syntax.md` L1228-1232
  - REST 接口里作业的 `conclusion` 可以取的值包括 `success, failure, neutral, cancelled, skipped, timed_out, action_required`。[文档] REST 数据 actions.json 约 L39526-39538
  - 第三方说法（WordPress core 的提交说明，经搜索引擎摘要转述，原页面返回 403，没能直接打开）：在工作流的表达式里，超时看起来和 cancelled 一样，`cancelled()` 为真；REST 接口则会给出 `timed_out`。**[待验证]**，建议在沙盒仓库里实测一次。
  - 取消的流程：先对还在跑的作业重新评估 `if`，带 `always()` 的作业继续跑；然后向步骤进程发 SIGINT，7.5 秒后发 SIGTERM，再过 2.5 秒强制结束；5 分钟后服务器强制终止所有还没停的。[文档] `content/actions/reference/workflows-and-actions/workflow-cancellation.md` L18-22。**[推断]** 超时也走这套流程，所以同一作业里 `if: always()` 的收尾步骤**有机会**执行（这一点和运行机丢失不同）。
  - 步骤级的 `timeout-minutes` 最多 360，必须是整数。[文档] workflow-syntax.md L1070-1074
  - GitHub 提供的运行机上，单个作业最长 6 小时，到时"终止并判为失败"。[文档] limits.md L45。（文档对"超时"一处写取消、一处写失败，这里照录原文。）
- **`continue-on-error`**：
  - 步骤级：这一步失败了，作业照样算成功；`steps.X.outcome` 是 failure，`steps.X.conclusion` 是 success。[文档] workflow-syntax.md L1066-1068；contexts.md L540-541
  - 作业级：这个作业失败了，整个运行照样算通过。[文档] workflow-syntax.md L1348-1352
- **有没有官方的"作业自动重试"**：**没有找到**。工作流语法里没有 retry 关键字，[文档] 在 workflow-syntax.md 中搜 retry 没有结果；官方文档全文搜 "automatically re-run" 等说法也没有命中。社区和第三方做法都是"失败后用接口重跑"（见第 4 节 WordPress 的例子）。结论：**没有，已核实（就官方文档范围而言）**。

---

## 6. 定时任务（schedule）

- **最短间隔**为每 5 分钟一次；默认按 UTC 时间；**现在也支持用 `timezone` 指定时区**（IANA 时区名，github.com 已上线）；只在默认分支的最新提交上运行。[文档] `data/reusables/repositories/actions-scheduled-workflow-example.md` L1-25；`data/features/actions-schedule-timezone.yml`
- **高峰延迟或丢弃**：负载高时（每小时整点尤其明显）会延迟；负载足够高时，排队中的任务**可能被丢弃**；建议避开整点。[文档] `data/reusables/actions/schedule-delay.md`
- **只在默认分支运行**，并且工作流文件必须存在于默认分支。[文档] events-that-trigger-workflows.md L963-966；`data/reusables/actions/branch-requirement.md`
- **公开仓库 60 天没有活动会自动停用定时工作流**，要手动重新启用。[文档] events-that-trigger-workflows.md L967。另外，有写权限的人提交修改 cron 表达式后，停用的定时工作流会重新激活。L992
- 定时任务的通知发给最后一个改 cron 表达式的人。[文档] L994
- **一次运行能否触发另一次运行**：
  - 用 `GITHUB_TOKEN` 触发的事件一般不会新建运行，**但 `workflow_dispatch` 和 `repository_dispatch` 例外，总会新建运行**。[文档] `data/reusables/actions/actions-do-not-trigger-workflows.md` L1-3
  - 调 `workflow_dispatch` 接口需要 **Actions 写权限**（`actions: write`）。[文档] REST 数据 "Create a workflow dispatch event"
  - 调 `repository_dispatch` 接口需要 **Contents 写权限**（`contents: write`）。[文档] REST 数据 `repos.json` "Create a repository dispatch event"
  - `repository_dispatch` 的 `client_payload` 最多 10 个顶层字段、65535 个字符。[文档] events-that-trigger-workflows.md L948-950
  - 两者都要求工作流文件在默认分支上。`workflow_dispatch` 的界面按钮要求文件在默认分支；运行过一次之后，可以通过接口对任意分支触发。[文档] L1070-1081
- `schedule`、`workflow_dispatch`、`repository_dispatch` 都在**可写缓存**的名单里（见第 2 节）。

---

## 7. 计费

- **私有仓库免费额度**：免费个人账号每月 2000 分钟，与之前核实的一致。[文档] `data/reusables/billing/actions-included-quotas.md` L3
- 分钟按月重置，算在**仓库所有者**头上，不算在触发者头上。[文档] `content/billing/concepts/product-billing/github-actions.md` L28-32
- **"按作业向上取整到整分钟"有原文**，两处：
  - `content/billing/reference/actions-runner-pricing.md` L14："GitHub rounds the minutes and partial minutes each job uses up to the nearest whole minute."
  - `content/actions/how-tos/monitor-workflows/view-job-execution-time.md` L21："Billable job execution minutes ... are rounded up to the next minute."
  - 两句都是按"每个作业"说的，所以**一个工作流拆成多个作业时，每个作业分别向上取整**。例如 3 个各跑 10 秒的作业，按 3 分钟计。[推断] 这个算例是按原文措辞推出来的，句子本身是原文。
- **公开仓库**：用标准运行机免费，另外 GitHub Pages、Dependabot 也免费；更大规格的运行机（larger runners）任何情况下都收费。[文档] github-actions.md L24、L125-133
- 失败的运行也计费，重跑会再计一次。例如跑 5 分钟失败，修好后重跑 10 分钟成功，一共 15 分钟。[文档] 同文件 L115
- **被跳过的作业（`if` 为假）是否计费**：**未找到**原文。[推断] 不计费，因为被跳过的作业根本不分配机器，也就没有执行时间。另外文档说"被跳过的作业状态报告为 Success"。[文档] `content/actions/how-tos/write-workflows/choose-when-workflows-run/control-jobs-with-conditions.md` L44
- 日志和作业摘要不占产物存储额度。[文档] github-actions.md L116
- 没有绑定付款方式时，额度用完就会被阻止继续使用。[文档] 同文件 L139
- 标准 Linux 2 核超出额度后的价格：表中 `linux_2_core_advanced` 为 $0.006/分钟，标准价格表在 `data/reusables/billing/actions-standard-runner-prices.md`（未展开逐行核对）。
- **注意：沙盒仓库 `ETOLucy/repoaegis-sandbox` 是私有仓库**。[实测] `gh api repos/ETOLucy/repoaegis-sandbox` 返回 `private`。所以在那里的每次试验都会消耗 2000 分钟额度，每个作业至少算 1 分钟。

---

## 8. issue 的事件记录

- **两个接口**：
  - `GET /repos/{o}/{r}/issues/{n}/events`（issue 事件）
  - `GET /repos/{o}/{r}/issues/{n}/timeline`（时间线，比前者多了评论、交叉引用等）
  
  都需要 Issues 读权限，公开仓库可以匿名读，每页最多 100 条，用 `page` 翻页；时间线接口还可以用 `exclude` 参数排除某些事件类型。[文档] REST 数据 `issues.json` "List issue events"、"List timeline events for an issue"
- `labeled` 和 `unlabeled` 两个接口里都有。[文档] `content/rest/using-the-rest-api/issue-event-types.md` "labeled" L397-415
- **字段**：`id, node_id, url, actor, event, commit_id, commit_url, created_at, performed_via_github_app, label{name, color}`，有时还有 `intent`（agent 操作的意图说明，仅在开启 issue 建议功能时出现）。其中 `performed_via_github_app` 在接口定义里是必有字段，值可能为 null。[文档] issues.json 中 "Labeled Issue Event" 和 "Unlabeled Issue Event" 的 schema；`data/reusables/issue-events/issue-event-common-properties.md`、`label-properties.md`
- **用 `GITHUB_TOKEN` 撤标签时，事件里显示的是谁**：[实测]
  - `nodejs/node` 的 `auto-start-ci.yml` 用 `GH_TOKEN: ${{ github.token }}` 撤掉 `request-ci` 标签。
  - 在 `nodejs/node` 的 #66967 上（2026-10-24T12:53:36Z），`/events` 和 `/timeline` 两个接口都显示：`actor.login = "github-actions[bot]"`、`actor.type = "Bot"`、`actor.id = 41898282`、**`performed_via_github_app = null`**。
  - 同一个 issue 上，人加标签时显示 `actor.login = "pimterry"`、`type = "User"`。
- **能否据此区分"人撤的"还是"Actions 撤的"**：**可以**，看 `actor.login == "github-actions[bot]"`，更稳妥的做法是看 `actor.id == 41898282` 并且 `actor.type == "Bot"`。`performed_via_github_app` 对 `GITHUB_TOKEN` 是 null，**不能**靠它来区分。
  - **[推断]** 如果以后改用自己的 GitHub 应用令牌，actor 会变成 `你的应用名[bot]`。
  - 如果用个人令牌，actor 会显示为那个人，就**无法和人工操作区分**了。
  - **[推断]** 同一仓库里任何用 `GITHUB_TOKEN` 的工作流撤标签，actor 都一样是 github-actions[bot]，所以光看 actor 分不出是哪一个工作流撤的。
- **保留期限**：issue 事件和时间线的保留期限，官方文档**未找到**。（对比：账号或仓库"活动事件"接口 `/events` 只保留 30 天、最多 300 条，`content/rest/activity/events.md` L38；那是另一个接口，不要混淆。）[实测] 沙盒仓库的标签事件今天都能查到；nodejs 的旧事件也能查到，说明至少不是 30 天就删。具体期限 **[待验证]**。

---

## 9. 日志保留

- 工作流运行日志和产物一样，默认保留 90 天；公开仓库可设 1–90 天，私有仓库可设 1–400 天，在仓库 Settings → Actions → General 里设置，改了只影响之后的新运行。[文档] `data/reusables/actions/about-artifact-log-retention.md`；`managing-github-actions-settings-for-a-repository.md` L208-223。从 2026-10-01 起，运行记录、检查结果（checks）、提交状态（commit statuses）也按这个设置删除。[文档] 同 reusable L5-6
- 删除一次运行会连带删除它的日志；也可以单独删除日志，要 Actions 写权限。[文档] REST 数据 "Delete workflow run logs"
- **谁能看**：
  - 文档原文：要看运行信息必须登录 GitHub，**公开仓库也一样**；还需要读权限。[文档] `content/actions/how-tos/monitor-workflows/use-workflow-run-logs.md` L22、L29
  - [实测] 匿名调 `GET .../runs/{id}/logs` 返回 403（"Must have admin rights to Repository."）；用一个与该公开仓库无关的已登录账号调用，返回 200。也就是说，**公开仓库的日志对任何登录用户可见**。
  - 私有仓库要读权限。
- 下载日志的链接 1 分钟内有效。[文档] REST 数据 "Download workflow run logs"
- **[推断]** 日志里会自动遮掉 secrets 的值，但 agent 打印出来的其他内容，例如代码片段、issue 内容、模型输出，不会被遮掉。对于公开仓库，这些就是公开内容。

---

## 10. 对比：检查点放在哪里

候选的五个地方：
- **git 分支**：每个 issue 一条工作分支，把检查点文件提交上去。
- **产物（artifact）**
- **缓存（cache）**
- **仓库文件**：往默认分支提交一个状态文件。
- **外部存储**：例如对象存储或数据库，需要自己保管访问凭证（credentials）。

| | git 分支 | 产物 | 缓存 | 默认分支上的仓库文件 | 外部存储 |
|---|---|---|---|---|---|
| 保留期限 | 不过期，删分支后提交仍可能残留 | 默认 90 天；公开仓库最长 90 天，私有仓库最长 400 天；运行被删时一起删 | 7 天没被访问就删（付费可延长）；超出总容量时从最旧的删 | 不过期 | 由自己决定 |
| 大小 | 单文件 100 MB 强制上限，单次推送 2 GB，仓库建议 10 GB 以内 | 单个产物上限未找到；私有仓库占 500 MB 共享额度（含 Packages）；公开仓库免费 | 单条 10 GB，整个仓库默认 10 GB | 同 git 分支 | 由自己决定 |
| 可见性 | 所有能读仓库的人；公开仓库对所有人公开，不用登录 | 公开仓库：任何人可以匿名列出名字，任何登录用户可以下载；私有仓库：有读权限的人 | 内容只能在工作流里读；公开仓库里任何人开 PR 就能读到默认分支的缓存 | 同 git 分支，并且出现在主线历史里 | 只有持有凭证的人 |
| 写入权限 | `contents: write` | 上传不需要额外权限；跨运行下载要 `actions: read` 和 run-id | **issues 触发的运行默认只读**，要声明 `cache-mode: write` 才能写，有投毒风险 | `contents: write`，并且会往默认分支加噪音提交；分支保护可能挡住 | 要在仓库里配一个 secret（加密保存的凭证） |
| 能否覆盖 | 可以，正常提交或强制推送 | 只能在本次运行内覆盖；跨运行时同名并存，要靠时间或 ID 选最新的 | 不能，每次要用新键 | 可以 | 可以 |
| 按 issue 查找 | 分支名里带 issue 号，直接读 | 产物名带 issue 号，用 `?name=` 查 | 键带 issue 号，接口只能列键，要在工作流里还原内容 | 读文件就行 | 由自己设计 |
| 会不会触发其他工作流 | 用 `GITHUB_TOKEN` 推送不会 | 不会 | 不会 | 不会 | 不会 |
| 安全风险 | 检查点内容（agent 中间状态、日志）对所有读者公开且难以彻底删除；agent 改到 `.github/workflows` 时推送会失败 | 公开仓库里任何登录用户都能下载，不要放敏感内容 | 文档明确说不要存敏感信息；不受信的人可能投毒 | 和分支一样，还会污染主线历史 | 多一个需要保管的凭证，项目依赖外部服务 |

最后补几点对设计有直接影响的事实（都在上文有出处）：

1. **缓存基本不适合当检查点**：issues 触发的运行默认不能写缓存，7 天不用就被删，键不能覆盖，内容也只能在工作流里读。
2. **产物适合存"一次运行内部的中间结果"，或者"给人看的过程记录"**：能按名字跨运行查找，过期时间可以设置，用完即删。缺点是公开仓库里任何登录用户都能下载，并且下载时要先拿到 run-id。
3. **git 分支天然就是 PR 的载体**：草稿 PR 本来就要一条分支，代码改动放在那里最自然。但如果把"agent 中间状态"也提交进去，会被公开、留在历史里，并且出现在 PR 的改动列表里。
4. **重跑拿到的是旧的事件快照，30 天后不能重跑；作业所在的机器一旦丢失，同一作业里的 `always()` 步骤也不会执行**。所以"撤标签、在 issue 里汇报"这类收尾要放在一个独立的、写了 `needs` 和 `if: always()` 的作业里。定时对账的工作流要以接口查到的实时状态为准：issue 上现在有没有标签、最后一次撤标签的 actor 是谁、对应运行的 conclusion 是什么。
