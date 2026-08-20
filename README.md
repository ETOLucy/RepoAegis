<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/repo-aegis-mark-reversed.svg">
    <img src="docs/assets/repo-aegis-mark.svg" width="112" alt="RepoAegis mark">
  </picture>
</p>

<h1 align="center">RepoAegis</h1>

<p align="center">
  A policy-controlled repository maintenance agent framework for evidence-backed patches and reviewable delivery.
</p>

<p align="center">
  <a href="https://github.com/ETOLucy/RepoAegis/actions/workflows/ci.yml"><img src="https://github.com/ETOLucy/RepoAegis/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <a href="https://github.com/ETOLucy/RepoAegis/actions/workflows/eval-smoke.yml"><img src="https://github.com/ETOLucy/RepoAegis/actions/workflows/eval-smoke.yml/badge.svg" alt="eval-smoke"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12-245dcc.svg" alt="Python 3.12"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-177245.svg" alt="License: Apache-2.0"></a>
</p>

<p align="center">
  <a href="README.zh-CN.md">简体中文</a>
</p>

---

## What Is This

**RepoAegis is a policy-controlled LLM agent framework that turns GitHub issues into evidence-backed, reviewable code patches.** Typed state, tenant isolation, deterministic routing, tool authorization, leased workers, hybrid retrieval, container isolation, and reproducible evaluation sit between a model decision and every side effect.

The system targets production-grade repository maintenance:

- **LLM agent orchestration** — a LangGraph multi-agent pipeline (intake → localization → planning → patching → verification → review) with deterministic routing and bounded correction loops.
- **Hybrid retrieval for code** — BM25 (ripgrep), symbol, and optional vector/OpenSearch adapters over the repository, fused with deterministic **reciprocal rank fusion (RRF)**; a code Q&A endpoint answers with cited file paths and line ranges.
- **Prompt engineering & structured output** — provider-specific structured JSON with strict local Pydantic validation; the model proposes bounded exact-text edits, and diff hunk metadata is derived locally.
- **Model evaluation & benchmarking** — a reproducible evaluation harness (concurrent, replay-safe, with release gates) and a completed **SWE-bench Verified** campaign judged by the official Docker harness; statistical rigor via **paired bootstrap CI**, Wilson / Clopper–Pearson intervals, Cohen's h, and **Holm correction**.
- **LLM-as-a-Judge** — dual-paradigm model evaluation: deterministic harness plus rubric-based LLM judging, and a model matrix that runs one suite across several models with aligned seeds and reports cost–quality trade-offs.
- **Safety & guardrails** — **deny-by-default** tool authorization with stage-aware permissions, approval-envelope-bound human approval for remote writes, Docker sandbox with digest-pinned images / non-root / read-only root / dropped capabilities / `no-new-privileges`, recursive secret redaction, and an execution-time red-team suite (prompt injection / unauthorized tools / secret exfiltration / path traversal).

**Benchmark result (frozen, one-shot):** 74 / 200 (37.0%) instances officially resolved on a stratified subset of **SWE-bench Verified**, 38.5% conditional on successful generation, judged by the official SWE-bench Docker harness — with per-instance results, frozen task IDs, and generation failures published in-repo for audit.

## Why This Exists

Repository maintenance agents operate on hostile input and high-impact tools. Issue text can carry prompt injection, source trees can contain secrets, tests execute untrusted code, and remote writes can affect production repositories. Production use adds requirements beyond the agent loop:

- immutable task scope: tenant, repository, and commit
- deny-by-default tools with stage-aware permissions
- approval-envelope-bound human approval for remote writes
- isolated execution with bounded resources and network policy
- durable concurrency control and replay-safe side effects
- evaluation that distinguishes correctness, safety, retrieval, and cost

This repository implements those boundaries end to end.

## Implemented Guarantees

| Boundary | Implementation |
|---|---|
| Agent state | Strict Pydantic models and legal lifecycle transitions |
| Concurrency | Atomic enqueue, optimistic versions, leased claims, rotating fencing IDs |
| Retrieval | Lexical, dense, and symbol adapters with deterministic reciprocal rank fusion |
| Tool use | Tenant/repository/commit scope plus role and stage authorization |
| Remote writes | Human decision bound to the plan, target commit, declared files, verification commands, and exact tool scope |
| Patch safety | Exact-text proposals, approved-path enforcement, local diff rendering, and `git apply --check` preflight |
| Independent review | Gateway-collected Git diff, post-change source, acceptance criteria, and verification evidence |
| Commands | Argument arrays, executable allowlist, timeout, output limit, sanitized environment |
| Sandbox | Digest-pinned image, non-root user, read-only root, dropped capabilities, offline checks |
| Model output | Provider-specific structured JSON with strict local validation; Responses calls use `store=False`; diff hunk metadata is derived locally |
| Coding context | Gateway-only search/read requests with fixed round and tool-call ceilings |
| Evaluation | Concurrent suites, retries, provenance, baseline deltas, hard gates, deterministic replay |
| Privacy | Recursive redaction plus current-tree and reachable-history publication scanning |
| Browser surface | Same-origin console with CSP and in-memory-only bearer identity |

## Evaluation

### Evaluation harness

The Harness evaluates a versioned suite under a bounded concurrency limit. It preserves manifest order, retries only timeout and infrastructure failures, and records:

- immutable repository commit and dataset version
- provider, model, prompt, tool-schema, and policy versions
- deterministic seed and normalized environment fingerprint
- observation, attempts, failure category, latency, retrieval, calls, and tokens per case
- aggregate resolution, Recall@10, MRR, regressions, safety rate, and p50/p95 latency
- candidate-minus-baseline deltas
- individual release-gate checks and one final decision

Replay creates a new run for selected cases and never mutates the source evidence.

Run the included credential-free example:

```bash
.venv/bin/python -m repo_maintenance_agent.cli evaluate-suite \
  examples/evaluation/suite.json \
  examples/evaluation/observations.json \
  --json-report artifacts/evaluation/example.json \
  --markdown-report artifacts/evaluation/example.md \
  --candidate-label local-example
```

The command writes both reports before returning exit code `1` for a failed gate, which makes it usable as a CI release check.

### SWE-bench evidence labels

RepoAegis keeps generation and quality evidence separate:

- **one-shot generation**: a prediction was produced without prior official-test feedback;
- **officially resolved**: the official SWE-bench Docker harness passed all required tests;
- **feedback-assisted calibration**: a development rerun consumed a previous official failure;
- **frozen evaluation**: the CLI rejects development feedback and preserves the one-shot boundary.

Feedback-assisted calibration is useful for improving the agent loop, but it is not reported as a one-shot or frozen benchmark score.

### Evaluation campaign

Development iteration was conducted on a **200-instance subset** sampled from the full SWE-bench dataset (2,294 instances), with all Verified instances (500) excluded by unique ID to prevent data leakage. The iterative loop — run on the development subset, classify failures, patch the root cause, re-run — drove the generation rate from an initial <10% to the final result.

The final evaluation ran on a **200-instance subset** sampled from **SWE-bench Verified** (500 tasks), stratified by repository proportional to the Verified 500 distribution (seed 42), in **frozen** mode, judged by the **official SWE-bench Docker harness**:

| Metric | Value |
|--------|:-----:|
| Total instances | 200 |
| Successfully generated | 192 / 200 (96.0%) |
| Generation failed | 8 / 200 (4.0%) |
| **Officially resolved (end-to-end)** | **74 / 200 (37.0%)** |
| Officially resolved (conditional on generation) | 74 / 192 (38.5%) |

Top repos by end-to-end resolution: pydata 62.5%, astropy 55.6%, django 46.3% (44 resolved), matplotlib 38.5%, sympy 25.8%. Per-instance results, frozen task IDs, generation-failure reasons, grading progress, and checksums are published in `docs/evaluation-results/` for audit (manifest: `manifest.json`, aggregate: `aggregate.json`).

> **Note:** This is a single-subset result, not a leaderboard claim. No baseline improvement claim is made until an aligned paired baseline is published.

### Statistical rigor

Comparisons carry paired-bootstrap uncertainty instead of bare point deltas: `evaluation/significance.py` computes a reproducible 10,000-resample percentile interval (seed-fixed), labels the direction (improvement / regression / inconclusive), and a `resolution_statistical_significance` release gate fails on a significant regression and on inconclusive small-sample deltas. `wilson_ci()` and the exact `clopper_pearson_ci()` report honest intervals for small binary results, and `required_n_for_power()` makes the sample-size assumption explicit. Effect size is reported as `cohens_h()`, and family-wise multiple-comparison control uses `holm_adjust()`. Aggregate reports also expose a mean partial resolution ratio (`tests_passed_ratio`) and a cache-hit rate so cost is measured, not guessed.

### LLM-as-a-Judge and model matrix

`evaluation/judge.py` adds rubric-based LLM judging (independent judge gateway, per-criterion 1–5 scores, rerun consistency) alongside the deterministic harness, plus an agreement/disagreement rate between the two paradigms. `evaluation/model_matrix.py` runs the same suite across several models with aligned seeds, prints a cost-quality table, and computes pairwise deltas ready for the bootstrap gate.

### Dual-track evaluation, Inspect alignment, and red-team evaluation

Evaluation runs on two tracks that feed one gate: a fast self-hosted harness for CI/iteration, and the UK AISI Inspect framework for authoritative runs.

- **Self-hosted harness (CI, seconds, no model calls)**: deterministic-fixture eval smoke gate (`.github/workflows/eval-smoke.yml`) plus the full versioned suite — concurrent, resumable, replay-safe, with release gates.
- **Inspect alignment (authoritative)**: `repo_maintenance_agent/inspect/` provides the bridge as a **scaffold** — dataset conversion, a SWE-bench progress scorer, a `.eval` log parser, and an agent-bridge skeleton — so official runs can reuse the industry-standard framework and baselines. The bridge is a designed integration plan, not yet a shipped official submission; Inspect executes and scores, while statistical conclusions remain the single authority of the AegisEvo gates.
- **Red-team case set**: `examples/evaluation/redteam/` covers prompt-injection / unauthorized-tool / secret-exfiltration / path-traversal cases and asserts 100% deny-by-default interception — execution-time governance that attack-surface scanners do not provide.

## Related Work

The design builds on established work in agent benchmarking, hybrid retrieval, agent safety, model evaluation, and statistics:

- **Agent benchmarking.** SWE-bench frames issue resolution as a reproducible benchmark judged by a Docker harness [1], and SWE-bench Verified provides a human-validated 500-task subset [2]. RepoAegis reports official-harness results with per-instance evidence and separates one-shot generation from feedback-assisted calibration.
- **Hybrid retrieval.** Dense passage retrieval demonstrates the value of dense representations over sparse baselines [3]; reciprocal rank fusion (RRF) provides a deterministic, parameter-free fusion of multiple ranked lists [4]. RepoAegis combines lexical (BM25), symbol, and optional dense adapters with RRF, keeping fusion deterministic and auditable.
- **Agent safety.** Indirect prompt injection demonstrates that attacker-controlled retrieved content can compromise LLM-integrated applications [5]; InjecAgent formalizes a benchmark for such attacks against tool-integrated agents [6]. RepoAegis treats issue text, repository files, and model output as untrusted data and enforces deny-by-default tool authorization, approval-bound remote writes, and sandboxed execution.
- **Model evaluation.** LLM-as-a-judge establishes agreement and bias characteristics of LLM judges against human preferences [7]. RepoAegis pairs deterministic harness scores with rubric-based LLM judging and reports agreement between paradigms.
- **Statistics.** The bootstrap [8], Holm's sequentially rejective procedure [9], and the Wilson [10] and Clopper–Pearson [11] binomial intervals underpin the significance gates in `evaluation/significance.py`.
- **Evolutionary prompt optimization.** EvoPrompt shows that LLMs can drive evolutionary search over prompt strategies [12]; AegisEvo applies the same evidence-gated evolutionary discipline to agent-configuration genomes with paired-bootstrap significance plus a safety veto.

Positioning against adjacent tooling:

| Tool / line of work | Focus | RepoAegis / AegisEvo position |
|---|---|---|
| Inspect AI (UK AISI) | Authoritative agent evaluation harness | Ships an Inspect bridge scaffold so official runs reuse the standard framework; Inspect executes and scores, RepoAegis adds release gates, safety, and cost accounting |
| OpenAI Evals / DeepEval / promptfoo | LLM evaluation frameworks | Score model outputs; RepoAegis evaluates agent side effects (tools, sandbox, cost, safety) end to end |
| LangSmith / Braintrust | Eval + trace + gates for LLM applications | Gate prompt/model-call regression by threshold; AegisEvo gates agent-configuration genomes by paired bootstrap plus safety veto |
| MLflow / SageMaker Model Registry | Model-weight versioning and promotion | AegisEvo governs agent-configuration genomes (not weights) with content-addressed lineage and statistical gates |
| Garak / PyRIT / HarmBench | Attack-surface scanning | Complementary: probe the model attack surface; RepoAegis enforces deny-by-default execution-time boundaries |

### References

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

## Web Workbench (AI Full-Stack)

A React + Vite workbench that talks to the API and a hybrid-search code Q&A endpoint:

- **Code Q&A (hybrid retrieval)** `POST /v1/chat`: BM25 + symbol hybrid retrieval over the repo, cited answers via an OpenAI-compatible model (DeepSeek), reference paths/line ranges returned.
- **Task console** `/v1/tasks`: list, create, and inspect repository maintenance tasks.
- **Evaluation dashboard** `/v1/evaluations/runs`: evaluation runs and release gates.

Build the frontend and serve it:

```bash
cd web
npm --registry=https://registry.npmmirror.com install
npm run build          # outputs web/dist
```

Set `REPO_AGENT_CHAT_REPO_ROOT` to a repo checkout to enable code Q&A. The chat engine is `repo_maintenance_agent/chat.py`; retrieval lives in `search/index.py` (BM25/symbol/vector) and `search/embeddings.py`.

## Quick Start

Requirements:

- Python 3.12
- Git
- Docker for sandbox and image execution

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev,postgres,observability]"
.venv/bin/python -m pytest --cov=repo_maintenance_agent --cov-report=term-missing
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy src
```

Start the local API with a development-only identity:

```bash
export REPO_AGENT_API_TOKENS='{"local-api-token":{"tenant_id":"tenant-local","subject":"local-reviewer"}}'
export REPO_AGENT_ENVIRONMENT='development'
.venv/bin/python -m uvicorn repo_maintenance_agent.main:build_application --factory
```

Open:

- Operations console: `http://127.0.0.1:8000/console`
- OpenAPI: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`

The console requests the bearer identity after load and keeps it only in JavaScript memory. It does not use cookies, local storage, session storage, or URL parameters.

## CLI

Set the API identity only in the current process:

```bash
export REPO_AGENT_API_TOKEN='local-api-token'

repo-agent run owner/repository aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "Fix empty config"
repo-agent status TASK_ID
repo-agent approve TASK_ID PLAN_HASH --reason "Reviewed scope and verification plan"
repo-agent resume TASK_ID PLAN_HASH --reason "Approved for sandbox execution"
repo-agent cancel TASK_ID
```

`status` returns the reviewable plan, deterministic risk and reasons, plan hash, evidence summaries, declared files, verification plan, and allowed tools. `approve` reads that envelope and submits its target commit and tool scope with the decision. The API rejects stale hashes, commits, or tool sets; any changed envelope requires a new decision. `approve --reject` records a rejection.

## API Surface

Authenticated task routes:

```text
POST /v1/tasks
GET  /v1/tasks
GET  /v1/tasks/{task_id}
POST /v1/tasks/{task_id}/approval
POST /v1/tasks/{task_id}/cancel
```

Task responses deliberately omit tenant identity and full retrieved content. Evidence summaries contain only source, locator, and bounded summary fields needed for review.

Authenticated evaluation routes:

```text
POST /v1/evaluations/runs
GET  /v1/evaluations/runs
GET  /v1/evaluations/runs/{run_id}
POST /v1/evaluations/runs/{run_id}/replay
GET  /v1/evaluations/runs/{run_id}/report.json
GET  /v1/evaluations/runs/{run_id}/report.md
```

Cross-tenant and unknown object IDs have the same 404 response. Public response models omit tenant identifiers and internal queue state.

## Local Infrastructure

The Compose profile defines the API, worker, PostgreSQL, OpenSearch, authenticated sandbox runner, and a project-owned rootless Docker daemon. The worker and daemon share no network; the runner is the only bridge, and no Docker socket or daemon port is exposed to the host. Exposed application ports bind to loopback. OpenSearch security is disabled only in this local profile.

```bash
export POSTGRES_PASSWORD='choose-a-local-password'
export REPO_AGENT_API_TOKENS='{"local-api-token":{"tenant_id":"tenant-local","subject":"local-reviewer"}}'
export SANDBOX_RUNNER_TOKEN='choose-a-separate-runner-token'
export REPO_AGENT_REPOSITORY_LOCATORS='{"owner/repository":"/operator/pinned/repository.git"}'
export REPO_AGENT_WORKER_TENANT_IDS='["tenant-local"]'
docker compose config
docker compose up --build
```

Application and task sandbox containers run as UID 10001 with a read-only root filesystem, dropped capabilities, `no-new-privileges`, and immutable base-image digests. The dedicated rootless daemon is isolated from worker and host sockets. Sandbox dependency setup is a separate auditable phase; test and lint phases run without network access. Compose syntax, isolation topology, image build, six-service startup, and one local submitted-task lifecycle are verified. Production availability, hostile multi-tenant operation, and capacity are not claimed.

## Configuration

| Variable | Purpose | Secret |
|---|---|---|
| `OPENAI_API_KEY` | Optional live OpenAI model calls | yes |
| `OPENAI_MODEL` | Model recorded and selected by the model gateway | no |
| `REPO_AGENT_API_TOKENS` | API bearer identities mapped to tenant and subject | yes |
| `REPO_AGENT_API_TOKEN` | CLI bearer identity | yes |
| `REPO_AGENT_API_URL` | CLI API URL | no |
| `REPO_AGENT_DATABASE_URL` | SQLAlchemy task and evaluation database | usually |
| `REPO_AGENT_ARTIFACT_ROOT` | Artifact storage root | no |
| `REPO_AGENT_WORKSPACE_ROOT` | Operator-owned task workspace root | no |
| `REPO_AGENT_REPOSITORY_LOCATORS` | Allowlisted repository source registry | usually |
| `REPO_AGENT_WORKER_TENANT_IDS` | Explicit worker tenant scope | no |
| `REPO_AGENT_SANDBOX_RUNNER_TOKEN` | Worker-to-runner bearer credential | yes |
| `REPO_AGENT_ALLOWED_HOSTS` | Trusted Host allowlist | no |
| `REPO_AGENT_MAX_ITERATIONS` | Bounded graph correction budget | no |

The application never loads a repository `.env` file. `.env.example` contains names and blank placeholders only. Production credentials belong in a secret manager; GitHub access should use short-lived App installation tokens.

## Security Model

Issue text, repository files, model output, search results, test logs, and documentation are untrusted data. None can grant permissions. Every side effect crosses a typed adapter and the Tool Gateway.

Publication gate:

```bash
.venv/bin/python -m repo_maintenance_agent.security.scanner
```

The scanner checks tracked and non-ignored files plus all reachable Git history for credential shapes, private keys, personal Windows paths, and private proxy configuration.

## Repository Layout

```text
src/repo_maintenance_agent/
  agents/          typed specialist nodes and outputs
  api/             authenticated API and console routes
  console/         zero-build operations workspace
  domain/          framework-independent state and ports
  evaluation/      harness, aggregation, gates, reports, and persistence
  graph/           LangGraph construction and deterministic routing
  models/          model-provider boundary
  observability/   redacted traces and normalized metrics
  policies/        tool authorization and recursive redaction
  sandbox/         language profiles and Docker verification
  search/          routing, adapters, and rank fusion
  security/        privacy and credential scanner
  storage/         task state, queue leases, and artifacts
  tools/           Git, GitHub, Context7, patch, and process adapters
examples/          credential-free evaluation inputs
sandbox/           immutable worker image and seccomp profile
tests/             unit and integration contracts
```

## Documentation

- [Authoritative system design](RepoAegis_Design.md)
- [Threat model](docs/threat-model.md)
- [Security best-practices report](security_best_practices_report.md)

## Joint Governance Flow With AegisEvo

RepoAegis and AegisEvo form one governed pipeline. RepoAegis executes repository maintenance tasks; AegisEvo is the search, evaluation, and promotion platform that drives the pinned RepoAegis runtime through a versioned, content-addressed target pack.

- **RepoAegis** executes real repository tasks: materialize the pinned commit → plan → approve → patch → container verification → review → commit/push → draft PR.
- **Target pack** freezes the RepoAegis commit, runtime source, images, and policy digests (`repoaegis-target-pack/v2`). The separate SWE-bench protocol binds task IDs, model, orchestration metadata, and token-accounting policy.
- **AegisEvo** consumes the target pack through the versioned `repoaegis-http-v1` adapter, runs equal-budget baseline / random / evolution search, and reports resolution, safety, usage, and latency evidence (`evaluation-observation/v1`).
- **Controlled promotion** requires absolute quality, statistical significance, zero safety regression, budget compliance, and human approval. A new RepoAegis release creates a new target pack instead of overwriting the previous one.

### Version Compatibility

| RepoAegis | Target pack | AegisEvo | Contract |
|---|---|---|---|
| `978d24e` (evaluated revision) | `repoaegis-target-pack/v2` (`repoaegis-v2`) | `ed1f445` (evaluated revision) | `repoaegis-http-v1` adapter + `evaluation-observation/v1` |

Cross-language digest checks and the live joint demo verify runtime compatibility: AegisEvo drives a real RepoAegis task to `completed`. That historical demo did not prove task resolution; only an official verifier report may establish `resolved`. See [AegisEvo](https://github.com/ETOLucy/AegisEvo) for the evaluation side.

## License

Apache License 2.0. See [LICENSE](LICENSE).
