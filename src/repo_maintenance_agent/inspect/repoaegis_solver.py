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
from typing import Any

from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox


@solver
def repoaegis_solver(
    *,
    predictions_path: str | None = None,
    patch_path: str = "/tmp/model_patch.diff",
    # Generate-mode parameters (plumbed from pilot_task -> run.py).
    # When any of these is None, the solver falls back to replay-only mode.
    repoaegis_root: Path | None = None,
    locators: dict[str, str] | None = None,
    cc_switch_db: Path | None = None,
    task_root: Path | None = None,
    model_alias: str | None = None,
    api_style: str = "chat-json",
    protocol_digest: str | None = None,
    arm: str = "candidate",
    maximum_call_cost_cny: str = "0.5",
    rates: dict[str, str] | None = None,
    configuration: tuple[int, int, int, int] | None = None,
) -> Solver:
    """Solver that applies a RepoAegis-generated patch into the sandbox.

    Args:
        predictions_path: Optional official-format prediction JSONL to replay.
            When given, the patch for the current sample id is read from this
            file instead of calling the model. Leave empty to call the model.
        patch_path: Path inside the sandbox where the unified diff is written.
        **generate_mode_kwargs: Forwarded to ``generate_repoaegis_patch``
            when predictions_path is None.  All must be non-None for generate
            mode to work; otherwise a NotImplementedError is raised.
    """
    async def solve(state: TaskState, generate: Generate) -> TaskState:
        instance_id = str(state.sample_id)
        if predictions_path:
            patch = _read_replayed_patch(Path(predictions_path), instance_id)
        else:
            # Generation path: call RepoAegis on the host.
            if repoaegis_root is None:
                raise NotImplementedError(
                    "Generate mode requires --repoaegis-root and related CLI args. "
                    "See run.py --help for the full list of generate-mode options."
                )
            from .generate import generate_repoaegis_patch

            patch = await generate_repoaegis_patch(
                state,
                repoaegis_root=repoaegis_root,
                locators=locators or {},
                cc_switch_db=cc_switch_db,
                task_root=task_root,
                model_alias=model_alias,
                api_style=api_style,
                protocol_digest=protocol_digest,
                arm=arm,
                maximum_call_cost_cny=maximum_call_cost_cny,
                rates=rates,
                configuration=configuration or (3, 1, 8, 2),
            )
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
                "/tmp/model_patch.apply_error",
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
