# Runtime Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make API-created tasks durably claimable and executable by a separately assembled RepoAegis worker.

**Architecture:** A shared composition root constructs SQL repositories, queue, and an explicit executor boundary. SQL task creation atomically creates queue work; a separate worker process consumes it. Observable integration behavior is implemented test-first, while recovery limitations stay explicit.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, LangGraph, pytest, Docker Compose

## Global Constraints

- Preserve existing user work and current ports.
- Write and observe each failing test before production code.
- Update the detailed operations documentation and claim matrix with behavior changes.
- Do not claim atomic save/ack recovery in this increment.
- Do not expose credentials, API domains, proxy values, or connection metadata.

---

### Task 1: Shared Runtime Composition

**Files:**
- Create: `src/repo_maintenance_agent/runtime.py`
- Modify: `src/repo_maintenance_agent/main.py`
- Test: `tests/integration/test_runtime.py`
- Test: `tests/unit/test_main.py`

**Interfaces:**
- Consumes: `Settings`, SQL engine, `SqlTaskRepository`, `SqlTaskQueue`, and `SqlEvaluationRepository`.
- Produces: immutable `RuntimeComponents` whose repository and queue share one engine.

- [x] Write an integration test that posts a task through the runtime API and claims its exact ID from the runtime SQL queue.
- [x] Run the focused test and confirm it fails because the shared runtime factory is missing.
- [x] Implement the immutable component factory and route `build_application` through it.
- [x] Run the focused integration test and application factory tests.

### Task 2: Graph Executor Assembly

**Files:**
- Modify: `src/repo_maintenance_agent/runtime.py`
- Test: `tests/unit/test_runtime.py`

**Interfaces:**
- Consumes: existing agent nodes, graph builder, and `LangGraphExecutor`.
- Produces: a validated executor factory boundary used by the worker service.

- [x] Write a test that injects a deterministic executor and proves the runtime exposes it unchanged.
- [x] Run it and confirm the factory has no executor boundary.
- [x] Add the minimal executor dependency without constructing model infrastructure at import time.
- [x] Run runtime tests.

### Task 3: Worker Service Entry Point

**Files:**
- Create: `src/repo_maintenance_agent/worker_service.py`
- Modify: `src/repo_maintenance_agent/config.py`
- Modify: `pyproject.toml`
- Test: `tests/unit/test_worker_service.py`

**Interfaces:**
- Consumes: `RuntimeComponents`, worker ID, tenant scope, bounded poll interval.
- Produces: `run_worker_once(settings, runtime=...) -> WorkerOutcome`; the polling CLI follows the
  per-task workspace and production graph factory so Compose never starts with a fake executor.

- [x] Write a test that uses real queue/repository components and a deterministic executor to persist the next task state.
- [x] Run it and confirm the worker service entry point is missing.
- [x] Implement one-shot execution with required executor and tenant-scope validation.
- [x] Run worker service and existing worker tests.
- [ ] Implement the polling command only after the production graph factory owns a per-task workspace.

### Task 4: Compose And Documentation

**Files:**
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/claim-to-evidence.md`
- Create: `docs/operations/runtime.md`
- Test: `tests/unit/test_compose_contract.py`

**Interfaces:**
- Consumes: the worker CLI and shared environment contract.
- Produces: a separately runnable worker service and detailed operator tutorial.

- [ ] Write a behavior-oriented Compose contract test that parses YAML and requires a portless worker using the worker command.
- [ ] Run it and confirm the service is absent.
- [ ] Add the worker service with the same database/artifact dependencies and hardened container settings.
- [ ] Document startup, task submission, state inspection, shutdown, failure behavior, and current recovery limitation.
- [ ] Run Compose validation, documentation tests, full pytest, Ruff, and strict MyPy.
- [ ] Update the claim matrix with exact implementation, tests, runtime evidence, and remaining gaps.
- [ ] Commit the verified increment without unrelated files.

## Self-Review

The plan covers the approved composition increment only. Interfaces use existing task and queue
identities consistently. No placeholder implementation or unsupported completion claim is present.
