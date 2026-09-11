<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/repo-aegis-mark-reversed.svg">
    <img src="docs/assets/repo-aegis-mark.svg" width="112" alt="RepoAegis 标志">
  </picture>
</p>

<h1 align="center">RepoAegis</h1>

<p align="center">
  Policy-controlled, evidence-backed 的 Issue 修复流水线：从 GitHub Issue 出发 → 定位代码 → 生成 patch → 沙箱验证 → 人工审批 → 提交 PR。
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

## 项目定位

RepoAegis 是一个 **policy-controlled、evidence-backed** 的 Issue 修复流水线：给定一个 GitHub Issue，自动完成从理解问题到提交 PR 的全流程，每一次远程写入都经过人工审批，每一次代码变更都有证据支撑，每一次执行都在隔离沙箱中完成。

核心编排基于 LangGraph 的 StateGraph（条件路由 + 有界重试 + 证据驱动兜底），涵盖 Intake / Research / Planning / Approval / Coding / Verification / Review / PR / Finalize。

## 项目状态

当前正按 [`改造计划.md`](改造计划.md) 推进重构（重点：真实评测数据、依赖感知的代码导航、多 agent 编排消融实验等）。详细架构、模块设计和搜索策略说明见 [`架构记忆文档.md`](架构记忆文档.md)，改造过程中会持续变化，本 README 暂时只保留可直接操作的信息，避免和实现脱节。

## 快速开始

### 前置要求

- Python 3.12+
- Node.js 18+
- Docker（可选，用于沙箱验证）
- OpenAI API Key（或兼容接口）

### 安装

```bash
git clone https://github.com/ETOLucy/RepoAegis.git
cd RepoAegis

# 后端
python3 -m venv .venv
source .venv/Scripts/activate
pip install -e ".[dev]"

# 前端
cd web && npm install && cd ..
```

### 配置

```bash
export OPENAI_API_KEY="sk-your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # 可选，兼容接口
export REPO_AGENT_API_TOKENS='{"dev-token":"dev-tenant"}'
```

### 启动开发服务器

```bash
# 终端 1：后端 API 服务
.venv/Scripts/python.exe -m uvicorn repo_maintenance_agent.main:build_application --host 127.0.0.1 --port 8000

# 终端 2：前端控制台
cd web && npx vite --host 127.0.0.1 --port 5173
```

### 验证

```bash
curl http://127.0.0.1:8000/v1/health
# 预期返回：{"status":"ok"}
```

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.12+ |
| 编排 | LangGraph（StateGraph） |
| 模型接入 | openai SDK（Responses API） |
| Web | FastAPI + uvicorn，前端 React + Vite |
| 沙箱 | Docker（digest-pinned 镜像、非 root、只读根） |
| 评测 | 自研 harness + Inspect scaffold（骨架，见 `src/repo_maintenance_agent/inspect/README.md`） |

## 相关项目

- [AegisEvo](https://github.com/ETOLucy/AegisEvo) — Agent 配置基因组进化优化，配套 RepoAegis 使用。
- [UK AISI Inspect](https://github.com/UKGovernmentBEIS/inspect_ai) — 行业标准 Agent 评测框架。RepoAegis 提供 scaffold 骨架（`inspect/`），generate 模式接线尚未完成。
