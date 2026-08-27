"""Generate a RepoAegis patch for one SWE-bench sample (host-side).

This mirrors the frozen-candidate runner (run_frozen_candidate.py) but is
parameterised: a single Inspect Sample is turned into a SWEbenchTask, RepoAegis
runs its real agent graph, and the produced unified diff is returned.

Wiring notes
------------
``generate_repoaegis_patch`` has a full signature with all required host
configuration parameters (repoaegis_root, locators, cc_switch_db, task_root,
model_alias, protocol_digest, ...).  These are NOT available by default from
the Inspect Sample or TaskState — they must be plumbed through the call chain::

    run.py CLI args
      -> pilot_task.py ``repoaegis_verified()`` task params
        -> repoaegis_solver.py ``repoaegis_solver()`` solver params
          -> ``generate_repoaegis_patch()``

Current status: the plumbing is complete in this file; the caller chain
(run.py -> pilot_task.py -> repoaegis_solver.py) must accept and forward the
same parameters.  When any required parameter is missing the caller should
raise a clear error or fall back to replay mode.
"""

# mypy: ignore-errors
from __future__ import annotations

import json
import sqlite3
import tomllib
from pathlib import Path
from typing import Any

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.evaluation.swebench_runner import (
    GitSWEbenchRuntime,
    RepoAegisPatchAgent,
    SWEbenchTask,
    run_predictions,
)
from repo_maintenance_agent.models.openai_gateway import OpenAIModelGateway
from repo_maintenance_agent.models.usage import UsageLedger

# Defaults matching the frozen protocol (single-task-protocol.json).
DEFAULT_CONFIGURATION = (3, 1, 8, 2)  # max_iterations, context_rounds, tool_calls, patch_attempts
PER_TASK_LIMIT_CNY = 1


class GitSafeProcessRunner:
    """Minimal git-only ProcessRunner with safe.directory env (reused pattern)."""

    def __init__(self, *, git_env: dict[str, str], timeout_seconds: float) -> None:
        from repo_maintenance_agent.tools.process import ProcessRunner

        self._runner = ProcessRunner(allowed_executables={"git"}, timeout_seconds=timeout_seconds)
        self._git_env = git_env

    async def run(
        self,
        arguments,
        *,
        cwd,
        extra_env=None,
        secret_env=None,
        check=True,
        timeout_seconds=None,
    ):
        merged_env = {**self._git_env, **(secret_env or {})}
        return await self._runner.run(
            arguments,
            cwd=cwd,
            extra_env=extra_env,
            secret_env=merged_env,
            check=check,
            timeout_seconds=timeout_seconds,
        )


def _git_safe_env(paths: list[Path]) -> dict[str, str]:
    resolved = [str(p.resolve()).replace("\\", "/") for p in paths]
    values = {"GIT_CONFIG_COUNT": str(len(resolved))}
    for index, path in enumerate(resolved):
        values[f"GIT_CONFIG_KEY_{index}"] = "safe.directory"
        values[f"GIT_CONFIG_VALUE_{index}"] = path
    return values


def _provider_settings(database: Path, model_alias: str, api_style: str) -> Settings:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "select settings_config from providers where app_type=? and name=? limit 1",
            ("codex", "DeepSeek"),
        ).fetchone()
    finally:
        connection.close()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError("DeepSeek provider is missing from CC Switch")
    stored = json.loads(row[0])
    api_key = stored.get("auth", {}).get("OPENAI_API_KEY")
    config_text = stored.get("config")
    if not isinstance(api_key, str) or not api_key or not isinstance(config_text, str):
        raise RuntimeError("DeepSeek provider credentials are incomplete")
    config = tomllib.loads(config_text)
    provider_name = config.get("model_provider")
    providers = config.get("model_providers")
    provider = providers.get(provider_name) if isinstance(providers, dict) else None
    base_url = provider.get("base_url") if isinstance(provider, dict) else None
    if config.get("model") != model_alias or not isinstance(base_url, str):
        raise RuntimeError("CC Switch provider does not match the model alias")
    settings = Settings.model_validate(
        {
            "OPENAI_API_KEY": api_key,
            "OPENAI_BASE_URL": base_url,
            "OPENAI_MODEL": model_alias,
            "model_api_style": api_style,
        }
    )
    if not settings.has_openai_credentials:
        raise RuntimeError("DeepSeek credential injection failed")
    return settings


def _make_task(sample: Any) -> SWEbenchTask:
    metadata = sample.metadata or {}
    return SWEbenchTask(
        instance_id=str(sample.sample_id),
        repo=str(metadata["repo"]),
        base_commit=str(metadata["base_commit"]),
        problem_statement=str(sample.input),
    )


async def generate_repoaegis_patch(
    sample: Any,
    *,
    repoaegis_root: Path,
    locators: dict[str, str],
    cc_switch_db: Path,
    task_root: Path,
    model_alias: str,
    api_style: str = "chat-json",
    protocol_digest: str,
    arm: str = "candidate",
    maximum_call_cost_cny: str = "0.5",
    rates: dict[str, str] | None = None,
    configuration: tuple[int, int, int, int] = DEFAULT_CONFIGURATION,
) -> str | None:
    """Run RepoAegis for one sample and return the unified diff (or None)."""
    task = _make_task(sample)
    settings = _provider_settings(cc_switch_db, model_alias, api_style)
    locator_paths = [Path(v) for v in locators.values() if isinstance(v, str)]
    git_safe_env = _git_safe_env([repoaegis_root, *locator_paths])
    rates = rates or {
        "cache_hit_input": "0.02",
        "cache_miss_input": "1",
        "output": "2",
    }
    max_iterations, max_context_rounds, max_context_tool_calls, max_patch_attempts = configuration
    ledger = UsageLedger(limit_cny=PER_TASK_LIMIT_CNY, rates=rates)
    patch_agent = RepoAegisPatchAgent(
        model_factory=lambda active_ledger: OpenAIModelGateway.from_settings(
            settings,
            usage_ledger=active_ledger,
            maximum_call_cost_cny=maximum_call_cost_cny,
        ),
        artifact_root=task_root / "artifacts",
        max_iterations=max_iterations,
        max_context_rounds=max_context_rounds,
        max_context_tool_calls=max_context_tool_calls,
        max_patch_attempts=max_patch_attempts,
    )
    runtime = GitSWEbenchRuntime(
        repository_locators=locators,
        workspace_root=task_root / "workspace",
        model_name_or_path=settings.openai_model,
        patch_agent=patch_agent,
        runner=GitSafeProcessRunner(git_env=git_safe_env, timeout_seconds=900),
        development_feedback=None,
    )
    try:
        await run_predictions(
            [task],
            runtime=runtime,
            ledger=ledger,
            evidence_directory=task_root / "evidence",
            output_path=task_root / "prediction.jsonl",
            protocol_digest=protocol_digest,
            arm=arm,
        )
    except Exception:
        return None
    prediction_path = task_root / "prediction.jsonl"
    if not prediction_path.exists():
        return None
    for line in prediction_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        patch = record.get("model_patch")
        if isinstance(patch, str):
            return patch
    return None
