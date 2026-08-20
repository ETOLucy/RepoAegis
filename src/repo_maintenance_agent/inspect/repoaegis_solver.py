"""Inspect solver that drives the real RepoAegis patch-generation pipeline.

Instead of Inspect's own react agent editing files inside the sandbox, this
solver invokes RepoAegis (GitSWEbenchRuntime + RepoAegisPatchAgent) on the host
to produce a unified diff, then writes that patch into the Inspect sandbox so
the official swe_bench_scorer can run the real tests.

Two modes:
- "replay": read a previously generated official-format prediction JSONL and
  apply those patches (free, validates the pipeline end to end).
- "generate": call RepoAegis to generate a new patch (requires model API).
"""

from __future__ import annotations

import json
from pathlib import Path

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox


@solver
def repoaegis_solver(
    *,
    predictions_path: str | None = None,
    patch_path: str = "/tmp/model_patch.diff",  # noqa: S108 - sandbox-internal path
) -> Solver:
    """Solver that applies a RepoAegis-generated patch into the sandbox.

    Args:
        predictions_path: Optional official-format prediction JSONL to replay.
            When given, the patch for the current sample id is read from this
            file instead of calling the model. Leave empty to call the model.
        patch_path: Path inside the sandbox where the unified diff is written.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        instance_id = str(state.sample_id)
        if predictions_path:
            patch = _read_replayed_patch(Path(predictions_path), instance_id)
        else:
            # Generation path: call RepoAegis on the host. Implemented in
            # generate.py; wired here so the scorer pipeline is identical.
            # NOTE: pilot wiring is incomplete - generate_repoaegis_patch needs
            # host config (repoaegis_root, locators, cc_switch_db, task_root,
            # model_alias, protocol_digest, ...) that is not plumbed through the
            # Inspect task yet; replay mode is the verified path.
            from .generate import generate_repoaegis_patch

            patch = await generate_repoaegis_patch(state)  # type: ignore[call-arg]
        if patch is None or not patch.strip():
            # No patch produced: leave the sandbox untouched so the scorer
            # reports an empty-patch (unresolved) result, mirroring the
            # official harness behaviour for a failed generation.
            return state
        await sandbox().write_file(patch_path, patch)
        result = await sandbox().exec(["git", "apply", "--whitespace=nowarn", patch_path])
        if result.returncode != 0:
            # Apply failure is an unresolved result, not a harness crash.
            await sandbox().write_file(
                "/tmp/model_patch.apply_error",  # noqa: S108 - sandbox-internal path
                result.stderr or result.stdout,
            )
        return state

    return solve


def _read_replayed_patch(path: Path, instance_id: str) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("instance_id") == instance_id:
            patch = record.get("model_patch")
            return patch if isinstance(patch, str) else None
    return None
