<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/repo-aegis-mark-reversed.svg">
    <img src="docs/assets/repo-aegis-mark.svg" width="112" alt="RepoAegis mark">
  </picture>
</p>

<h1 align="center">RepoAegis</h1>

<p align="center">
  A policy-controlled, evidence-backed issue-fixing pipeline: from a GitHub issue → code localization → patch generation → sandbox verification → human approval → PR submission.
</p>

<p align="center">
  <a href="https://github.com/ETOLucy/RepoAegis/actions/workflows/ci.yml"><img src="https://github.com/ETOLucy/RepoAegis/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="https://github.com/ETOLucy/RepoAegis/actions/workflows/eval-smoke.yml"><img src="https://github.com/ETOLucy/RepoAegis/actions/workflows/eval-smoke.yml/badge.svg" alt="eval-smoke"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12-245dcc.svg" alt="Python 3.12"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-177245.svg" alt="License: Apache-2.0"></a>
</p>

<p align="center">
  <a href="README.md">中文</a>
</p>

---

## Overview

RepoAegis is a **policy-controlled, evidence-backed** issue-fixing pipeline: given a GitHub issue, it automates the full flow from understanding the problem to submitting a PR. Every remote write requires human approval, every code change carries supporting evidence, and every execution runs inside an isolated sandbox.

Core orchestration is a LangGraph StateGraph (conditional routing + bounded retry + evidence-driven fallback) spanning Intake / Research / Planning / Approval / Coding / Verification / Review / PR / Finalize.

## Project status

The project is currently being reworked per [`改造计划.md`](改造计划.md) (real evaluation data, dependency-aware code navigation, multi-agent orchestration ablation, and more — Chinese only for now). Full architecture, module design, and search strategy details live in [`架构记忆文档.md`](架构记忆文档.md), which will keep changing during the rework — this README is intentionally kept minimal for now so it doesn't drift out of sync with the implementation.

## Quick start

### Prerequisites

- Python 3.12+
- Node.js 18+
- Docker (optional, for sandbox verification)
- OpenAI API key (or a compatible endpoint)

### Install

```bash
git clone https://github.com/ETOLucy/RepoAegis.git
cd RepoAegis

# backend
python3 -m venv .venv
source .venv/Scripts/activate
pip install -e ".[dev]"

# frontend
cd web && npm install && cd ..
```

### Configure

```bash
export OPENAI_API_KEY="sk-your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"  # optional, compatible endpoint
export REPO_AGENT_API_TOKENS='{"dev-token":"dev-tenant"}'
```

### Run the dev servers

```bash
# terminal 1: backend API
.venv/Scripts/python.exe -m uvicorn repo_maintenance_agent.main:build_application --host 127.0.0.1 --port 8000

# terminal 2: frontend console
cd web && npx vite --host 127.0.0.1 --port 5173
```

### Verify

```bash
curl http://127.0.0.1:8000/v1/health
# expected: {"status":"ok"}
```

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.12+ |
| Orchestration | LangGraph (StateGraph) |
| Model access | openai SDK (Responses API) |
| Web | FastAPI + uvicorn, frontend React + Vite |
| Sandbox | Docker (digest-pinned image, non-root, read-only root) |
| Evaluation | In-house harness + Inspect scaffold (skeleton, see `src/repo_maintenance_agent/inspect/README.md`) |

## Related projects

- [AegisEvo](https://github.com/ETOLucy/AegisEvo) — Genome-based evolutionary optimization for agent configs, paired with RepoAegis.
- [UK AISI Inspect](https://github.com/UKGovernmentBEIS/inspect_ai) — Industry-standard agent evaluation framework. RepoAegis provides a scaffold skeleton (`inspect/`); generate-mode wiring is not yet complete.
