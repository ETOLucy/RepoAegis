"""Runtime settings and logging setup.

Everything is overridable through ``REPOAEGIS_*`` environment variables or a
``.env`` file; the defaults are what a developer wants on a laptop.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from structlog.typing import Processor


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPOAEGIS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./data/repoaegis.db"
    # Bring the schema to head on startup. Tests build the schema straight from
    # the models instead, and a dedicated test proves the two agree.
    migrate_on_startup: bool = True
    worker_enabled: bool = True
    worker_poll_seconds: float = 0.5
    # A tick can be inside a clone or a model call; do not cut it short.
    worker_shutdown_seconds: float = 30.0
    approval_policy: str = "default"
    approval_ttl_seconds: float = 3600.0

    # The provider's own variable name is the conventional one, so accept it
    # unprefixed as well as under REPOAEGIS_.
    deepseek_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "REPOAEGIS_DEEPSEEK_API_KEY"),
    )
    llm_model: str = "deepseek-flash"
    llm_base_url: str = "https://api.deepseek.com"
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    # Per task, in USD. Checked before each call, so a runaway loop stops rather
    # than overshooting. Roughly 400k input tokens at cache-miss peak rates.
    llm_budget_usd: float = 0.50

    github_base_url: str = "https://api.github.com"
    github_token: str = Field(
        default="",
        validation_alias=AliasChoices("GITHUB_TOKEN", "REPOAEGIS_GITHUB_TOKEN"),
    )
    # One bare clone per repository, one worktree per task.
    workspace_cache_dir: Path = Path("data/repos")
    workspace_work_dir: Path = Path("data/work")
    git_timeout_seconds: float = 300.0
    agent_max_steps: int = 20
    agent_max_edit_steps: int = 30
    # Solve/verify rounds per task. Running out is not a failure: the patch
    # goes to the reviewer with the red report and the hypotheses attached.
    agent_max_rounds: int = 3
    sse_heartbeat_seconds: float = 15.0
    # The worker's lease on a task it is working on, and how often it renews.
    # A worker that stops renewing for a whole lease is presumed dead and its
    # task is taken back; after max_recoveries such rescues the task fails.
    worker_lease_seconds: float = 300.0
    worker_heartbeat_seconds: float = 30.0
    worker_max_recoveries: int = 3
    log_json: bool = False

    # The sandbox the repository's tests run in. Off by default so a checkout
    # without Docker still plans, edits and asks for review; on, every round
    # ends with a real test run. The prefix reaches an engine on another host
    # (on the development machine: "wsl -d Ubuntu --").
    sandbox_enabled: bool = False
    sandbox_docker_prefix: str = ""
    sandbox_base_image: str = "python:3.12-slim"
    sandbox_timeout_seconds: float = 600.0
    sandbox_build_timeout_seconds: float = 1800.0
    sandbox_memory: str = "4g"

    # Scoring is SWE-bench's own harness, which needs Docker and its own
    # interpreter. On this machine the engine lives inside WSL, so the harness
    # is invoked through a prefix and paths crossing the boundary are rewritten.
    # Absolute, because there is no shell on the other side to expand "~".
    eval_harness_python: str = ""
    eval_harness_prefix: str = ""
    eval_dataset_name: str = "SWE-bench/SWE-bench_Verified"
    eval_max_workers: int = 4
    eval_timeout_seconds: float = 3600.0


def configure_logging(*, json: bool) -> None:
    # A log line quotes event payloads, and payloads quote issue text, which
    # can hold any character at all. On a Windows console the default codec
    # is GBK, and one emoji in an issue body once took down a state transition
    # from inside the log call. Logging must never be able to do that.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    renderer: Processor = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        renderer,
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )
