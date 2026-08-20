<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/repo-aegis-mark-reversed.svg">
    <img src="docs/assets/repo-aegis-mark.svg" width="112" alt="RepoAegis 标志">
  </picture>
</p>

<h1 align="center">RepoAegis</h1>

<p align="center">
  面向证据化补丁与可审查交付的策略受控仓库维护 Agent 框架。
</p>

<p align="center">
  <a href="https://github.com/ETOLucy/RepoAegis/actions/workflows/ci.yml"><img src="https://github.com/ETOLucy/RepoAegis/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="https://github.com/ETOLucy/RepoAegis/actions/workflows/eval-smoke.yml"><img src="https://github.com/ETOLucy/RepoAegis/actions/workflows/eval-smoke.yml/badge.svg" alt="eval-smoke"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12-245dcc.svg" alt="Python 3.12"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-177245.svg" alt="License: Apache-2.0"></a>
</p>

<p align="center">
  <a href="README-EN.md">English</a>
</p>

---

## 这是什么

**RepoAegis 是一个自动化解决仓库issue的迷你Coding Agent。**

系统面向生产级仓库维护设计：

- **LLM Agent 编排** —— 基于 LangGraph 的多 Agent 流水线（intake → 定位 → 规划 → 打补丁 → 验证 → 审查），确定性路由 + 有界纠错循环。
- **面向代码的混合检索（hybrid retrieval）** —— 多路召回 + 重写 + 融合 + 重排：先对查询做 query rewrite（改写/扩展，提升召回质量），再并行走词法检索（BM25，底层用 ripgrep 扫描）、符号检索（按函数/类/变量名定位）、可选向量检索（OpenSearch 适配器）三路召回，用确定性 reciprocal rank fusion（RRF） 融合成单一排序，最后经 rerank 精排后返回；代码问答接口返回带引用文件路径与行区间的答案。
- **Prompt 工程与结构化输出** —— provider 专属结构化 JSON + 严格本地 Pydantic 校验；模型只提有界精确文本编辑，diff hunk 元数据在本地派生。
- **模型评测与基准** —— 可复现评测 harness（并发、可重放、带发布门禁）+ 使用官方 Docker harness 判定的 **SWE-bench Verified** 评测战役；统计严谨性：配对 **bootstrap CI**、Wilson / Clopper–Pearson 区间、Cohen's h、**Holm 校正**。
- **LLM-as-a-Judge** —— 双范式模型评测：确定性 harness + 基于量表的 LLM 判定；另有 model matrix 用对齐种子在同一套件上跑多个模型，输出成本–质量权衡。
- **安全与护栏（guardrails）** —— **deny-by-default** 工具授权 + 阶段感知权限、远端写入绑定审批信封的人工审批、Docker 沙箱（digest-pinned 镜像 / 非 root / 只读根 / 丢弃 capabilities / `no-new-privileges`）、递归密钥脱敏、执行期红队用例集（prompt injection / 越权工具 / 密钥外泄 / 路径穿越）。

**基准结果（frozen、one-shot）：** 在 **SWE-bench Verified** 分层抽样子集上，74 / 200（37.0%）实例经官方 SWE-bench Docker harness 判定为端到端解决，38.5%（条件于生成成功）；逐实例结果、冻结任务 ID、生成失败原因全部随仓库发布，可审计。

## 为什么存在

仓库维护 Agent 面对的是恶意输入与高影响工具：issue 文本可能携带提示注入、源码树可能包含密钥、测试会执行不可信代码、远端写入可能影响生产仓库。生产使用在 agent 循环之外还有额外要求：

- 不可变任务边界：租户、仓库与 commit
- 默认拒绝的工具，带阶段感知的权限
- 远端写入必须绑定审批信封的人工审批
- 有界资源与网络策略的隔离执行
- 持久化并发控制与可重放副作用
- 能区分正确性、安全、检索与成本的评测

本仓库把这些边界端到端实现出来。

## 已实现的保证

| 边界 | 实现 |
|---|---|
| Agent 状态 | 严格 Pydantic 模型与合法生命周期迁移 |
| 并发 | 原子入队、乐观版本、租约认领、轮转 fencing ID |
| 检索 | 词法、稠密与符号适配器 + 确定性 reciprocal rank fusion |
| 工具使用 | 租户/仓库/commit 作用域 + 角色与阶段授权 |
| 远端写入 | 人工决策绑定计划、目标 commit、声明文件、验证命令与精确工具范围 |
| 补丁安全 | 精确文本编辑、批准路径强制、本地 diff 渲染 + `git apply --check` 预检 |
| 独立审查 | Gateway 收集的 Git diff、变更后源码、验收条件与验证证据 |
| 命令 | 参数数组、可执行白名单、超时、输出上限、净化环境 |
| 沙箱 | 摘要固定镜像、非 root、只读根、丢弃 capabilities、离线检查 |
| 模型输出 | provider 专属结构化 JSON + 严格本地校验；Responses 调用使用 `store=False`；diff hunk 元数据本地派生 |
| 编码上下文 | 仅 Gateway 的搜索/读取请求，固定轮次与工具调用上限 |
| 评测 | 并发套件、重试、来源、基线增量、硬门禁、确定性重放 |
| 隐私 | 递归脱敏 + 当前树与可达历史发布扫描 |
| 浏览器面 | 同源控制台 + CSP + 仅内存的 bearer 身份 |

## 评测

### 评测 harness

Harness 在有界并发下评测版本化套件。它保持清单顺序，只重试超时与基础设施失败，并记录：

- 不可变的仓库 commit 与数据集版本
- provider、模型、提示、工具 schema 与策略版本
- 确定性种子与规范化环境指纹
- 每个用例的观察、尝试、失败类别、延迟、检索、调用与 token
- 聚合 resolution、Recall@10、MRR、回归、安全率与 p50/p95 延迟
- 候选减基线的增量
- 逐项发布门禁检查与一个最终决策

重放为选定用例创建新 run，且永不修改源证据。

运行内置的无凭据示例：

```bash
.venv/bin/python -m repo_maintenance_agent.cli evaluate-suite \
  examples/evaluation/suite.json \
  examples/evaluation/observations.json \
  --json-report artifacts/evaluation/example.json \
  --markdown-report artifacts/evaluation/example.md \
  --candidate-label local-example
```

命令在返回前写入两份报告；门禁失败时以退出码 `1` 返回，可直接用作 CI 发布检查。

### SWE-bench 证据标签

RepoAegis 把生成证据与质量证据分开：

- **one-shot generation**：预测在未收到官方测试反馈的情况下生成；
- **officially resolved**：官方 SWE-bench Docker harness 通过全部必需测试；
- **feedback-assisted calibration**：开发重跑消费了之前的官方失败；
- **frozen evaluation**：CLI 拒绝开发反馈并保持 one-shot 边界。

反馈辅助校准对改进 agent 循环有用，但不会被报告为 one-shot 或 frozen 基准分数。

### 评测战役

开发迭代在从 SWE-bench 全量（2,294 实例）中采样的 **200 实例子集** 上进行，所有 Verified 实例（500 个）按唯一 ID 排除，防止数据泄漏。这一迭代循环——在开发子集上运行、分类失败、修复根因、重新运行——把生成率从最初的 <10% 提升到最终结果。

最终评测在从 **SWE-bench Verified**（500 任务）中采样的 200 实例子集上进行，按仓库分层、比例对齐 Verified 500 分布（seed 42），**frozen** 模式，由**官方 SWE-bench Docker harness** 判定：

| 指标 | 数值 |
|--------|:-----:|
| 总实例 | 200 |
| 成功生成 | 192 / 200（96.0%） |
| 生成失败 | 8 / 200（4.0%） |
| **官方解决（端到端）** | **74 / 200（37.0%）** |
| 官方解决（条件于生成成功） | 74 / 192（38.5%） |

端到端解决率领先的仓库：pydata 62.5%、astropy 55.6%、django 46.3%（44 个解决）、matplotlib 38.5%、sympy 25.8%。逐实例结果、冻结任务 ID、生成失败原因、评分进度与校验和全部发布在 `docs/evaluation-results/` 中供审计（manifest：`manifest.json`，聚合：`aggregate.json`）。

> **注意：** 这是单子集结果，不是排行榜声明。在发布对齐的配对基线之前，不作任何基线提升声明。

### 统计严谨性

比较自带配对 bootstrap 不确定性，而非裸点估计增量：`evaluation/significance.py` 计算可复现的 10,000 次重采样百分位区间（种子固定），标注方向（improvement / regression / inconclusive）；`resolution_statistical_significance` 发布门禁在显著回归与不明确的小样本增量上拒绝。`wilson_ci()` 与精确的 `clopper_pearson_ci()` 为小样本二分类结果给出诚实区间；`required_n_for_power()` 把样本量假设显式化。效应量用 `cohens_h()` 报告，族系多重比较控制用 `holm_adjust()`。聚合报告还暴露平均部分解决率（`tests_passed_ratio`）与缓存命中率，让成本被测量而非猜测。

### LLM-as-a-Judge 与模型矩阵

`evaluation/judge.py` 在确定性 harness 之外增加基于量表的 LLM 判定（独立 judge gateway、逐标准 1–5 分、重跑一致性），并给出两种范式的一致/不一致率。`evaluation/model_matrix.py` 用对齐种子在同一套件上跑多个模型，输出成本–质量表，并计算可直接进入 bootstrap 门禁的配对增量。

### 双轨评测、Inspect 对齐与红队评测

评测跑在两条轨道上，汇入同一个门禁：CI/迭代用快速自研 harness，权威 run 用 UK AISI Inspect 框架。

- **自研 harness（CI，秒级，无模型调用）**：确定性 fixture 评测 smoke 门禁（`.github/workflows/eval-smoke.yml`）+ 完整版本化套件——并发、可断点、可重放，带发布门禁。
- **Inspect 对齐（权威）**：`repo_maintenance_agent/inspect/` 以**脚手架**形式提供桥接——数据集转换、SWE-bench 进度 scorer、`.eval` 日志解析器、agent 桥接骨架——使官方 run 可复用行业标准框架与基线。该桥接是已设计的集成方案，还不是已交付的官方提交；Inspect 负责执行与评分，统计结论仍由 AegisEvo 门禁作为唯一权威。
- **红队用例集**：`examples/evaluation/redteam/` 覆盖提示注入 / 越权工具 / 密钥外泄 / 路径穿越用例，断言 100% deny-by-default 拦截——这是攻击面扫描工具不提供的执行期治理。

## 相关工作

设计建立在 Agent 基准、混合检索、Agent 安全、模型评测与统计学的既有工作上：

- **Agent 基准。** SWE-bench 把 issue 解决定义为由 Docker harness 判定的可复现基准 [1]，SWE-bench Verified 提供人工验证的 500 任务子集 [2]。RepoAegis 报告官方 harness 结果并附逐实例证据，且区分 one-shot 生成与反馈辅助校准。
- **混合检索。** 稠密段落检索展示了稠密表示相对稀疏基线的价值 [3]；reciprocal rank fusion（RRF）提供对多个排序列表的确定性、无参数融合 [4]。RepoAegis 组合词法（BM25）、符号与可选稠密适配器，用 RRF 融合，保持融合确定性与可审计性。
- **Agent 安全。** 间接提示注入展示了攻击者控制的检索内容可攻陷 LLM 集成应用 [5]；InjecAgent 为针对工具集成 Agent 的攻击形式化基准 [6]。RepoAegis 把 issue 文本、仓库文件与模型输出视为不可信数据，并强制 deny-by-default 工具授权、审批绑定的远端写入与沙箱执行。
- **模型评测。** LLM-as-a-judge 建立了 LLM 判定与人类偏好的一致性与偏差特征 [7]。RepoAegis 把确定性 harness 分数与基于量表的 LLM 判定配对，并报告两种范式的一致性。
- **统计学。** Bootstrap [8]、Holm 序贯拒绝过程 [9]、Wilson [10] 与 Clopper–Pearson [11] 二项区间，支撑 `evaluation/significance.py` 中的显著性门禁。
- **进化式 prompt 优化。** EvoPrompt 展示了 LLM 可以驱动对 prompt 策略的进化搜索 [12]；AegisEvo 把同样的证据门控进化纪律应用于 Agent 配置基因组，采用配对 bootstrap 显著性加安全否决。

与相邻工具/工作线的定位：

| 工具/工作线 | 焦点 | RepoAegis / AegisEvo 定位 |
|---|---|---|
| Inspect AI（UK AISI） | 权威 Agent 评测 harness | 提供 Inspect 桥接脚手架，让官方 run 复用标准框架；Inspect 负责执行与评分，RepoAegis 增加发布门禁、安全与成本记账 |
| OpenAI Evals / DeepEval / promptfoo | LLM 评测框架 | 给模型输出打分；RepoAegis 端到端评测 Agent 副作用（工具、沙箱、成本、安全） |
| LangSmith / Braintrust | LLM 应用的评测 + 追踪 + 门禁 | 用阈值门控 prompt/模型调用的回归；AegisEvo 用配对 bootstrap 加安全否决门控 Agent 配置基因组 |
| MLflow / SageMaker Model Registry | 模型权重版本化与晋升 | AegisEvo 治理的是 Agent 配置基因组（而非权重），带内容寻址谱系与统计门禁 |
| Garak / PyRIT / HarmBench | 攻击面扫描 | 互补：探测模型的攻击面；RepoAegis 在执行期强制 deny-by-default 边界 |

### 参考文献

1. John Yang, Carlos E. Jimenez, Alexander Wettig, Kilian Lieret, Shunyu Yao, Karthik Narasimhan, Ofir Press. *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?* ICLR 2024. arXiv:2310.06770.
2. OpenAI. *SWE-bench Verified.* 2024. https://openai.com/index/introducing-swe-bench-verified/.
3. Vladimir Karpukhin, Barlas Oğuz, Sewon Min, Patrick Lewis, Ledell Wu, Sergey Edunov, Danqi Chen, Wen-tau Yih. *Dense Passage Retrieval for Open-Domain Question Answering.* EMNLP 2020. arXiv:2004.04906.
4. Gordon V. Cormack, Charles L. A. Clarke, Stefan Büttcher. *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods.* SIGIR 2009.
5. Kai Greshake, Sahar Abdelnabi, Shailesh Mishra, Christoph Endres, Thorsten Holz, Mario Fritz. *Not what you've signed up for: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection.* AISec 2023. arXiv:2302.12173.
6. Qiusi Zhan, Zhixiang Liang, Zifan Ying, Daniel Kang. *InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated Large Language Model Agents.* ACL 2024 Findings. arXiv:2403.02691.
7. Lianmin Zheng, Wei-Lin Chiang, Ying Sheng, et al. *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena.* NeurIPS 2023. arXiv:2306.05685.
8. Bradley Efron. *Bootstrap Methods: Another Look at the Jackknife.* The Annals of Statistics, 7(1), 1979.
9. Sture Holm. *A Simple Sequentially Rejective Multiple Test Procedure.* Scandinavian Journal of Statistics, 6(2), 1979.
10. Edwin B. Wilson. *Probable Inference, the Law of Succession, and Statistical Inference.* Journal of the American Statistical Association, 22, 1927.
11. C. J. Clopper, E. S. Pearson. *The Use of Confidence or Fiducial Limits Illustrated in the Case of the Binomial.* Biometrika, 26, 1934.
12. Qingyan Guo, Rui Wang, Junliang Guo, Bei Li, Kaitao Song, Xu Tan, Guoqing Liu, Jiang Bian, Yujiu Yang. *EvoPrompt: Connecting LLMs with Evolutionary Algorithms Yields Powerful Prompt Optimizers.* ICLR 2024. arXiv:2309.08532.

## Web 工作台（AI 全栈）

一个 React + Vite 工作台，连接 API 与混合检索代码问答接口：

- **代码问答（混合检索）** `POST /v1/chat`：对仓库做 BM25 + 符号混合检索，通过 OpenAI 兼容模型（DeepSeek）返回带引用的回答，并返回参考路径/行区间。
- **任务控制台** `/v1/tasks`：列出/创建/查看仓库维护任务。
- **评测看板** `/v1/evaluations/runs`：评测 run 与发布门禁。

构建前端并托管：

```bash
cd web
npm --registry=https://registry.npmmirror.com install
npm run build          # outputs web/dist
```

设置 `REPO_AGENT_CHAT_REPO_ROOT` 指向仓库检出即可启用代码问答。对话引擎在 `repo_maintenance_agent/chat.py`；检索在 `search/index.py`（BM25/符号/向量）与 `search/embeddings.py`。

## 快速开始

依赖：

- Python 3.12
- Git
- Docker（沙箱与镜像执行）

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev,postgres,observability]"
.venv/bin/python -m pytest --cov=repo_maintenance_agent --cov-report=term-missing
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src
```

用仅开发的身份启动本地 API：

```bash
export REPO_AGENT_API_TOKENS='{"local-api-token":{"tenant_id":"tenant-local","subject":"local-reviewer"}}'
export REPO_AGENT_ENVIRONMENT='development'
.venv/bin/python -m uvicorn repo_maintenance_agent.main:build_application --factory
```

打开：

- 运维控制台：`http://127.0.0.1:8000/console`
- OpenAPI：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

控制台在加载后请求 bearer 身份，且只保存在 JavaScript 内存中。它不使用 cookie、local storage、session storage 或 URL 参数。

## CLI

仅在当前进程设置 API 身份：

```bash
export REPO_AGENT_API_TOKEN='local-api-token'

repo-agent run owner/repository aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "Fix empty config"
repo-agent status TASK_ID
repo-agent approve TASK_ID PLAN_HASH --reason "Reviewed scope and verification plan"
repo-agent resume TASK_ID PLAN_HASH --reason "Approved for sandbox execution"
repo-agent cancel TASK_ID
```

`status` 返回可审查计划、确定性风险与原因、计划哈希、证据摘要、声明文件、验证计划与允许工具。`approve` 读取该信封并随决策提交其目标 commit 与工具范围。API 拒绝过期的哈希、commit 或工具集；任何变更的信封都要求新的决策。`approve --reject` 记录一次拒绝。

## API 面

认证任务路由：

```text
POST /v1/tasks
GET  /v1/tasks
GET  /v1/tasks/{task_id}
POST /v1/tasks/{task_id}/approval
POST /v1/tasks/{task_id}/cancel
```

任务响应刻意省略租户身份与完整检索内容。证据摘要只包含审查所需的来源、定位符与有界摘要字段。

认证评测路由：

```text
POST /v1/evaluations/runs
GET  /v1/evaluations/runs
GET  /v1/evaluations/runs/{run_id}
POST /v1/evaluations/runs/{run_id}/replay
GET  /v1/evaluations/runs/{run_id}/report.json
GET  /v1/evaluations/runs/{run_id}/report.md
```

跨租户与未知对象 ID 返回相同的 404。公开响应模型省略租户标识与内部队列状态。

## 本地基础设施

Compose profile 定义 API、worker、PostgreSQL、OpenSearch、认证沙箱 runner 与项目自有的 rootless Docker daemon。worker 与 daemon 不共享网络；runner 是唯一桥梁，宿主机不暴露任何 Docker socket 或 daemon 端口。暴露的应用端口绑定 loopback。OpenSearch 安全仅在本本地 profile 中禁用。

```bash
export POSTGRES_PASSWORD='choose-a-local-password'
export REPO_AGENT_API_TOKENS='{"local-api-token":{"tenant_id":"tenant-local","subject":"local-reviewer"}}'
export SANDBOX_RUNNER_TOKEN='choose-a-separate-runner-token'
export REPO_AGENT_REPOSITORY_LOCATORS='{"owner/repository":"/operator/pinned/repository.git"}'
export REPO_AGENT_WORKER_TENANT_IDS='["tenant-local"]'
docker compose config
docker compose up --build
```

应用与任务沙箱容器以 UID 10001 运行，只读根文件系统、丢弃 capabilities、`no-new-privileges`、不可变基础镜像摘要。专用 rootless daemon 与 worker 和宿主机 socket 隔离。沙箱依赖安装是独立的可审计阶段；测试与 lint 阶段无网络运行。Compose 语法、隔离拓扑、镜像构建、六服务启动与一个本地提交任务生命周期均已验证。生产可用性、敌意多租户运行与容量不做声明。

## 配置

| 变量 | 用途 | 密钥 |
|---|---|---|
| `OPENAI_API_KEY` | 可选的实时 OpenAI 模型调用 | 是 |
| `OPENAI_MODEL` | 由模型网关记录与选择的模型 | 否 |
| `REPO_AGENT_API_TOKENS` | 映射到租户与主体的 API bearer 身份 | 是 |
| `REPO_AGENT_API_TOKEN` | CLI bearer 身份 | 是 |
| `REPO_AGENT_API_URL` | CLI API URL | 否 |
| `REPO_AGENT_DATABASE_URL` | SQLAlchemy 任务与评测数据库 | 通常 |
| `REPO_AGENT_ARTIFACT_ROOT` | 工件存储根 | 否 |
| `REPO_AGENT_WORKSPACE_ROOT` | 运维方任务工作区根 | 否 |
| `REPO_AGENT_REPOSITORY_LOCATORS` | 白名单仓库来源注册表 | 通常 |
| `REPO_AGENT_WORKER_TENANT_IDS` | 显式 worker 租户范围 | 否 |
| `REPO_AGENT_SANDBOX_RUNNER_TOKEN` | worker 到 runner 的 bearer 凭证 | 是 |
| `REPO_AGENT_ALLOWED_HOSTS` | Trusted Host 白名单 | 否 |
| `REPO_AGENT_MAX_ITERATIONS` | 有界图纠错预算 | 否 |

应用永不加载仓库的 `.env` 文件。`.env.example` 只含名称与空白占位符。生产凭证属于密钥管理器；GitHub 访问应使用短时 App installation token。

## 安全模型

Issue 文本、仓库文件、模型输出、搜索结果、测试日志与文档都是不可信数据。它们都不能授予权限。每个副作用都穿过类型化适配器与工具网关。

发布门禁：

```bash
.venv/bin/python -m repo_maintenance_agent.security.scanner
```

扫描器检查已跟踪与未忽略文件，以及所有可达 Git 历史中的凭证形态、私钥、个人 Windows 路径与私有代理配置。

## 仓库布局

```text
src/repo_maintenance_agent/
  agents/         类型化专责节点与输出
  api/            认证 API 与控制台路由
  console/        零构建运维工作区
  domain/         框架无关状态与端口
  evaluation/     harness、聚合、门禁、报告与持久化
  graph/          LangGraph 构建与确定性路由
  models/         模型 provider 边界
  observability/  脱敏追踪与归一化指标
  policies/       工具授权与递归脱敏
  sandbox/        语言 profile 与 Docker 验证
  search/         路由、适配器与排序融合
  security/       隐私与凭证扫描
  storage/        任务状态、队列租约与工件
  tools/          Git、GitHub、Context7、补丁与进程适配器
examples/         无凭据评测输入
sandbox/          不可变 worker 镜像与 seccomp profile
tests/            单元与集成契约
```

## 文档

- [权威系统设计](RepoAegis_Design.md)
- [威胁模型](docs/threat-model.md)
- [安全最佳实践报告](security_best_practices_report.md)

## 与 AegisEvo 的联合治理流

RepoAegis 与 AegisEvo 构成一条受治理流水线。RepoAegis 执行仓库维护任务；AegisEvo 是它的搜索、评测与晋升平台，通过版本化、内容寻址的 target pack 驱动固定的 RepoAegis 运行时。

- **RepoAegis** 执行真实仓库任务：materialize 固定 commit → plan → approve → patch → 容器验证 → review → commit/push → 草稿 PR。
- **Target pack** 冻结 RepoAegis commit、运行时源码、镜像与策略摘要（`repoaegis-target-pack/v2`）。独立的 SWE-bench 协议绑定任务 ID、模型、编排元数据与 token 记账策略。
- **AegisEvo** 通过版本化 `repoaegis-http-v1` 适配器消费 target pack，运行等预算 baseline / random / evolution 搜索，并报告解决率、安全、用量与延迟证据（`evaluation-observation/v1`）。
- **受控晋升** 要求绝对质量、统计显著性、零安全回退、预算合规与人工批准。新的 RepoAegis 发布创建新的 target pack，而不是覆盖旧的。

### 版本兼容

| RepoAegis | Target pack | AegisEvo | 契约 |
|---|---|---|---|
| `978d24e`（已评测修订） | `repoaegis-target-pack/v2`（`repoaegis-v2`） | `ed1f445`（已评测修订） | `repoaegis-http-v1` 适配器 + `evaluation-observation/v1` |

跨语言摘要校验与实时联合 demo 验证运行时兼容：AegisEvo 驱动真实 RepoAegis 任务到 `completed`。该历史 demo 不证明任务解决；只有官方 verifier 报告才能确立 `resolved`。评测侧见 [AegisEvo](https://github.com/ETOLucy/AegisEvo)。

## 许可证

Apache License 2.0。见 [LICENSE](LICENSE)。
