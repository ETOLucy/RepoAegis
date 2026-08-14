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

## Evaluation campaign

The development iteration was conducted on a **200-instance subset** sampled from the full
SWE-bench dataset (2,294 instances), with all Verified instances excluded by unique ID.
After iterative failure analysis and resolution, the final evaluation was conducted on a
**200-instance subset** sampled from **SWE-bench Verified** (500 instances).

The evaluation was run in **frozen** mode: no development feedback was consumed during the
evaluation campaign. All results below are from one-shot, frozen evaluation passes.

## Evaluation results

| Metric | Value |
|--------|:-----:|
| Total instances | 200 |
| Successfully generated | 192 / 200 (96.0%) |
| Generation failed | 8 / 200 (4.0%) |
| Officially resolved (end-to-end) | **74 / 200 (37.0%)** |
| Officially resolved (conditional on generation) | 74 / 192 (38.5%) |
| Generated but unresolved | 118 / 192 (61.5%) |

This is a single-subset result, not a leaderboard claim. No baseline improvement
claim is made until an aligned paired baseline is published. The end-to-end resolution
rate (37.0%) is the primary metric; the conditional rate (38.5%) is provided for
comparability with studies that report resolution only among successfully generated
instances.

### Results by repository

| Repo | Sampled | Generated | Resolved | End-to-end rate (of sampled) | Conditional rate (of generated) |
|------|--------:|----------:|---------:|:------------------------:|:--------------------------------:|
| django | 95 | 91 | 44 | 46.3% | 48.4% |
| sympy | 31 | 30 | 8 | 25.8% | 26.7% |
| sphinx-doc | 18 | 17 | 3 | 16.7% | 17.6% |
| matplotlib | 13 | 12 | 5 | 38.5% | 41.7% |
| scikit-learn | 12 | 12 | 2 | 16.7% | 16.7% |
| astropy | 9 | 9 | 5 | 55.6% | 55.6% |
| pydata | 8 | 8 | 5 | 62.5% | 62.5% |
| pytest-dev | 6 | 6 | 2 | 33.3% | 33.3% |
| psf | 3 | 3 | 0 | 0.0% | 0.0% |
| pylint-dev | 3 | 2 | 0 | 0.0% | 0.0% |
| mwaskom | 1 | 1 | 0 | 0.0% | 0.0% |
| pallets | 1 | 1 | 0 | 0.0% | 0.0% |
| **Total** | **200** | **192** | **74** | **37.0%** | **38.5%** |

Repo-level sample sizes vary; single-digit samples should not be used for strong
conclusions. The stratified sampling was proportional to the Verified 500 distribution
(see [evaluation-plan.md](evaluation-plan.md) for methodology).

### Generation failures

8 instances failed during generation:

| Failure reason | Count |
|----------------|:-----:|
| context window exceeded during repository analysis | 2 |
| generated patch failed to apply cleanly to base commit | 2 |
| patch parsing failed after generation (unexpected diff format) | 2 |
| agent could not locate the correct modification site | 1 |
| generated patch failed self-consistency check | 1 |

### Result data

Per-instance results are available in [evaluation-results/](evaluation-results/):

- `manifest.json` ? evaluation campaign metadata
- `aggregate.json` ? aggregate statistics
- `per-instance.jsonl` ? per-instance status (instance ID, generation status, resolution status)
- `generation-failures.jsonl` ? details of generation failures
- `frozen-task-ids.jsonl` ? the 200 frozen instance IDs
- `grading-summary.json` ? full summary with resolved/unresolved ID lists
- `grading-progress.json` ? per-instance scores

> **Note:** Some evidence (full Docker harness logs, model patches) are retained locally
> due to size and API-key sensitivity. The published files contain all status labels,
> digest references, and reproduction commands needed to verify the reported numbers.
