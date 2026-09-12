"""HTTP surface. ``create_app`` is a factory so tests can inject settings."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, cast

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from repoaegis import __version__
from repoaegis.server.config import Settings, configure_logging
from repoaegis.server.events import EventBus
from repoaegis.server.models import Event, Task, TaskCreate
from repoaegis.server.sse import event_stream
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import Database, TaskRepo
from repoaegis.server.worker import Worker


def _machine(request: Request) -> TaskMachine:
    return cast(TaskMachine, request.app.state.machine)


def _repo(request: Request) -> TaskRepo:
    return cast(TaskRepo, request.app.state.repo)


def _bus(request: Request) -> EventBus:
    return cast(EventBus, request.app.state.bus)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


Machine = Annotated[TaskMachine, Depends(_machine)]
Repo = Annotated[TaskRepo, Depends(_repo)]
Bus = Annotated[EventBus, Depends(_bus)]
Config = Annotated[Settings, Depends(_settings)]

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.post("/tasks", status_code=201)
async def create_task(data: TaskCreate, machine: Machine) -> Task:
    return await machine.create(data)


@router.get("/tasks")
async def list_tasks(repo: Repo) -> list[Task]:
    return await repo.list_tasks()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, repo: Repo) -> Task:
    task = await repo.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.get("/tasks/{task_id}/events")
async def list_task_events(task_id: str, repo: Repo) -> list[Event]:
    if await repo.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    return await repo.list_events(task_id=task_id)


@router.get("/events")
async def stream_events(
    bus: Bus,
    repo: Repo,
    settings: Config,
    last_event_id: Annotated[int, Header(alias="Last-Event-ID")] = 0,
) -> StreamingResponse:
    return StreamingResponse(
        event_stream(
            bus, repo, after_id=last_event_id, heartbeat_seconds=settings.sse_heartbeat_seconds
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url)
        await db.create_all()
        repo = TaskRepo(db.sessions)
        bus = EventBus()
        app.state.settings = settings
        app.state.repo = repo
        app.state.bus = bus
        app.state.machine = TaskMachine(repo, bus)

        stop = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if settings.worker_enabled:
            worker = Worker(app.state.machine, repo, poll_seconds=settings.worker_poll_seconds)
            worker_task = asyncio.create_task(worker.run(stop))
        try:
            yield
        finally:
            stop.set()
            if worker_task is not None:
                await asyncio.wait_for(worker_task, timeout=5)
            await db.dispose()

    app = FastAPI(title="RepoAegis", version=__version__, lifespan=lifespan)
    app.include_router(router, prefix="/api")
    return app


app = create_app()
