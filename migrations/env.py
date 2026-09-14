"""Alembic wiring.

The models stay the single source of truth for the schema; migrations are the
recorded path from one version of that truth to the next. The database URL
comes from the application's own settings rather than alembic.ini, so there is
one place that knows where the database lives.

``render_as_batch`` matters on SQLite, which cannot ALTER a column: alembic
emulates it by building a new table, copying the rows and swapping the names.
PostgreSQL ignores the setting, so one migration file serves both.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from repoaegis.server.config import Settings
from repoaegis.server.storage import Base

config = context.config
# A caller (a test, or `alembic -x`) may pin the URL; otherwise use the app's.
if not config.get_main_option("sqlalchemy.url", ""):
    config.set_main_option("sqlalchemy.url", Settings().database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it, for review or for a DBA."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
