# Evaluation Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reproducible evaluation harness, tenant-scoped evaluation APIs, a zero-build
operations console, and official project documentation.

**Architecture:** Strict Pydantic evaluation models and an async service own scheduling,
aggregation, comparison, replay, and exports. In-memory and SQL repositories sit behind a typed
port. FastAPI exposes authenticated APIs and package-owned static console assets.

**Tech Stack:** Python 3.12, Pydantic 2, FastAPI, SQLAlchemy 2, asyncio, vanilla HTML/CSS/JavaScript,
pytest, Playwright.

## Global Constraints

- Preserve Python as the control plane and avoid a Node build dependency.
- Scope every run operation by authenticated tenant.
- Keep browser tokens in memory only.
- Never persist or print provider credentials.
- Maintain at least 80% branch coverage and strict Ruff/mypy checks.
- Keep exports deterministic, bounded, and privacy-safe.

---

### Task 1: Evaluation Domain And Aggregation

**Files:**
- Modify: `src/repo_maintenance_agent/evaluation/models.py`
- Create: `src/repo_maintenance_agent/evaluation/aggregate.py`
- Create: `src/repo_maintenance_agent/evaluation/reports.py`
- Test: `tests/unit/evaluation/test_harness_models.py`
- Test: `tests/unit/evaluation/test_aggregate.py`

**Interfaces:**
- Produces: strict suite, run, case-result, aggregate, comparison, and gate models.
- Produces: `aggregate_results`, `compare_aggregates`, `evaluate_gates`,
  `render_markdown_report`.

- [ ] Write validation and aggregation tests that import the desired interfaces.
- [ ] Run the focused tests and confirm missing-interface failures.
- [ ] Implement strict domain models and deterministic aggregation.
- [ ] Implement explicit missing-baseline comparison and release gates.
- [ ] Implement deterministic JSON-compatible and Markdown reports.
- [ ] Run focused tests, Ruff, and mypy.

### Task 2: Concurrent Harness Service

**Files:**
- Create: `src/repo_maintenance_agent/evaluation/harness.py`
- Test: `tests/unit/evaluation/test_harness.py`

**Interfaces:**
- Consumes: domain and aggregation interfaces from Task 1.
- Produces: `CaseExecutor` protocol and `EvaluationHarness.run` / `replay`.

- [ ] Write async tests for concurrency limits, stable ordering, timeout retry, terminal failures,
  provenance, and replay selection.
- [ ] Run focused tests and confirm missing-interface failures.
- [ ] Implement semaphore scheduling, bounded attempts, classifications, and immutable replay.
- [ ] Run focused tests, Ruff, and mypy.

### Task 3: Evaluation Persistence

**Files:**
- Create: `src/repo_maintenance_agent/evaluation/storage.py`
- Modify: `src/repo_maintenance_agent/storage/sql.py`
- Test: `tests/unit/evaluation/test_storage.py`
- Test: `tests/integration/storage/test_evaluation_sql.py`

**Interfaces:**
- Produces: `EvaluationRepository`, `InMemoryEvaluationRepository`, and
  `SqlEvaluationRepository`.

- [ ] Write tenant isolation, ordering, limit, round-trip, and optimistic-version tests.
- [ ] Run tests and confirm missing repositories fail.
- [ ] Implement in-memory and SQL adapters using tenant/run composite identity.
- [ ] Run focused tests, Ruff, and mypy.

### Task 4: Evaluation And Task APIs

**Files:**
- Modify: `src/repo_maintenance_agent/api/schemas.py`
- Modify: `src/repo_maintenance_agent/api/app.py`
- Modify: `src/repo_maintenance_agent/main.py`
- Test: `tests/integration/api/test_evaluations.py`
- Modify: `tests/integration/api/test_tasks.py`

**Interfaces:**
- Consumes: evaluation repository and harness.
- Produces: run create/list/detail/replay/export routes and bounded task list.

- [ ] Write authentication, tenant isolation, strict input, ordering, replay, export, and task-list
  tests.
- [ ] Run focused tests and confirm route failures.
- [ ] Implement dependency-injected routes and safe response schemas.
- [ ] Wire SQL evaluation storage in the application factory.
- [ ] Run focused tests, Ruff, and mypy.

### Task 5: Lightweight Operations Console

**Files:**
- Create: `src/repo_maintenance_agent/console/index.html`
- Create: `src/repo_maintenance_agent/console/app.css`
- Create: `src/repo_maintenance_agent/console/app.js`
- Modify: `src/repo_maintenance_agent/api/app.py`
- Modify: `pyproject.toml`
- Test: `tests/integration/api/test_console.py`

**Interfaces:**
- Consumes: `/v1/evaluations/runs`, `/v1/tasks`, replay and report endpoints.
- Produces: `/console`, `/console/app.css`, and `/console/app.js`.

- [ ] Write tests for routes, content types, CSP, no token persistence APIs, and production
  availability.
- [ ] Run tests and confirm missing assets fail.
- [ ] Implement the accessible desktop/mobile operations workspace and all loading/error/empty
  states.
- [ ] Package static assets in wheel output.
- [ ] Run focused tests, Ruff, and mypy.

### Task 6: Demonstration Data And CLI

**Files:**
- Create: `examples/evaluation/suite.json`
- Create: `examples/evaluation/observations.json`
- Modify: `src/repo_maintenance_agent/cli.py`
- Modify: `tests/unit/test_cli.py`

**Interfaces:**
- Produces: `repo-agent evaluate-suite` for deterministic local suite reports.

- [ ] Write CLI tests for suite execution, report export, invalid observations, and gate exit code.
- [ ] Run tests and confirm command absence.
- [ ] Implement deterministic file-backed suite execution without live provider credentials.
- [ ] Add privacy-safe example fixtures.
- [ ] Run focused tests, Ruff, and mypy.

### Task 7: Documentation And Visual Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `RepoAegis_Design.md`
- Create: `docs/images/evaluation-console.png`

**Interfaces:**
- Documents the implemented runtime, harness, console, and verified commands.

- [ ] Rewrite README around product, proof, quick start, harness, console, security, and docs.
- [ ] Update architecture and authoritative design to match the implementation.
- [ ] Start the development API with non-secret demo identity.
- [ ] Use Playwright to inspect desktop and mobile console layouts, interactions, and console errors.
- [ ] Capture and add the verified desktop screenshot.
- [ ] Run Markdown privacy and whitespace checks.

### Task 8: Release And Private Beginner Guide

**Files:**
- Modify: `.gitignore`
- Create locally only: `private/README_BEGINNER_GUIDE.md`

**Interfaces:**
- Produces a privacy-clean GitHub release and an ignored local learning guide.

- [ ] Add `private/` to `.gitignore` before creating the guide.
- [ ] Run full pytest coverage, Ruff, mypy, YAML/JSON parsing, wheel build, dependency audit,
  Compose validation, and privacy scan.
- [ ] Review the staged diff for security and documentation consistency.
- [ ] Commit and push through the process-scoped GitHub proxy.
- [ ] Wait for GitHub CI, inspect failures, and fix until successful.
- [ ] Create a detailed zero-background Chinese guide under ignored `private/`.
- [ ] Verify `git check-ignore private/README_BEGINNER_GUIDE.md` and confirm a clean tracked tree.
