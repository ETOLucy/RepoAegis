# RepoAegis Baseline Reference

> 2026-08-14 | 记录公开基线参考值的来源与诚实边界。

## 1. 公开基线参考值

| 方法 | resolution_rate | 来源 |
|---|---:|---:|
| SWE-agent (GPT-4o) | 0.180 | SWE-bench 官方 run（2025） |
| Claude 3.5 Sonnet | 0.463 | 2026 公开参考（近似） |
| OpenHands / CodeAct | 0.260 | 公开参考 |

这些值来自公开榜单/报告（scripts/report_baseline.py 内置），仅作为绝对分的上下文锚点，
**不代表与 RepoAegis 在同一子集上的对比结果**。

## 2. RepoAegis 自身结果

**200 任务 SWE-bench Verified 生成 campaign**（已完成，deepseek-v4-flash@2026-08-06，
swebench==4.1.0）：结果已发布。

## 3. 诚实边界

1. 公开基线跑在 SWE-bench Verified 200 例或其它子集；**不同子集/阶段不可直接比**。
2. RepoAegis 用 deepseek-v4-flash；基线多为 Claude 3.5 Sonnet / GPT-4o，**模型不同**。
3. 基线数字来自 2025/2026 公开资料；RepoAegis 评测于 2026-08，**时间不同**。
4. 结果已发布；在判分与足够样本量之前，不支撑统计显著或自动晋升结论。
5. 公开基线并列仅作**方向性参考**，不构成排行榜对比。

