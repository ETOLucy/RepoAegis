from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from repoaegis.server.api import create_app
from repoaegis.server.config import Settings
from repoaegis.server.events import EventBus
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import Database, TaskRepo


@pytest.fixture
def settings() -> Settings:
    return Settings(database_url="sqlite+aiosqlite:///:memory:", worker_enabled=False)


@pytest.fixture
async def repo(settings: Settings) -> AsyncIterator[TaskRepo]:
    db = Database(settings.database_url)
    await db.create_all()
    yield TaskRepo(db.sessions)
    await db.dispose()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def machine(repo: TaskRepo, bus: EventBus) -> TaskMachine:
    return TaskMachine(repo, bus)


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
