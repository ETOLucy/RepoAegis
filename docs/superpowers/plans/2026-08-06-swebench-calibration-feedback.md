# SWE-bench Calibration Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auditable, calibration-only path that feeds a prior official SWE-bench failure into RepoAegis and binds that feedback to saved generation evidence.

**Architecture:** Keep official `SWEbenchTask` input unchanged. Parse feedback into a separate strict model, attach it to the in-memory task state after research, expose it to planning/coding/review, and persist its canonical digest with the prediction evidence. Gate the CLI so frozen runs cannot consume feedback.

**Tech Stack:** Python 3.12, Pydantic v2, Typer, pytest, Ruff, MyPy

## Global Constraints

- Feedback is accepted only for `calibration` and `development` roles.
- The `frozen` role rejects feedback before any model call.
- Feedback must not contain a gold patch or SWE-bench `test_patch`.
- Feedback summaries are bounded to 10,000 characters and test IDs to 100 entries.
- Feedback-assisted results must remain distinguishable from one-shot results.

---

### Task 1: Strict feedback model and digest

**Files:**
- Modify: `src/repo_maintenance_agent/evaluation/swebench_runner.py`
- Test: `tests/integration/test_swebench_runner.py`

**Interfaces:**
- Produces: `SWEbenchDevelopmentFeedback` with `digest() -> str`.
- Produces: `SWEbenchGenerationEvidence.development_feedback_digest`.

- [ ] **Step 1: Write failing schema and digest tests**

Add tests that construct a bounded feedback record, assert a stable `sha256:` digest, and assert that `patch`, `test_patch`, `FAIL_TO_PASS`, and `PASS_TO_PASS` are rejected as extra fields.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m pytest tests/integration/test_swebench_runner.py -q`

Expected: collection/import failure because `SWEbenchDevelopmentFeedback` does not exist.

- [ ] **Step 3: Implement the strict model**

Define the model beside `SWEbenchTask` with fields `instance_id`, `source_run_id`, `prediction_digest`, `official_report_digest`, `failing_tests`, and `summary`. Canonicalize `model_dump(mode="json")` with sorted compact JSON and return a prefixed SHA-256 digest.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest tests/integration/test_swebench_runner.py -q`

- [ ] **Step 5: Commit**

Run: `git add src/repo_maintenance_agent/evaluation/swebench_runner.py tests/integration/test_swebench_runner.py && git commit -m "feat: model SWE-bench calibration feedback"`

### Task 2: Feed feedback through the agent graph

**Files:**
- Modify: `src/repo_maintenance_agent/evaluation/swebench_runner.py`
- Modify: `src/repo_maintenance_agent/agents/nodes.py`
- Test: `tests/integration/test_swebench_runner.py`

**Interfaces:**
- Consumes: `SWEbenchDevelopmentFeedback`.
- Produces: `PatchAgent.run(task, workspace, ledger, development_feedback=None)`.

- [ ] **Step 1: Write a failing prompt-propagation test**

Capture model inputs for `PlanOutput`, `ContextRequest`, `PatchProposal`, and `ReviewOutput`; run a fixture task with feedback and assert the feedback record is present in every post-research prompt.

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `python -m pytest tests/integration/test_swebench_runner.py::test_calibration_feedback_reaches_planning_coding_and_review -q`

Expected: `GitSWEbenchRuntime` or `PatchAgent.run` rejects the missing feedback argument.

- [ ] **Step 3: Implement minimal propagation**

Store feedback in `RepoTaskState.repo_profile["development_feedback"]` after research and add it to coding context selection, patch generation, and review payloads. Do not add it to the public issue or search query.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest tests/integration/test_swebench_runner.py -q`

- [ ] **Step 5: Commit**

Run: `git add src/repo_maintenance_agent/evaluation/swebench_runner.py src/repo_maintenance_agent/agents/nodes.py tests/integration/test_swebench_runner.py && git commit -m "feat: route calibration feedback through review"`

### Task 3: Bind feedback to resumable evidence

**Files:**
- Modify: `src/repo_maintenance_agent/evaluation/swebench_runner.py`
- Test: `tests/integration/test_swebench_runner.py`

**Interfaces:**
- Consumes: `RuntimeExecutor.development_feedback_digest(instance_id) -> str | None`.
- Produces: saved evidence that cannot resume under a different feedback digest.

- [ ] **Step 1: Write a failing resume-mismatch test**

Generate evidence with feedback, then attempt resume with a different feedback summary and assert `ValueError` contains `saved SWE-bench evidence does not match this run`.

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `python -m pytest tests/integration/test_swebench_runner.py::test_prediction_evidence_rejects_changed_development_feedback -q`

- [ ] **Step 3: Implement digest persistence and validation**

Record the runtime feedback digest when creating `SWEbenchGenerationEvidence` and compare it in `_validate_resume`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest tests/integration/test_swebench_runner.py -q`

- [ ] **Step 5: Commit**

Run: `git add src/repo_maintenance_agent/evaluation/swebench_runner.py tests/integration/test_swebench_runner.py && git commit -m "feat: bind feedback to generation evidence"`

### Task 4: CLI parsing and role gate

**Files:**
- Modify: `src/repo_maintenance_agent/cli.py`
- Test: `tests/unit/test_cli.py`

**Interfaces:**
- Produces: optional `--development-feedback PATH` on `swebench-generate`.
- Consumes: newline-delimited `SWEbenchDevelopmentFeedback` records.

- [ ] **Step 1: Write failing CLI tests**

Assert duplicate instance IDs fail, feedback for an unselected task fails, and `--role frozen --development-feedback ...` fails before model configuration is used.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m pytest tests/unit/test_cli.py -q`

- [ ] **Step 3: Implement parser and role gate**

Parse strict JSONL, require exact selected-task membership, reject feedback for `frozen`, and pass the mapping to `GitSWEbenchRuntime`.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest tests/unit/test_cli.py tests/integration/test_swebench_runner.py -q`

- [ ] **Step 5: Commit**

Run: `git add src/repo_maintenance_agent/cli.py tests/unit/test_cli.py && git commit -m "feat: gate calibration feedback at CLI"`

### Task 5: Full verification and documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`

**Interfaces:**
- Documents: one-shot versus feedback-assisted result labels and CLI usage.

- [ ] **Step 1: Add concise integrity documentation**

Document the feedback JSONL fields, role restriction, evidence digest binding, and the rule that feedback-assisted calibration is not a frozen score.

- [ ] **Step 2: Run all quality gates**

Run: `python -m pytest -q --cov=repo_maintenance_agent --cov-report=term-missing --cov-fail-under=80`

Run: `python -m ruff check .`

Run: `python -m mypy --strict src tests`

Run: `python scripts/validate_logo_assets.py docs`

Run: `rg -n "feedback-assisted|one-shot|frozen" README.md docs/README.md`

- [ ] **Step 3: Commit**

Run: `git add README.md docs/README.md && git commit -m "docs: explain feedback-assisted calibration"`
