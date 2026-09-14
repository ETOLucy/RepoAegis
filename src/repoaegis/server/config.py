"""Runtime settings and logging setup.

Everything is overridable through ``REPOAEGIS_*`` environment variables or a
``.env`` file; the defaults are what a developer wants on a laptop.
"""

from __future__ import annotations

import logging
from pathlib import Path

import structlog
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from structlog.typing import Processor


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPOAEGIS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./data/repoaegis.db"
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
    sse_heartbeat_seconds: float = 15.0
    log_json: bool = False


def configure_logging(*, json: bool) -> None:
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
