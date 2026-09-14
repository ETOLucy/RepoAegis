"""Migrations must describe exactly the schema the models declare.

Tests build their schema from the models directly, because running alembic for
each of a hundred tests would cost more than it proves. The risk that trade
introduces is drift: a column added to a model and forgotten in a migration
would pass every test and fail in production, where only migrations run.

This file closes that hole. It migrates an empty database and asks alembic what
it would still need to change; anything at all means the two have diverged.
"""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from repoaegis.server.migrate import ROOT, config_for, downgrade, upgrade
from repoaegis.server.storage import Base


def test_the_repository_has_migrations() -> None:
    versions = sorted((ROOT / "migrations" / "versions").glob("*.py"))
    assert versions, "no migration scripts; the schema has no recorded history"


def test_migrations_leave_nothing_for_autogenerate_to_add(tmp_path: Path) -> None:
    database = tmp_path / "migrated.db"
    upgrade(f"sqlite+aiosqlite:///{database.as_posix()}")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        difference = compare_metadata(context, Base.metadata)
    engine.dispose()

    assert difference == [], (
        "the models and the migrations disagree; run\n"
        "  uv run python -m alembic revision --autogenerate -m '<what changed>'"
    )


def test_downgrade_returns_to_an_empty_schema(tmp_path: Path) -> None:
    """A migration that cannot be undone is a one-way door in production."""
    database = tmp_path / "roundtrip.db"
    url = f"sqlite+aiosqlite:///{database.as_posix()}"
    upgrade(url)
    downgrade(url)  # "base" means: before the first revision

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    with engine.connect() as connection:
        remaining = set(inspect(connection).get_table_names())
    engine.dispose()
    assert remaining <= {"alembic_version"}


def test_config_points_at_this_repository() -> None:
    config = config_for("sqlite+aiosqlite:///./ignored.db")
    assert Path(config.get_main_option("script_location") or "").name == "migrations"
    assert config.get_main_option("sqlalchemy.url") == "sqlite+aiosqlite:///./ignored.db"
