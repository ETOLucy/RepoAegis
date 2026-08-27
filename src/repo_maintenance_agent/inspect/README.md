# Inspect Scaffold（RepoAegis × UK AISI Inspect）

把 RepoAegis 的 SWE-bench predictions 交给 **Inspect**（UK AISI 官方框架）的
官方 `swe_bench_scorer`，在 Docker 沙箱里重跑官方测试并判分。

> **状态说明**：本模块是 scaffold 骨架，尚未完成正式 submission 接线。
> 当前已验证的闭环是 **replay 判分**（3/8 resolved），generate 模式
> 的 solver 参数接线尚未完成（见 `repoaegis_solver.py` 的 generate 分支）。
> 真正的 SWE-bench 官方验证器是 `swebench` 包（4.1.0），Inspect 是调用
> 该验证器的评测框架。

> 本目录是 `.portfolio-eval/inspect_pilot/`（2026-08-11 试点）的仓库内版本：
> 代码与试点一致（`pilot_task.py` / `repoaegis_solver.py` / `windows_shims.py`
> / `generate.py` / `prepare_dataset.py`），导入路径改为
> `repo_maintenance_agent.inspect`。

## 文件

| 文件 | 职责 |
|---|---|
| `pilot_task.py` | `@task` 定义 `repoaegis_verified`：本地 SWE-bench JSONL dataset + `repoaegis_solver` + 官方 `swe_bench_scorer` + Docker 沙箱（compose） |
| `repoaegis_solver.py` | Inspect solver：**replay** 模式读取 predictions JSONL 并把 patch 应用进沙箱；**generate** 模式调 RepoAegis 真实生成 |
| `windows_shims.py` | Windows 兼容 shim（swebench 依赖的 `resource` 模块 + UTF-8 兜底） |
| `generate.py` | RepoAegis 真实生成桥接（host 侧，复用 `RepoAegisPatchAgent` 管线） |
| `prepare_dataset.py` | 从本地 HF 缓存 parquet 导出 Inspect 可读的 SWE-bench JSONL（离线） |
| `run.py` | 运行入口（replay / generate） |
| `data/`（本地，不入库） | SWE-bench Verified JSONL 数据集，见「数据格式」 |

## 依赖（可选，不进入默认安装 / CI）

```bash
.venv/Scripts/python.exe -m pip install "inspect-ai>=0.3" "inspect-evals" "swebench==4.1.0" "pyarrow"
```

- `inspect-ai` + `inspect-evals`：Inspect 框架与官方 SWE-bench 任务/scorer；
- `swebench==4.1.0`：官方 SWE-bench harness（scorer 内部依赖）；
- `pyarrow`：仅 `prepare_dataset.py` 重新导出数据集时需要；
- Docker Desktop：判分必须（沙箱）。

> 未安装这些依赖时，`import repo_maintenance_agent.inspect` 不会失败
> （`repoaegis_verified` 是惰性导出；solver 函数从
> `repo_maintenance_agent.inspect.repoaegis_solver` 模块导入）；只有实际调用判分
> API 时才需要它们。

## 数据格式

### 1) 评测数据集（SWE-bench Verified JSONL）

每行一个完整 SWE-bench 任务记录。`pilot_task.py` 的 `json_dataset` 按
`FieldSpec` 读取这些字段：`problem_statement`、`instance_id`、`base_commit`、
`patch`（gold patch）、`FAIL_TO_PASS`、`PASS_TO_PASS`、`test_patch`、
`version`、`repo`、`environment_setup_commit`、`hints_text`、`created_at`。

现成数据集可直接复用
`.portfolio-eval/inspect_pilot/data/verified.jsonl`（8 条 holdout），或用
`prepare_dataset.py` 从本地 HF 缓存重新导出（只导出指定 id）：

```bash
.venv/Scripts/python.exe -m repo_maintenance_agent.inspect.prepare_dataset \
  --output data/verified.jsonl \
  --ids django__django-13568 psf__requests-1921
```

### 2) predictions（SWEbenchPrediction）

RepoAegis 官方格式的 prediction，与
`repo_maintenance_agent.evaluation.swebench.SWEbenchPrediction` 一致，每行
一个 JSON：

```json
{"instance_id": "django__django-13568", "model_patch": "diff --git a/...", "model_name_or_path": "repoaegis/candidate-xxx"}
```

- `model_patch` 必须是 unified diff（`diff --git ` 或 `--- ` 开头），由
  `SWEbenchPrediction` 校验；
- 写出工具：`repo_maintenance_agent.evaluation.swebench.write_predictions(path, predictions)`；
- 现有 RepoAegis 产物（如 `candidate-predictions.jsonl`）可直接用于 `--replay`。

## 运行判分

前置：Docker Desktop 已启动；上面依赖已装入 RepoAegis venv。

### Replay 回放（不花钱，验证管线）

```bash
cd /d/Repos/Agents/RepoAegis
export PYTHONUTF8=1
export PYTHONPATH="$PWD/src"

.venv/Scripts/python.exe -m repo_maintenance_agent.inspect.run \
  --dataset data/verified.jsonl \
  --replay /path/to/candidate-predictions.jsonl \
  --sample-id django__django-13568 --allow-internet
```

参数：

| 参数 | 说明 |
|---|---|
| `--dataset` | SWE-bench JSONL 数据集（默认 `inspect/data/verified.jsonl`） |
| `--replay` | 官方格式 predictions JSONL；**不传则走 generate 模式（花钱调模型）** |
| `--sample-id` | 可重复，只判指定任务 |
| `--limit N` | 只判前 N 个样本 |
| `--max-connections` | 并发沙箱数（默认 1） |
| `--allow-internet` | 允许沙箱联网（eval 脚本需要 pip install 时用） |
| `--log-dir` | `.eval` 日志输出目录（默认 `inspect/logs/`） |

### Generate 真实生成（花钱，调模型）

不传 `--replay` 即走 generate 模式：`repoaegis_solver` 调
`generate.generate_repoaegis_patch`，用 RepoAegis 真实 agent 图（
`RepoAegisPatchAgent` + `GitSWEbenchRuntime`）生成 patch，再交给官方 scorer
判分。需要 CC Switch 数据库（DeepSeek 凭证）与 repository-locators 配置，
见 `generate.py`。

> 注意：试点里 generate 模式在 solver 中的参数接线尚未完成（`repoaegis_solver`
> 的 generate 分支是骨架，需按 `generate_repoaegis_patch` 签名补齐 host 配置）；
> 当前可验证的闭环是 **replay 判分**。

## 输出：.eval 日志与回接 AegisEvo 门控

Inspect 在 `--log-dir` 写出 `.eval` 日志（inspect_ai 0.3.x 默认是 zip
归档）。用 `repo_maintenance_agent.inspect.parser.parse_inspect_log` 把
`.eval` 转成 `EvalResult` 统一行（`source="inspect"`），再进 AegisEvo 统计
门控（均值/方差、baseline 对照、显著性判定，门控只认权威轨道分数）：

```python
from repo_maintenance_agent.inspect.parser import parse_inspect_log

rows = parse_inspect_log("inspect/logs/2026-08-11T01-15-32-00-00_repoaegis-verified_xxx.eval")
for row in rows:
    print(row.sample_id, row.score, row.status)
```

## 已验证（试点结论，2026-08-11）

用 4 条历史 prediction 在 Inspect 官方 `swe_bench_scorer` 下 replay，与官方
SWE-bench 4.1.0 harness 判分完全一致（3/4 resolved，mean=0.75）。详见
`.portfolio-eval/inspect_pilot/README.md` 的验证结论表。