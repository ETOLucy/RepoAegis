# SWE-bench calibration feedback design

## Goal

Let RepoAegis consume a previous official SWE-bench failure as development
evidence during calibration, while preserving a strict distinction between
one-shot evaluation and feedback-assisted reruns.

## Scope and integrity boundary

- Feedback is accepted only for `calibration` and `development` protocol roles.
- The `frozen` role rejects feedback before any model call.
- Feedback contains the prior run ID, prediction digest, official report digest,
  failing test IDs, and a bounded failure summary. It never contains a gold patch
  or the benchmark `test_patch` field.
- A feedback-assisted prediction is development evidence, not a one-shot or
  frozen benchmark result.

## Data flow

The `swebench-generate` command accepts an optional private feedback JSONL file.
Each record is validated and matched by `instance_id`. RepoAegis adds the record
to the task's research evidence after repository retrieval, so planning and
coding can reason from it. Review receives the same evidence explicitly and must
check the candidate against the recorded failure.

Generation evidence records the feedback digest. Resume validation requires the
same digest, preventing a prediction created with one feedback artifact from
being silently reused with another.

## Error handling

Generation fails before model use when feedback is malformed, duplicated,
targets a task outside the selected role, is supplied to `frozen`, or does not
match resumed evidence. Feedback summaries and test lists are bounded to keep
prompt cost predictable.

## Verification

Tests cover schema rejection, role gating, prompt propagation to coding and
review, evidence digest persistence, and resume mismatch. The existing full
pytest, Ruff, strict MyPy, and coverage gates remain authoritative for the code
change. The resulting prediction is then graded by the official Docker harness.
