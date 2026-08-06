# Evaluation integrity

RepoAegis uses the official SWE-bench Docker harness as the authority for task resolution. Model
generation, patch application, and internal review are intermediate evidence; none of them implies
that an instance is resolved.

The model proposes bounded exact-text edits. RepoAegis validates them against current files and
renders unified diff bytes locally before the existing Git preflight. These stages are recorded
separately: a valid proposal is not necessarily a rendered patch, a rendered patch is not
necessarily applied, and an applied/reviewed patch is not officially resolved until the Docker
harness passes every required test.

## Result labels

| Label | Meaning |
|---|---|
| `one-shot generation` | RepoAegis produced a patch without prior official-test feedback. |
| `officially resolved` | The official harness completed and every required test passed. |
| `feedback-assisted calibration` | A development rerun consumed a previous official failure. |
| `frozen evaluation` | No development feedback is accepted; the CLI enforces this before model use. |

Do not aggregate feedback-assisted calibration with one-shot or frozen results. A failed or errored
harness run remains failed or errored even when prediction generation completed successfully.

## Calibration feedback contract

`swebench-generate --development-feedback` accepts private JSONL records with this shape:

```json
{"instance_id":"owner__repo-1","source_run_id":"run-v1","prediction_digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","official_report_digest":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","failing_tests":["tests/test_value.py::test_value"],"summary":"The target behavior still failed under the official harness."}
```

The schema rejects extra benchmark answer fields such as `patch`, `test_patch`, `FAIL_TO_PASS`, and
`PASS_TO_PASS`. The feedback file must contain exactly one record for every selected calibration or
development task. The `frozen` role rejects the option.

```powershell
.venv\Scripts\python.exe -m repo_maintenance_agent.cli swebench-generate `
  tasks.jsonl `
  --output predictions.jsonl `
  --protocol protocol.json `
  --repository-locators repositories.json `
  --evidence-directory private-evidence `
  --workspace-root disposable-workspaces `
  --artifact-root private-artifacts `
  --arm baseline `
  --role calibration `
  --development-feedback official-failure-feedback.jsonl
```

The generated evidence stores a canonical digest of the feedback record. Resume fails if the
feedback changes, so an older prediction cannot be silently reused under new development evidence.

Paid-call evidence records the Asia/Shanghai date and time, dated model revision, cache-hit input,
cache-miss input, output tokens, their total, and whether usage is
exact response evidence or a balance estimate. A transport failure without a provider usage object
has unknown token usage; a locally saved zero must not be presented as exact zero cost.

## Consumed frozen holdout

The protocol `sha256:6b388c25f96b06662dff7bdb0c015f7f3b3ae4eca7ecc2174727ea38d69b168e`
froze eight previously unseen Verified tasks before generation. RepoAegis commit `978d24e` generated
four predictions and recorded four terminal generation failures. SWE-bench 4.1.0 officially
resolved three of the four submitted predictions, so the strict result is 3/8 (37.5%). Generation
failures are not removed from the denominator.

This holdout is now consumed. Its outcomes may diagnose failure mechanisms, but no later revision
may reuse it as unseen evidence. A future improvement claim requires a new frozen RepoAegis
revision, a new disjoint holdout, and results reported separately from this run. The redacted record
is [`docs/evidence/swebench-holdout-v2.json`](evidence/swebench-holdout-v2.json).
