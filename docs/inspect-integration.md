# Inspect AI 集成脚手架（RepoAegis × UK AISI Inspect）

> 本文件是项目分析报告第三篇的落地说明：把 RepoAegis agent 接入 UK AISI 的
> **Inspect** 评测框架（Task / Dataset / Solver / Scorer 四元组），目标是
> **SWE-bench 官方评测 + baseline 对照 + AegisEvo 统计门控可消费输出**。
>
> 当前状态：**脚手架已就绪**（数据集加载、日志解析、进度评分、agent 桥接骨架
> 均可导入并有单元测试覆盖）；**权威判分轨道已落地可运行**——`pilot_task.py` +
> `repoaegis_solver.py` + `run.py` 已纳入 `inspect/` 包，replay 回放经官方
> `swe_bench_scorer` 验证与 SWE-bench 4.1.0 harness 判分一致（4 条 holdout 3/4）。
> B2 agent 桥接仍未接线（见「后续 TODO」）。

---

## 1. 集成架构总览

RepoAegis 的评测遵循「**增强 + 双轨**」原则：

| 轨道 | 用途 | 载体 | 权威性 |
|---|---|---|---|
| **轻量回归轨道（自研 harness）** | 迭代期快速回归、策略门控、成本控制 | `evaluation/` 模块 | 内部证据 |
| **权威评测轨道（Inspect）** | 官方 SWE-bench 结论、baseline 对照、对外声明 | `inspect/` 模块（脚手架 + 可运行判分） | 唯一权威 |

两条轨道**互不替代**：

- 自研 harness 跑得快、便宜、可解释，但它不是官方结论；
- Inspect 官方 SWE-bench（`inspect_evals.swe_bench` + Docker/K8s sandbox）才是
  「officially resolved」的来源；
- 对外报告只认权威轨道；轻量轨道只用于迭代与诊断。

Inspect 的四元组与本脚手架对应关系：

| Inspect 概念 | 本脚手架实现 | 说明 |
|---|---|---|
| **Task** | `tasks/repoaegis_swe.py`（示例，见文末） | 组装 dataset/solver/scorer |
| **Dataset** | `dataset.load_repoaegis_holdout` | holdout JSONL → `MemoryDataset` |
| **Solver/Agent** | `bridge.repoaegis_agent` | RepoAegis agent 桥接骨架（TODO） |
| **Scorer** | `scorers.repoaegis_swe_progress_scorer` | 官方 SWE-bench 之上的连续进度分 |
| **日志 → 门控** | `parser.parse_inspect_log` | `.eval` 日志 → `EvalResult` 统一行 |

---

## 2. 方案 A / B / C 简述

### 方案 A：Inspect 定义 SWE-bench 任务

直接用 Inspect 生态的官方 SWE-bench 任务（本机 venv 已安装
`inspect_evals.swe_bench`，含 `swe_bench` 任务与 `swe_bench_scorer`）：

- 数据源 `princeton-nlp/SWE-bench_Verified`（或自备 holdout），sandbox 跑官方
  eval script，`swe_bench_scorer` 给出 0/1 权威结论；
- RepoAegis 只需产出标准 predictions JSONL，交给官方 harness 复核（现有
  `evaluation/swebench.py` 的 `write_predictions` 已可复用）。

```powershell
.venv\Scripts\python.exe -m inspect_evals swe_bench `
  --model openai/gpt-5.5 --sandbox docker --limit 3
```

### 方案 B：agent 桥接（三种方式）

把 RepoAegis 作为 Inspect 的 Solver/Agent 接入，三种方式递进：

1. **B1 预测文件桥接（最稳，推荐先做）**：RepoAegis 自行跑任务、产出
   predictions JSONL，Inspect 仅负责「官方复核 + 评分 + 出日志」；
2. **B2 `@agent` + `agent_bridge`（本脚手架采用）**：RepoAegis 在 Inspect
   进程内运行，内部 LLM 客户端注入 `model_name="inspect"`，bridge 把请求
   转接到 Inspect 模型 API（`bridge.py` 骨架，待接线）；
3. **B3 子代理（subagent）**：用 `general`/`subagent` 组合 RepoAegis 工具，
   不改 RepoAegis 主循环，适合「用 Inspect 工具补充 RepoAegis」。

### 方案 C：评分结果接入 AegisEvo 统计门控

Inspect 的 `.eval` 日志经 `parser.parse_inspect_log` 转成统一 `EvalResult`
行（`run_id / model / sample_id / score / extra_scores / tokens /
tool_calls / status / source="inspect"`），与自研 harness 的行共用同一统计
门控（均值/方差、baseline 对照、显著性判定），门控**只认权威轨道**的分数。

---

## 3. 数据流图（ASCII）

```
 data/holdout.jsonl (instance_id, problem_statement, repo, base_commit,
                     test_patch, FAIL_TO_PASS, PASS_TO_PASS, difficulty,
                     gold_patch?)
        │ load_repoaegis_holdout()
        ▼
 Inspect MemoryDataset (Sample: id/input/target/metadata)
        │
        ▼
 Task ── solver: as_solver(repoaegis_agent())   [B2 桥接，TODO 接线]
        │          │
        │          └─ LLM client(model_name="inspect")
        │              └──► agent_bridge ──► Inspect 模型 API
        │
        ▼
 scorer: repoaegis_swe_progress_scorer()  ── 官方 swe_bench_scorer 之上
        │  metadata: passed_ratio / passed_ftp / passed_p2p
        ▼
 .eval 日志（zip 或 JSONL 事件流）
        │ parse_inspect_log()
        ▼
 EvalResult[]（统一行，source="inspect"）
        │
        ▼
 AegisEvo 统计门控（baseline 对照 + 显著性 + 门控判定）
```

---

## 4. 模块清单

| 文件 | 职责 | 公开 API |
|---|---|---|
| `src/repo_maintenance_agent/inspect/dataset.py` | holdout JSONL → Inspect `MemoryDataset`/`Sample`；`FAIL_TO_PASS`/`PASS_TO_PASS` 归一化为 list | `load_repoaegis_holdout(path)` |
| `src/repo_maintenance_agent/inspect/scorers.py` | 官方 SWE-bench 之上的 0~1 连续进度分；纯函数 `_progress_ratio` 可单测 | `repoaegis_swe_progress_scorer(pass_threshold=0.5)` |
| `src/repo_maintenance_agent/inspect/parser.py` | `.eval` 日志（JSONL 事件 / zip 归档）→ `EvalResult` 统一行，容错解析 | `parse_inspect_log(path)`、`EvalResult` |
| `src/repo_maintenance_agent/inspect/bridge.py` | RepoAegis agent → Inspect `Agent`（`agent_bridge` 桥接骨架） | `repoaegis_agent()` |
| `src/repo_maintenance_agent/inspect/__init__.py` | 导出公开 API（判分入口惰性导出） | 上述 API + `repoaegis_verified`（惰性导出） |
| `src/repo_maintenance_agent/inspect/pilot_task.py` | 可运行权威判分 task：本地 SWE-bench JSONL dataset + `repoaegis_solver` + 官方 `swe_bench_scorer` + Docker compose 沙箱 | `repoaegis_verified(...)` |
| `src/repo_maintenance_agent/inspect/repoaegis_solver.py` | replay / generate solver：读 predictions JSONL 应用 patch，或调 RepoAegis 真实生成 | `repoaegis_solver.repoaegis_solver(predictions_path=...)`（从模块导入） |
| `src/repo_maintenance_agent/inspect/windows_shims.py` | Windows 兼容 shim（`resource` 模块 + UTF-8） | `install_windows_shims()` |
| `src/repo_maintenance_agent/inspect/run.py` | 运行入口（replay / generate，产出 `.eval` 日志） | `python -m repo_maintenance_agent.inspect.run` |
| `src/repo_maintenance_agent/inspect/generate.py` | RepoAegis 真实生成桥接（host 侧，复用 `RepoAegisPatchAgent` 管线） | `generate_repoaegis_patch(...)` |
| `src/repo_maintenance_agent/inspect/README.md` | 判分用法、数据格式（SWEbenchPrediction）、依赖说明 | — |

测试：`tests/unit/inspect/`（`test_dataset.py` / `test_parser.py` /
`test_scorers.py`，共 23 例）。

---

## 5. 使用步骤

1. **准备 holdout**（`data/holdout.jsonl` 风格，字段见上文数据流图）：

   ```json
   {"instance_id":"django__django-11039","problem_statement":"...","repo":"django/django","base_commit":"abc123","test_patch":"diff --git ...","FAIL_TO_PASS":["tests/test_x.py::test_a"],"PASS_TO_PASS":[],"difficulty":"easy"}
   ```

2. **写 task 文件**（示例 `tasks/repoaegis_swe.py`，可新建）：

   ```python
   from inspect_ai import Task, task
   from inspect_ai.agent import as_solver

   from repo_maintenance_agent.inspect.bridge import repoaegis_agent
   from repo_maintenance_agent.inspect.dataset import load_repoaegis_holdout
   from repo_maintenance_agent.inspect.scorers import repoaegis_swe_progress_scorer


   @task
   def repoaegis_swe() -> Task:
       return Task(
           dataset=load_repoaegis_holdout("data/holdout.jsonl"),
           solver=as_solver(repoaegis_agent()),
           scorer=repoaegis_swe_progress_scorer(),
       )
   ```

3. **运行评测**：

   ```powershell
   cd D:\Repos\Agents\RepoAegis
   $env:PYTHONPATH='D:\Repos\Agents\RepoAegis\src'
   .venv\Scripts\python.exe -m inspect eval tasks/repoaegis_swe.py `
     --model openai/gpt-5.5 --limit 5 --sandbox docker
   ```

4. **消费日志**：

   ```python
   from repo_maintenance_agent.inspect.parser import parse_inspect_log

   rows = parse_inspect_log("logs/repoaegis_swe-2026-08-11T00-00-00.eval")
   for row in rows:
       print(row.sample_id, row.score, row.tokens, row.tool_calls, row.status)
   ```

5. **AegisEvo 门控**：把 `rows`（`source="inspect"`）并入统一统计入口，
   与 baseline 对照后做显著性判定与门控决策。

> 注：B2 桥接尚未接线，直接运行 task 会在 agent 处抛
> `NotImplementedError`。当前可先行使用**方案 B1**（RepoAegis 自产预测 →
> 官方 harness 复核）与**方案 A**（官方 `swe_bench` 任务）完成权威评测闭环。

### 可运行的权威判分（replay，已落地）

试点（`.portfolio-eval/inspect_pilot/`，2026-08-11）已证明：Inspect 官方
`swe_bench_scorer` 与 SWE-bench 4.1.0 harness 判分**完全一致**（4 条 holdout
replay：3/4 resolved，mean=0.75）。据此把可运行部分纳入仓库
`src/repo_maintenance_agent/inspect/`（`pilot_task.py` / `repoaegis_solver.py`
/ `windows_shims.py` / `run.py` / `generate.py` / `prepare_dataset.py`，见
第 4 节模块清单），「用 Inspect 官方 harness 判分」现在是可验证的：

```bash
cd /d/Repos/Agents/RepoAegis
export PYTHONUTF8=1
export PYTHONPATH="$PWD/src"

.venv/Scripts/python.exe -m repo_maintenance_agent.inspect.run \
  --dataset data/verified.jsonl \
  --replay /path/to/candidate-predictions.jsonl \
  --sample-id django__django-13568 --allow-internet
```

- `--replay` 走 **replay 模式**：`repoaegis_solver` 读取官方格式 predictions
  JSONL（`SWEbenchPrediction`：`instance_id` / `model_patch` /
  `model_name_or_path`），把 patch 应用进 Docker 沙箱，官方
  `swe_bench_scorer` 重跑测试判分（不花钱）；
- 不传 `--replay` 走 **generate 模式**：调 RepoAegis 真实生成（花钱，需
  CC Switch 凭证，见 `generate.py`；solver 的 generate 分支目前仍是骨架，
  参数接线见 `repoaegis_solver.py` 内注释）；
- 判分结果以 `.eval` 日志落在 `--log-dir`，经 `parse_inspect_log` 转成
  `EvalResult` 统一行（`source="inspect"`）后进 AegisEvo 统计门控（见上文
  步骤 4、5 与第 2 节方案 C）；
- 完整命令与数据格式见 `src/repo_maintenance_agent/inspect/README.md`。

> 依赖说明：`inspect-ai` / `inspect-evals` / `swebench==4.1.0` 是可选依赖，
> 不在 RepoAegis 默认安装与 CI 里；`repoaegis_verified` 在包 `__init__` 中
> **惰性导出**，solver 函数从 `repoaegis_solver` 模块导入，未装依赖时导入包本身不失败。

---

## 6. 与现有 evaluation 模块的关系

- `evaluation/`（自研 harness）负责迭代期轻量回归、策略/成本门控、证据归档；
- `inspect/`（本脚手架）负责权威 SWE-bench 结论与对外声明；
- 二者**输出格式不同但口径一致**：都以 `FAIL_TO_PASS`/`PASS_TO_PASS` 是否
  全过为「resolved」的判据；Inspect 轨道以官方 sandbox 为准；
- 复用的现成物：`evaluation/swebench.py::write_predictions`（SWE-bench
  predictions JSONL 写出）、`evaluation/swebench.py::SWEbenchPrediction`
  （校验模型 patch 为 unified diff）。

**并行边界提醒**：本脚手架只新增 `inspect/` 与 `tests/unit/inspect/` 与
`docs/inspect-integration.md`，不触碰 `evaluation/` 任何文件。

---

## 7. 版本兼容说明（API 偏差，以安装的 inspect_ai 0.3.255 为准）

联调前核对过本地 `inspect_ai 0.3.255`（Python 3.12），以下为与「常见认知 /
旧文档」不一致、且本脚手架已按本地实际 API 适配的点：

1. **`.eval` 日志格式**：0.3.x 默认 `.eval` 是 **zip 归档**（`PK` 魔数），
   不再是旧版「每行一个 JSON 事件」的纯 JSONL。`parse_inspect_log` 自动探测：
   zip → `inspect_ai.log.read_eval_log`；JSONL 事件流（`type` 为
   `"sample"`/`"score"` 等）→ 容错逐行解析。JSONL schema 以安装版本为准，
   解析失败不崩溃（坏行跳过、缺字段给默认值）。
2. **`TaskState` 没有 `.sample` 属性**：任务描述里写的是
   `state.sample.metadata`，但 0.3.255 的 `TaskState` 只暴露 `state.metadata`。
   `scorers.py` 已按 `state.metadata` 实现并写进 docstring。
3. **`AgentBridge` 没有 `bridge.tools` / `bridge.input` / `bridge.output` 属性**：
   bridge 通过 patch 客户端库 + `bridge_generate` 转发客户端声明的工具，不是
   暴露属性列表。`bridge.py` 的 TODO 已按此说明工具接入点。
4. **`@agent` 返回的 Agent 不能直接当 solver**：需 `as_solver(repoaegis_agent())`
   转换（`inspect_ai.agent.as_solver`）。
5. **官方 SWE-bench scorer** 在 `inspect_evals.swe_bench.scorers.swe_bench_scorer`
   （metrics 为 `mean()`+`std()`），生产进度分可解析其
   `swebench.harness.grading.get_eval_report` 返回的 `tests_status`
   （按 `FAIL_TO_PASS`/`PASS_TO_PASS` 分类的每测试结果）算出 `passed_ratio`。
6. **`Score.value` 支持 float**：进度分以 0~1 连续值返回，`mean()` 直接聚合；
   二进制 `passed` 标志放在 `Score.metadata`。

---

## 8. 后续 TODO（联调清单）

1. **LLM 客户端注入**：`models/openai_gateway.py` 支持注入
   `model_name="inspect"`，使 RepoAegis 请求经 bridge 转发到 Inspect；
2. **工具网关对接**：RepoAegis `tools/` + `domain.ports` 映射到 Inspect tool
   协议（`AgentBridge`/`bridge_generate`，注意第 7.3 条）；
3. **消息转换**：Inspect `ChatMessage` ↔ RepoAegis `ToolCall`/`ToolResult`
   （`domain.models`），并把 `passed_ratio`/`passed_ftp`/`passed_p2p`
   写回 `state.metadata`；
4. **评测产物落盘**：model patch 按 SWE-bench predictions JSONL 写出
   （`evaluation/swebench.py::write_predictions` 已可复用；replay 判分已消费
   该格式）；剩余：generate 模式端到端跑通后统一归档；
5. **端到端验证**：replay 闭环**已验证**——4 条 holdout 经官方
   `swe_bench_scorer` 判分 3/4，与 SWE-bench 4.1.0 harness 一致，`.eval`
   可被 `parse_inspect_log` 消费；**generate 模式**待跑 1-2 个新 case。

---

