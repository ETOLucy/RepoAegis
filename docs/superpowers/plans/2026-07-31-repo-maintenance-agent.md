# RepoAegis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-oriented repository maintenance agent that turns a GitHub issue into an evidence-backed patch or approved draft pull request.

**Architecture:** A Python control plane uses FastAPI, LangGraph, typed domain state, ports, and adapters. Repository code executes only through a language-independent sandbox contract. Hybrid retrieval combines lexical, semantic, symbol, and Git history candidates using tenant- and commit-scoped filters.

**Tech Stack:** Python 3.12, FastAPI 0.128+, Pydantic 2.12+, LangGraph 1.x, SQLAlchemy 2.x, PostgreSQL, OpenSearch, Docker, GitHub CLI, OpenTelemetry, pytest, Ruff, mypy.

## Global Constraints

- Python control-plane modules must not execute repository commands directly; all execution crosses a policy-enforcing tool gateway.
- Every task state, cache key, artifact, and search query is scoped by `tenant_id`, `repo_id`, and immutable `commit_sha`.
- Remote writes, risky paths, dependency changes, and privileged files require explicit approval.
- Secrets are accepted only through runtime environment/configuration, represented by references, redacted from logs, and never committed.
- Production commands use argument arrays with `shell=False`; paths are resolved and verified inside the assigned workspace.
- Core domain logic remains independent from LangGraph, FastAPI, OpenSearch, Docker, and GitHub CLI.

---

### Task 1: Project Foundation and Domain Contracts

**Files:**
- Create: `pyproject.toml`
- Create: `src/repo_maintenance_agent/domain/models.py`
- Create: `src/repo_maintenance_agent/domain/errors.py`
- Create: `src/repo_maintenance_agent/domain/ports.py`
- Test: `tests/unit/domain/test_models.py`

**Interfaces:**
- Produces: `RepoTaskState`, `TaskStatus`, `RiskLevel`, `Evidence`, `ToolCall`, `ToolResult`, `SearchQuery`, `SearchHit`, `VerificationResult`, and port protocols.

- [ ] Write tests proving strict tenant/repository/commit validation and legal state transitions.
- [ ] Run the tests and verify they fail because the domain package is absent.
- [ ] Implement immutable Pydantic value objects and typed port protocols.
- [ ] Run the tests and verify they pass.

### Task 2: Policy Engine and Tool Gateway

**Files:**
- Create: `src/repo_maintenance_agent/policies/permissions.py`
- Create: `src/repo_maintenance_agent/policies/redaction.py`
- Create: `src/repo_maintenance_agent/tools/gateway.py`
- Test: `tests/unit/tools/test_gateway.py`
- Test: `tests/unit/policies/test_redaction.py`

**Interfaces:**
- Consumes: domain tool contracts.
- Produces: `PermissionPolicy.authorize(call, state)` and `ToolGateway.execute(call, state)`.

- [ ] Write failing tests for tenant mismatch, approval-gated tools, path traversal, secret redaction, timeouts, and idempotent write replay.
- [ ] Implement deny-by-default permissions, workspace containment, redaction, timeout handling, operation logging, and idempotency.
- [ ] Run focused and full unit tests.

### Task 3: Hybrid Search

**Files:**
- Create: `src/repo_maintenance_agent/search/models.py`
- Create: `src/repo_maintenance_agent/search/router.py`
- Create: `src/repo_maintenance_agent/search/fusion.py`
- Create: `src/repo_maintenance_agent/search/service.py`
- Create: `src/repo_maintenance_agent/search/adapters/local.py`
- Create: `src/repo_maintenance_agent/search/adapters/opensearch.py`
- Test: `tests/unit/search/test_fusion.py`
- Test: `tests/integration/search/test_local_search.py`

**Interfaces:**
- Produces: `SearchRouter.route(query)`, `reciprocal_rank_fusion(result_sets)`, and `HybridSearchService.search(query)`.

- [ ] Write failing tests for RRF ordering, duplicate collapse, immutable commit filters, query routing, and local lexical retrieval.
- [ ] Implement local retrieval and an OpenSearch adapter that emits a tenant/repository/commit-scoped hybrid query.
- [ ] Run search unit and integration tests.

### Task 4: Repository, GitHub, and Context7 Adapters

**Files:**
- Create: `src/repo_maintenance_agent/tools/process.py`
- Create: `src/repo_maintenance_agent/tools/git.py`
- Create: `src/repo_maintenance_agent/tools/github.py`
- Create: `src/repo_maintenance_agent/tools/context7.py`
- Test: `tests/unit/tools/test_process.py`
- Test: `tests/integration/tools/test_git.py`

**Interfaces:**
- Produces: safe argument-array process execution, read-only Git operations, approval-gated GitHub writes, and a documentation port.

- [ ] Write failing tests proving no shell interpolation, repository containment, output limits, timeouts, and structured Git output.
- [ ] Implement adapters with allowlisted commands and sanitized environment inheritance.
- [ ] Run adapter tests.

### Task 5: Sandbox and Environment Profiles

**Files:**
- Create: `src/repo_maintenance_agent/sandbox/profiles.py`
- Create: `src/repo_maintenance_agent/sandbox/selector.py`
- Create: `src/repo_maintenance_agent/sandbox/docker.py`
- Create: `sandbox/Dockerfile.python`
- Create: `sandbox/seccomp.json`
- Test: `tests/unit/sandbox/test_profiles.py`
- Test: `tests/unit/sandbox/test_docker_command.py`

**Interfaces:**
- Produces: `EnvironmentProfiler.inspect(workspace)` and `DockerSandbox.build_command(spec)`.

- [ ] Write failing tests for lockfile-based profile detection and hardened Docker arguments.
- [ ] Implement deterministic environment profiles, baseline/test phases, resource limits, read-only root filesystem, no-network default, and non-root execution.
- [ ] Run sandbox contract tests and validate Dockerfiles.

### Task 6: Agent Nodes and Graph Routing

**Files:**
- Create: `src/repo_maintenance_agent/agents/*.py`
- Create: `src/repo_maintenance_agent/graph/state.py`
- Create: `src/repo_maintenance_agent/graph/routes.py`
- Create: `src/repo_maintenance_agent/graph/builder.py`
- Test: `tests/unit/graph/test_routes.py`
- Test: `tests/integration/graph/test_workflow.py`

**Interfaces:**
- Produces nodes `intake`, `research`, `planning`, `approval`, `coding`, `verification`, `review`, `pr`, plus `build_graph(dependencies)`.

- [ ] Write failing tests for success, approval, verification retry, review retry, budget exhaustion, and environment-failure routes.
- [ ] Implement deterministic routers and dependency-injected node services.
- [ ] Build LangGraph with conditional edges and an in-memory test checkpointer.
- [ ] Run graph tests.

### Task 7: Persistence, Artifacts, and Operation Log

**Files:**
- Create: `src/repo_maintenance_agent/storage/memory.py`
- Create: `src/repo_maintenance_agent/storage/sql.py`
- Create: `src/repo_maintenance_agent/storage/artifacts.py`
- Create: `src/repo_maintenance_agent/storage/models.py`
- Test: `tests/unit/storage/test_memory.py`
- Test: `tests/integration/storage/test_sql.py`

**Interfaces:**
- Produces tenant-scoped task repositories, artifact metadata, operation log, and outbox records.

- [ ] Write failing tests for tenant isolation, optimistic version checks, idempotent operations, and artifact filename sanitization.
- [ ] Implement in-memory development stores and SQLAlchemy production mappings.
- [ ] Run storage tests.

### Task 8: Secure FastAPI and CLI Interfaces

**Files:**
- Create: `src/repo_maintenance_agent/api/app.py`
- Create: `src/repo_maintenance_agent/api/auth.py`
- Create: `src/repo_maintenance_agent/api/schemas.py`
- Create: `src/repo_maintenance_agent/cli.py`
- Test: `tests/integration/api/test_tasks.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Produces authenticated task create/read/cancel/approve endpoints and CLI commands `run`, `status`, `approve`, `evaluate`.

- [ ] Write failing tests for missing bearer auth, tenant object authorization, extra-field rejection, response shaping, and secure production docs settings.
- [ ] Implement router-level authentication, strict Pydantic schemas, request IDs, trusted hosts, generic error responses, and dependency-injected services.
- [ ] Run API and CLI tests.

### Task 9: Observability and Evaluation

**Files:**
- Create: `src/repo_maintenance_agent/observability/tracing.py`
- Create: `src/repo_maintenance_agent/observability/metrics.py`
- Create: `src/repo_maintenance_agent/evaluation/models.py`
- Create: `src/repo_maintenance_agent/evaluation/graders.py`
- Create: `src/repo_maintenance_agent/evaluation/runner.py`
- Test: `tests/unit/evaluation/test_graders.py`
- Test: `tests/unit/observability/test_tracing.py`

**Interfaces:**
- Produces privacy-safe structured events and executable grading metrics for resolution, retrieval, safety, cost, and latency.

- [ ] Write failing tests for deterministic grading and secret-free traces.
- [ ] Implement metric graders, JSON report output, and trace redaction.
- [ ] Run evaluation tests.

### Task 10: Deployment, Documentation, and Security Audit

**Files:**
- Create: `README.md`
- Create: `.env.example`
- Create: `docker-compose.yml`
- Create: `configs/*.yaml`
- Create: `docs/architecture.md`
- Create: `docs/threat-model.md`
- Create: `security_best_practices_report.md`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces a reproducible local stack, operations documentation, privacy review, CI, and interview-ready walkthrough.

- [ ] Add configuration and deployment artifacts without credentials or personal paths.
- [ ] Run test, lint, type, build, secret, dependency, and privacy scans.
- [ ] Review the implementation against `RepoAegis_Design.md`.
- [ ] Commit the verified tree and push `main` to `origin` through the approved local proxy.
