# Development Iteration Log

> 2026-08 | Internal development iteration log
> This file summarizes the iterative refinement process conducted during development.
> The full iteration process was carried out in the local development environment; detailed
> per-round logs are not retained.

## Overview

Development iteration was conducted on a **200-instance subset** sampled from the full
SWE-bench dataset (2,294 instances), with all Verified instances (500) excluded by unique ID
to ensure complete isolation between the development set and the final evaluation set.

The iteration process followed a cycle of: **run ? classify failures ? identify root cause ?
implement fix ? re-run**. Multiple rounds of this cycle progressively improved the system's
generation stability and patch quality.

## Failure Categories Addressed

### Patch Matching & Localization

Model-generated old_text often failed to match the target file exactly due to whitespace
variations, trailing newline differences, or duplicate code blocks. Solutions included:

- **Fuzzy matching**: tolerating whitespace and indentation differences
- **Approximate position matching**: using first/last non-blank lines as anchors
- **Whitespace normalization**: collapsing consecutive whitespace for matching
- **Context window disambiguation**: scoring multiple match candidates by context proximity
- **Whole-file fallback**: replacing the entire file when content similarity is sufficient

### Structured Output Validation

Model outputs occasionally violated JSON schema constraints or contained no-op edits where
old_text == new_text. Solutions included:

- **Retry with feedback**: catching validation errors, providing structured error messages
  back to the model, and allowing up to several retries
- **No-op edit filtering**: discarding edits where old_text equals new_text, retaining
  the proposal as long as at least one valid edit remains
- **Prompt improvement**: reinforcing that old_text must match verbatim

### Tool Execution Failures

Various tool-level failures disrupted the generation pipeline. Solutions included:

- **Empty query guard**: skipping search when the query string is empty
- **Graceful degradation**: continuing after a search failure instead of aborting
- **Git diff fallback**: falling back to working-tree-vs-HEAD diff when ref-based diff fails
- **Whitespace policy**: relaxing `git apply` whitespace checking from error-all to nowarn
- **Windows compatibility**: enabling long paths support for deep repository trees

### Review Gate Deadlock

The LLM-based reviewer sometimes repeatedly requested changes for reasonable, in-scope
patches, causing task timeouts. Solutions included:

- **Evidence-driven auto-approval**: automatically approving patches that meet all criteria:
  in-scope files, low risk, and verification passed
- **Audit trail preservation**: keeping warning records in the auto-approval trail

### Workspace Management

Workspace reuse and cleanup issues caused environment inconsistencies. Solutions included:

- **Delete-and-recreate**: when base commit mismatch is detected, delete and recreate
  the workspace instead of raising an error
- **Read-only file handling**: retrying with permission changes before deletion
- **Long paths**: enabling Windows long path support

## Outcome

After multiple iteration rounds, the generation success rate and stability improved
significantly. The final evaluation campaign on a 200-instance subset sampled from SWE-bench Verified (500 instances)
is currently complete.