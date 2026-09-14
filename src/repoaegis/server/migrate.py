"""Running migrations from inside the application.

Alembic's environment drives its own event loop, so it is called on a worker
thread rather than from the server's loop. Startup migration is right for a
single-node deployment and a laptop; a multi-node one would run it as a deploy
step instead, which is why it is a setting rather than a hard-coded call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
from alembic import command
from alembic.config import Config

log = structlog.get_logger(__name__)

ROOT = Path(__file__).resolve().parents[3]


def config_for(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(config_for(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    """Undo migrations. Used by the round-trip test, and by a human in trouble."""
    command.downgrade(config_for(url), revision)


async def upgrade_async(url: str, revision: str = "head") -> None:
    log.info("migrations.upgrading", revision=revision)
    await asyncio.to_thread(upgrade, url, revision)
    log.info("migrations.current", revision=revision)
