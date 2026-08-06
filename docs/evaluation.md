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
cache-miss input, output tokens, the observed rate snapshot, computed CNY cost, and whether usage is
exact response evidence or a balance estimate. A transport failure without a provider usage object
has unknown token usage; a locally saved zero must not be presented as exact zero cost.
