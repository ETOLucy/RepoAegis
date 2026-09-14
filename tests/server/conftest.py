from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from repoaegis.server.api import create_app
from repoaegis.server.config import Settings
from repoaegis.server.events import EventBus
from repoaegis.server.gate import ApprovalGate
from repoaegis.server.policy import get_policy
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, Database, TaskRepo


def sqlite_url(tmp_path: Path) -> str:
    """A fresh file per test. POSIX separators keep the URL valid on Windows too."""
    return f"sqlite+aiosqlite:///{(tmp_path / 'repoaegis.db').as_posix()}"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=sqlite_url(tmp_path),
        worker_enabled=False,
        # Build the schema from the models; test_migrations.py proves the
        # migrations describe the same thing.
        migrate_on_startup=False,
    )


@pytest.fixture
async def db(settings: Settings) -> AsyncIterator[Database]:
    database = Database(settings.database_url)
    await database.create_all()
    yield database
    await database.dispose()


@pytest.fixture
def repo(db: Database) -> TaskRepo:
    return TaskRepo(db.sessions)


@pytest.fixture
def approvals(db: Database) -> ApprovalRepo:
    return ApprovalRepo(db.sessions)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def machine(repo: TaskRepo, bus: EventBus) -> TaskMachine:
    return TaskMachine(repo, bus)


@pytest.fixture
def gate(approvals: ApprovalRepo, machine: TaskMachine, settings: Settings) -> ApprovalGate:
    return ApprovalGate(
        approvals,
        machine,
        policy=get_policy(settings.approval_policy),
        ttl_seconds=settings.approval_ttl_seconds,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
