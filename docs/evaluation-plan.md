# Evaluation plan (enhanced)

> 2026-08-14
> 目标：把评测升级为「可复现、可对照、有统计边界、成本可控、可诊断闭环」的方案（基于 SWE-bench 200 子集开发迭代，已在从 Verified 500 中抽样的 200 子集上完成评测）。
> 原则：保留自研 harness 亮点；用 Inspect 对齐行业标准；诚实报告永远优先于好看数字。

## 1. 现状与缺口

| 维度 | 现状 | 缺口 |
|---|---|---|
| 样本量 | 基于 SWE-bench 200 子集（剔除 Verified）进行开发迭代，已在从 Verified 500 中抽样的 200 子集上完成评测 | 开发集与评测集完全隔离 |
| 对照 | 无 baseline 对照 | 无法回答「相对公开参考分是否更好」 |
| 统计 | RepoAegis 比较无 CI；AegisEvo 有 bootstrap 未打通 | 点估计缺失不确定性传播（已补 paired bootstrap） |
| 指标 | resolution / Recall@10 / MRR / 延迟 / token | 缺部分解决率、NDCG、缓存命中率（已补） |
| 失败诊断 | 粗粒度五类 | 无法定位检索/规划/补丁格式缺陷（已补细粒度） |
| 主观维度 | 无 | LLM-as-Judge 双轨（已补） |
| 行业对齐 | 自研 | Inspect 桥接（已脚手架） |
| 成本 | token 记账 | 缺 cache hit rate 门禁（已补） |

## 2. 增强目标（可量化）

1. 样本分层扩展：从 Verified 500 中抽样的 200 实例子集中按 repo 覆盖 + 难度代表性分层抽样，冻结后评估，保留防污染。
2. baseline 对照：每次正式评测输出「绝对分 + 相对公开参考分 delta」双呈现。
3. 统计边界：所有正式结论带 Wilson CI / paired bootstrap CI；小样本不 claim 显著。
4. 诊断闭环：失败分类细化 → 定位缺陷 → 改进 → 复测，形成可跟踪案例。
5. 成本可观测：cache hit rate + token 预算进入门禁与报告。

## 3. 分层抽样方案（scripts/sample_swebench.py）

本方案适用于 **SWE-bench Verified 评测子集**（从 500 全量中抽样 200 实例），用于正式评测阶段的分层抽样。
开发迭代阶段已在 200 实例子集（剔除 Verified）上完成，不在此方案范围内。

- 输入：SWE-bench **Verified** 全量（500 实例），本次评测从中抽样 200 实例。
- 分层维度：repo（维度覆盖，每 repo 至少 1 个）+ difficulty（easy/medium/hard 按整体占比配额）。
- 确定性：random.Random(seed)，同 seed 输出一致；输出行加 sample_seed / sampled: true。
- 规模策略（按可用预算选，均从 Verified 全量中抽样）：
  - 100 例：每日回归，~$60-150。
  - 100-200 例：每周权威，配对设计 200 例可检测 ~10 点效应（α=0.05、功效 0.80）。
  - 200 例：发布前 final（200 子集），仅公开发布/冲榜时跑。
- 防污染：抽样清单在评估前冻结（frozen holdout），开发反馈不回灌；生成结果与官方 harness 验证分离。

## 4. baseline 对照（scripts/report_baseline.py）

- 内置公开参考分（条件与子集差异在报告中注明，仅方向性参考）：
  - SWE-agent (GPT-4o) ≈ 18%
  - Claude 3.5 Sonnet ≈ 46%
  - OpenHands / CodeAct ≈ 26%
- 输出 markdown 表：Method | Resolution | Delta vs RepoAegis，按 resolution 降序。
- 诚实边界：不同子集/环境不可直接比；对照的唯一目的是给「绝对分」提供上下文锚点。

## 5. 统计方法

- 小样本二分类：wilson_ci(k, n) 给出诚实区间，样本不足时不 claim 显著。
- 配对对比：paired_bootstrap_delta（10,000 次重采样、seed 固定）→ CI + direction。
- 多重校正：AegisEvo holm_adjust（family-wise alpha 控制）。
- 门禁：resolution_statistical_significance gate —— regression 拦截、inconclusive 标注样本不足、improvement 通过。
- 报告模板：-0.0500[-0.1200, 0.0100] (inconclusive)。

## 6. 成本控制

- 每次运行记录：input cache-hit / cache-miss / output tokens、estimated cost。
- cache_hit_rate 聚合 + 门禁阈值（可选）：cache 命中高 = 评测成本低。
- 预算分级：25 任务 smoke 级 < 50 任务正式级 < 200 任务权威级；每级明确 token 上限。
- 失败重试：仅 TIMEOUT / INFRASTRUCTURE 可重试，避免无效烧钱。

## 7. 诊断闭环

评测 → 失败分类（retrieval/planning/patch_format/verification/...）→ 定位缺陷 → 针对性改进 → 复测对比（bootstrap 看是否显著提升）。

- 示例：某 case 失败分类为 patch_format → 改进 diff 渲染校验 → 复测该 case 通过 → paired bootstrap 确认方向。
- LLM-as-Judge 双轨：确定性 0/1 + rubric 打分，judge_disagreement_rate 定位「测试判过但质量差」或「测试判不过但意图对」的 case。

## 8. 行业对齐（Inspect 双轨）

- 轻量回归（CI / 迭代）：自研 harness（快、便宜、已有）。
- 权威评测（发布 / 晋级）：Inspect（SWE-bench 官方 scorer + baseline 对照 + .eval 日志）。
- AegisEvo 门控：两轨评分归一化为统一 EvalResult，喂 paired bootstrap + promotion。

## 9. 执行节奏

| 阶段 | 任务 |
|---|---|
| 管线收尾 | 分层抽样器、baseline 报告、失败分类、NDCG 管线收尾；README 更新 Wilson CI 数字 |
| 方向验证 | 跑 25 任务分层抽样（方向验证），记录 token 成本 |
| 规模扩展 | 若预算允许扩到 50；输出 baseline 对照报告 |
| 全链路通跑 | RepoAegis eval → AegisEvo bootstrap gate → 报告 |


