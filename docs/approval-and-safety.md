# 审批与安全机制

> RepoAegis 的核心信任假设是：**Issue 内容、仓库代码、任何外部文本都不可信**，唯一能授权"写"操作的是显式的人工审批。本文描述这个信任边界具体是怎么实现的，对照 `domain/models.py`、`policies/`、`sandbox/docker.py` 源码核实。

## 1. 审批信封（ApprovalEnvelope）

Planning 节点产出计划后，会把计划本身连同执行边界打包进 `ApprovalEnvelope`（`domain/models.py`）：

| 字段 | 内容 |
|---|---|
| `plan` | 计划步骤（元组套字典） |
| `target_commit` | 计划对应的目标提交 SHA |
| `allowed_tools` | 允许 Coding 阶段使用的 `ToolPermission` 集合（校验时自动去重排序） |
| `declared_files` | 声明将修改的文件路径（同样自动去重排序） |
| `verification_plan` | 验证阶段要跑的命令列表 |

`digest()` 方法计算这份信封的指纹：

```python
payload = self.model_dump(mode="json")
encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
return hashlib.sha256(encoded).hexdigest()
```

用 `sort_keys=True` 的 canonical JSON 而不是直接对 Python 对象取哈希，是为了保证同一份计划无论字段顺序、序列化实现怎么变，摘要都是确定性的、可复现的。

人工审批产生 `ApprovalDecision`：`approved` / `approver` / `plan_hash` / `target_commit` / `allowed_tools` / `reason` / `decided_at`。审批本身通过 LangGraph 的 `interrupt()` 暂停流水线，等待外部输入后恢复。

### 审批一致性怎么校验

真正的强制点不在 Approval 节点本身，而在 `policies/permissions.py::PermissionPolicy.authorize()`——每一次工具调用（`ToolCall`）在真正执行前都要过这一关。对 `GIT_WRITE` / `GITHUB_WRITE` 这类写权限，它会**当场重新构造一份 `ApprovalEnvelope`**（用当前 `state.plan` / `state.commit_sha` / `state.allowed_tools` / `state.declared_files` / `state.verification_plan`）并重新计算 `digest()`，然后要求同时满足：

- 存在 `approval` 且 `approval.approved` 为真；
- `approval.plan_hash == state.plan_hash`；
- 重新计算的 `envelope.digest() == state.plan_hash`（防止 approval 通过后 state 里的计划被悄悄改动而不重新计算哈希）；
- `approval.target_commit == state.commit_sha`；
- `approval.allowed_tools == state.allowed_tools`；
- 这次调用申请的权限本身也在 `state.allowed_tools` 里。

任何一条不满足就抛 `AuthorizationDenied`。也就是说，"审批通过"不是一个一次性打勾的状态，而是每次写操作都要重新验证的不变量。

## 2. 权限系统：按 Agent 授权，不是按工具授权

`policies/permissions.py::_AGENT_PERMISSIONS` 把 9 个 Agent 名（外加内部的 `localizer`）各自映射到一组 `ToolPermission`：

| Agent | 权限 |
|---|---|
| `intake` | `github_read` |
| `research` | `github_read`, `repo_read` |
| `localizer`（Research 内部的定位循环） | `repo_read` |
| `planning` | `repo_read` |
| `coding` | `repo_read`, `sandbox_write`, `sandbox_execute` |
| `verification` | `repo_read`, `sandbox_execute` |
| `review` | `repo_read` |
| `pr` | `repo_read`, `git_write`, `github_read`, `github_write` |
| `control` | `control` |

一共 7 种 `ToolPermission`：`REPO_READ` / `SANDBOX_WRITE` / `SANDBOX_EXECUTE` / `GITHUB_READ` / `GIT_WRITE` / `GITHUB_WRITE` / `CONTROL`。`authorize()` 除了检查 agent 是否拥有对应权限，还会：

- 校验 `ToolCall` 携带的 `task_id` / `tenant_id` / `repo_id` / `commit_sha` 与当前任务状态完全一致，防止跨任务/跨租户串用工具调用；
- 对 `SANDBOX_WRITE` / `SANDBOX_EXECUTE`，额外要求任务状态处于 `CODING` / `VERIFYING` / `REVIEWING` 之一，其他阶段不允许沙箱写入；
- 对参数里任何 `path` / `paths` / `cwd` / `file` / `files` / `target` 键，逐一 `resolve()` 后检查是否仍在 `workspace_root` 之内，拦截 `../` 或绝对路径逃逸工作区的尝试。

## 3. 风险判定：确定性规则先兜底

`policies/risk.py::deterministic_risk(paths, allowed_tools)` 是不依赖 LLM 判断的规则引擎，命中以下任一条就把风险定为 `HIGH`（否则 `LOW`）：

- 路径文件名是已知的依赖清单文件（`package.json` / `pyproject.toml` / `go.mod` / `Cargo.lock` 等）；
- 路径落在 CI 配置目录/文件（`.github/workflows/`、`.gitlab-ci.yml`、`Jenkinsfile` 等）；
- 路径任一段命中 `auth` / `authentication` / `authorization` / `security` / `crypto`；
- 路径任一段命中 `migration` / `migrations` / `alembic`；
- 文件名是已知敏感配置（`.env`、`secrets.yml`）或包含 `secret` / `credential`；
- 申请的 `allowed_tools` 里包含 `GIT_WRITE` 或 `GITHUB_WRITE`（意味着这次操作本身就是远程写）。

`higher_risk(left, right)` 按 `LOW < MEDIUM < HIGH < CRITICAL` 取更严格的一方——Planning 节点用它把确定性规则的结论和 LLM 的风险判断合并，任一方判高就是高，不会被 LLM 的乐观判断稀释掉规则命中的结果。这套规则同时被 `route_after_review` 的"证据驱动兜底"复用，确保复审阶段放行前也过一遍同样的高风险路径检查。

## 4. Docker 沙箱隔离

所有代码修改、测试、构建都在 `sandbox/docker.py::DockerSandbox` 构造的容器里执行，`build_command()` 拼出的 `docker run` 参数包含：

- `--read-only` 只读根文件系统，`--tmpfs=/tmp:rw,noexec,nosuid,size=512m` 提供唯一可写、且不可执行的临时目录；
- `--user=10001:10001` 非 root 运行，`--cap-drop=ALL` 丢弃全部 Linux capability；
- `--security-opt=no-new-privileges`，可选叠加自定义 seccomp profile；
- `--network=none` 默认禁用网络（`network_enabled=True` 时才切到 `bridge`）；
- `--cpus` / `--memory` / `--pids-limit` 资源上限（默认 2 核 / 4G / 256 进程），`timeout_seconds` 控制单次执行超时（默认 900 秒）；
- 镜像必须是 `name@sha256:...` 形式的**摘要锁定镜像**——`build_command()` 用正则强制校验，普通 tag（如 `python:3.12`）会直接拒绝构造命令，避免"镜像标签被悄悄改指向"这类供应链风险；
- 容器名按 `task_id` 派生并做安全字符过滤，避免注入到 `docker run` 参数里。

## 5. 输出脱敏

`policies/redaction.py::Redactor` 在任何工具调用的输入/输出记录落盘前递归脱敏：

- 键名匹配 `authorization` / `api_key` / `password` / `secret` / `access_token` / `refresh_token` / `private_key` / `cookie` 等模式（大小写不敏感）的字段，整体替换为 `[REDACTED]`（显式排除了 `token_count` 这种同样带 `token` 但不敏感的计数字段）；
- 字符串内容里出现 `Authorization: Bearer <...>` 或裸 `Bearer <...>` 会替换掉凭据部分、保留前缀；
- 匹配 `sk-` 开头的 OpenAI 风格密钥直接整体替换；
- 递归处理嵌套的 dict / tuple / list，保证深层结构里的敏感字段也不会漏网。

## 小结：写操作的完整校验链

一次真正落地的远程写（比如 PR 节点调用 `github_write`）必须同时满足：

1. Agent 名在 `_AGENT_PERMISSIONS` 里被授予这个权限；
2. 任务状态、租户、仓库、commit 与当前 `ToolCall` 完全匹配；
3. 存在一份 `approved=True` 的 `ApprovalDecision`，且其 `plan_hash` 与**重新计算**的 `ApprovalEnvelope.digest()` 一致；
4. 审批记录里的 `target_commit` / `allowed_tools` 与当前任务状态一致；
5. 涉及的路径参数都落在工作区之内。

任何一环失败都是 `AuthorizationDenied`，流程不会静默降级为"跳过这步继续走"。

## 延伸阅读

- [`architecture.md`](architecture.md) — 审批、权限在整条 9 节点流水线里的位置。
- [`../RepoAegis_Design.md`](../RepoAegis_Design.md) 第 11 节 — 沙箱隔离的完整生产化考量（镜像预热、依赖安装网络策略等尚未全部落地的部分）。
