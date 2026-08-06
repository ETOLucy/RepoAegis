# Deterministic Patch Proposals Design

## Goal

Replace model-authored unified diffs with bounded exact-text edits so RepoAegis
can validate a proposal against the current workspace and generate the diff
locally. The existing artifact, policy, review, verification, and official
SWE-bench boundaries remain unchanged.

## Motivation

Three feedback-assisted generations for `sphinx-doc__sphinx-8548` received
current source context but produced unified diffs that failed to apply. The
failures persisted with `git apply --recount`, so another prompt-only retry
would spend evaluation budget without addressing the unreliable boundary:
model-authored hunk metadata and context.

## Proposal Schema

`PatchProposal` contains a summary and one to one hundred `PatchEdit` records.
Each edit contains:

- `path`: a safe repository-relative POSIX path;
- `old_text`: the exact non-empty current text to replace, or `null` only when
  creating a file that does not exist; and
- `new_text`: the replacement or new-file content.

The model no longer supplies a unified diff or a separate changed-file list.
Changed paths are derived from the edits, eliminating disagreement between
claimed paths and patch content. File deletion and binary edits are outside
this change; they can be designed separately if evaluation evidence requires
them.

## Deterministic Rendering

After receiving a proposal, the coding node reads every proposed path through
the existing scoped `read_files` gateway. A local renderer then:

1. rejects paths outside the approved plan;
2. requires existing-file `old_text` to occur exactly once in the current
   content;
3. rejects overlapping edits, duplicate file creation, missing creation
   targets, and no-op edits;
4. applies validated replacements in memory from the end of each file toward
   the beginning; and
5. emits a Git-compatible unified diff, including new-file headers and
   no-newline markers when needed.

The rendered bytes enter the existing private artifact store and are applied
by `GitPatchApplier`, which still performs declared-file checks and
`git apply --check` before mutation. The local renderer never writes directly
to the task workspace.

## Retry And Evidence Semantics

A deterministic validation failure becomes bounded `patch_feedback`. Before a
retry, the coding context is refreshed with the current contents of the
proposed and approved paths. Model-call usage, latency, failures, and patch
artifacts continue to be persisted by the existing evidence pipeline.

Model completion, rendered patch, applied patch, internal review approval, and
official SWE-bench resolution remain distinct states. No additional paid call
is allowed until the implementation passes the full local quality gates.

## Evaluation Cost Record

Every future paid call record must include the Asia/Shanghai date and time,
model and dated revision, cache-hit input tokens, cache-miss input tokens,
output tokens, the dated per-million-token rate snapshot, computed CNY cost,
and whether the value comes from exact response usage or a balance estimate.
An interrupted response without a usage object is recorded as unknown rather
than silently treated as exact zero cost.

## Verification

Focused tests cover successful replacement, file creation, missing and
ambiguous old text, overlapping edits, undeclared paths, no-op edits, and
no-trailing-newline output. Agent-node tests prove that source is read before
rendering, deterministic failures are fed back into a bounded retry, and only
locally rendered diffs reach the patch artifact adapter. Full pytest coverage,
Ruff, strict MyPy, and existing asset checks remain required before one
feedback-assisted generation and official Docker grading.
