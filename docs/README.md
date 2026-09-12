# 文档索引

这些文档描述 RepoAegis **当前实现**的架构，内容对照源码核实，随代码演进会持续更新（如与源码冲突，以源码为准）。

建议阅读顺序：

1. [`architecture.md`](architecture.md) — 系统整体架构：分层视图、任务状态机、9 节点流水线。先读这篇建立全局图景。
2. [`search-system.md`](search-system.md) — 混合检索子系统：SearchKind/QueryKind 两层分类、RRF 融合、调用图检索。
3. [`approval-and-safety.md`](approval-and-safety.md) — 信任边界：审批信封、权限系统、沙箱隔离、脱敏。
4. [`architecture-flow.md`](architecture-flow.md) — 以上内容的 Mermaid 流程图合集，适合对照代码位置速查。

其他相关文档：

- [`../RepoAegis_Design.md`](../RepoAegis_Design.md) — 完整的生产级参考架构，覆盖尚未落地的阶段性规划（Phase 2/3），是"蓝图"而非"现状"。
- [`refactor-plan.md`](refactor-plan.md) — 对照源码的现状核实、已知差距、改造优先级和进展记录。
- [`industry-survey.md`](industry-survey.md) — 业界 coding agent 全流程调研（2026-09）：主流方案、关键论文与数据、RepoAegis 每一环的选型与理由，以及对改造计划的逐项评估。
- [`interview-prep-complete.md`](interview-prep-complete.md) — 个人面试准备笔记（Q&A 形式），非项目通用文档。
