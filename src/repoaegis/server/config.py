"""Runtime settings and logging setup.

Everything is overridable through ``REPOAEGIS_*`` environment variables or a
``.env`` file; the defaults are what a developer wants on a laptop.
"""

from __future__ import annotations

import logging

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict
from structlog.typing import Processor


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REPOAEGIS_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./data/repoaegis.db"
    worker_enabled: bool = True
    worker_poll_seconds: float = 0.5
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
