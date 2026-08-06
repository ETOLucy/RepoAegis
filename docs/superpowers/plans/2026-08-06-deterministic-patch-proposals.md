# Deterministic Patch Proposals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make model patch proposals deterministic by accepting exact-text edits and generating Git-compatible unified diffs locally.

**Architecture:** `PatchProposal` carries validated repository-relative exact-text edits. A pure renderer consumes current file contents, validates uniqueness and overlap, and emits diff bytes; the coding node obtains those contents through `read_files` and sends only rendered artifacts through the existing `GitPatchApplier` boundary.

**Tech Stack:** Python 3.12, Pydantic v2, standard-library `difflib`, pytest, Ruff, MyPy

## Global Constraints

- Do not make a paid model call until all local quality gates pass.
- Proposed paths must be a subset of the files approved in the plan.
- Existing-file `old_text` must be non-empty and occur exactly once.
- Creation uses `old_text = null` and requires a missing target.
- File deletion and binary edits are out of scope.
- Preserve artifact storage, `git apply --check`, internal review, and official SWE-bench grading.
- Every future private cost record includes Asia/Shanghai date/time, model revision, hit/miss/output tokens, dated rates, computed provider cost, and evidence precision; public evidence publishes token usage only.

---

### Task 1: Exact-edit schema and pure renderer

**Files:**
- Modify: `src/repo_maintenance_agent/agents/schemas.py`
- Create: `src/repo_maintenance_agent/agents/patches.py`
- Create: `tests/unit/agents/test_patches.py`

**Interfaces:**
- Produces: `PatchEdit(path: str, old_text: str | None, new_text: str)`.
- Produces: `PatchProposal(summary: str, edits: list[PatchEdit])`.
- Produces: `RenderedPatch(data: bytes, changed_files: tuple[str, ...])`.
- Produces: `render_patch(proposal, *, current_files, declared_files) -> RenderedPatch`.

- [ ] **Step 1: Write schema and renderer failure tests**

Add literal fixtures proving unsafe paths, empty existing-file search text,
undeclared paths, absent and duplicate old text, overlapping ranges, duplicate
creation, an existing creation target, and no-op replacements are rejected.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python -m pytest tests/unit/agents/test_patches.py -q`

Expected: collection fails because `PatchEdit` and `render_patch` do not exist.

- [ ] **Step 3: Implement the minimal models and validator**

Use a Pydantic field validator to normalize backslashes to `/` and reject
absolute paths, `..`, leading `-`, tabs, and newlines. Use a model validator to
require non-empty `old_text` for replacement and reject equal old/new text.

- [ ] **Step 4: Implement deterministic edit application**

For each path, find every `old_text` span against the original content, require
one match, reject intersecting spans, apply replacements in descending offset
order, and render one diff section per sorted path. Include `diff --git`,
`---`/`+++`, new-file mode, and `\\ No newline at end of file` markers.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run: `python -m pytest tests/unit/agents/test_patches.py -q`

- [ ] **Step 6: Commit**

Run: `git add src/repo_maintenance_agent/agents/schemas.py src/repo_maintenance_agent/agents/patches.py tests/unit/agents/test_patches.py && git commit -m "feat: render exact patch proposals"`

### Task 2: Coding-node integration and bounded retry

**Files:**
- Modify: `src/repo_maintenance_agent/agents/nodes.py`
- Modify: `tests/unit/agents/test_nodes.py`
- Modify: `tests/integration/test_swebench_runner.py`

**Interfaces:**
- Consumes: `render_patch(...) -> RenderedPatch` from Task 1.
- Preserves: `apply_patch` artifact call with derived `changed_files`.

- [ ] **Step 1: Convert fake models to exact edits**

Update existing `PatchProposal` fixtures to replace the literal
`def load(): return default` text with `def load(): return safe_default`, while
keeping expected end-to-end diffs unchanged.

- [ ] **Step 2: Write a failing source-read integration test**

Assert coding calls `read_files` for the proposal paths before `apply_patch`,
stores a `text/x-diff` artifact produced locally, and passes renderer-derived
paths to the gateway.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `python -m pytest tests/unit/agents/test_nodes.py tests/integration/test_swebench_runner.py -q`

Expected: coding still reads `unified_diff` directly and does not perform the
proposal-path source read.

- [ ] **Step 4: Integrate rendering into coding**

Prompt for exact edits only. Read sorted proposed paths with the scoped gateway,
call `render_patch`, store `RenderedPatch.data`, and apply using
`RenderedPatch.changed_files`. Convert validation errors to
`ToolExecutionError` so the existing bounded retry path can handle them.

- [ ] **Step 5: Add deterministic retry coverage**

Make the first proposal use absent old text and the second use current exact
text. Assert the first failure never reaches `apply_patch`, refreshed source and
the error appear in the second prompt, and exactly one rendered patch is
applied.

- [ ] **Step 6: Run focused tests and confirm GREEN**

Run: `python -m pytest tests/unit/agents/test_nodes.py tests/integration/test_swebench_runner.py -q`

- [ ] **Step 7: Commit**

Run: `git add src/repo_maintenance_agent/agents/nodes.py tests/unit/agents/test_nodes.py tests/integration/test_swebench_runner.py && git commit -m "feat: build patches from exact edits"`

### Task 3: Quality gates, documentation, and frozen calibration protocol

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/architecture.md`
- Modify: `docs/evaluation.md`
- Modify: `docs/claim-to-evidence.md`
- Modify: private protocol and cost ledger outside the public repository

**Interfaces:**
- Documents: model proposal, local rendering, patch application, and official resolution as separate stages.
- Produces: a calibration protocol bound to the new RepoAegis commit and exact cost fields.

- [ ] **Step 1: Update public documentation**

Replace claims that the model emits unified diffs with the exact-edit contract,
local rendering checks, and unchanged Git/SWE-bench authority boundaries.

- [ ] **Step 2: Run the full local quality gates**

Run: `python -m pytest -q --cov=repo_maintenance_agent --cov-report=term-missing --cov-fail-under=80`

Run: `python -m ruff check .`

Run: `python -m mypy --strict src`

Run: `python scripts/validate_logo_assets.py docs`

- [ ] **Step 3: Commit the implementation documentation**

Run: `git add README.md README.zh-CN.md docs/architecture.md docs/evaluation.md docs/claim-to-evidence.md && git commit -m "docs: explain deterministic patch rendering"`

- [ ] **Step 4: Rebind private evaluation protocol**

Record the exact RepoAegis commit and protocol SHA-256. Preserve prior failed
runs and classify any interrupted response without usage as cost-unknown rather
than exact zero.

- [ ] **Step 5: Perform at most one paid generation**

Use the authorized public SWE-bench issue, public Sphinx source, and private
failure summary. Keep the DeepSeek credential process-local and use the
operator's process-local network configuration. Persist exact dated usage and cost.

- [ ] **Step 6: Run official grading only for a produced prediction**

Use the existing official image
`swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-8548:latest`. Record
FAIL_TO_PASS and PASS_TO_PASS separately; do not label generation, application,
or review as resolution.

- [ ] **Step 7: Commit only public repository changes**

Do not push. Private protocol, keys, balance data, and evaluation working files
remain outside the public repository.
