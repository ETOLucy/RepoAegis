"""Guards on the database setup itself, not on any single query."""

import asyncio
from pathlib import Path

import httpx
import pytest

from repoaegis.server.api import create_app
from repoaegis.server.config import Settings
from repoaegis.server.models import TaskStatus
from repoaegis.server.storage import Database, InMemorySQLiteRejected

from .conftest import sqlite_url


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite:///file:x?mode=memory&cache=shared&uri=true",
    ],
)
def test_in_memory_sqlite_is_rejected(url: str) -> None:
    with pytest.raises(InMemorySQLiteRejected):
        Database(url)


async def test_sqlite_runs_in_wal_mode(settings: Settings) -> None:
    db = Database(settings.database_url)
    await db.create_all()
    async with db.engine.connect() as conn:
        mode = await conn.exec_driver_sql("PRAGMA journal_mode")
        assert mode.scalar_one().lower() == "wal"
    await db.dispose()


async def test_worker_and_api_share_one_database(tmp_path: Path) -> None:
    """The regression: a concurrent worker used to roll back the API's writes."""
    settings = Settings(
        database_url=sqlite_url(tmp_path), worker_enabled=True, worker_poll_seconds=0.01
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/tasks", json={"issue_url": "https://github.com/o/r/issues/1"}
            )
            assert created.status_code == 201
            task_id = created.json()["id"]

            for _ in range(200):
                fetched = await client.get(f"/api/tasks/{task_id}")
                assert fetched.status_code == 200, "the task vanished mid-flight"
                if fetched.json()["status"] == TaskStatus.AWAITING_APPROVAL:
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("worker never reached the approval gate")

            assert [t["id"] for t in (await client.get("/api/tasks")).json()] == [task_id]
