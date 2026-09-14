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
from repoaegis.server.gate import (
    ApprovalClosed,
    ApprovalGate,
    ApprovalNotFound,
    PayloadMismatch,
)
from repoaegis.server.models import Approval, DecisionRequest, Event, Task, TaskCreate
from repoaegis.server.policy import get_policy
from repoaegis.server.sse import event_stream
from repoaegis.server.state import TaskMachine
from repoaegis.server.storage import ApprovalRepo, Database, TaskRepo
from repoaegis.server.worker import Worker


def _machine(request: Request) -> TaskMachine:
    return cast(TaskMachine, request.app.state.machine)


def _repo(request: Request) -> TaskRepo:
    return cast(TaskRepo, request.app.state.repo)


def _bus(request: Request) -> EventBus:
    return cast(EventBus, request.app.state.bus)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _gate(request: Request) -> ApprovalGate:
    return cast(ApprovalGate, request.app.state.gate)


def _approvals(request: Request) -> ApprovalRepo:
    return cast(ApprovalRepo, request.app.state.approvals)


Machine = Annotated[TaskMachine, Depends(_machine)]
Repo = Annotated[TaskRepo, Depends(_repo)]
Bus = Annotated[EventBus, Depends(_bus)]
Config = Annotated[Settings, Depends(_settings)]
Gate = Annotated[ApprovalGate, Depends(_gate)]
Approvals = Annotated[ApprovalRepo, Depends(_approvals)]

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


@router.get("/tasks/{task_id}/approvals")
async def list_task_approvals(task_id: str, repo: Repo, approvals: Approvals) -> list[Approval]:
    if await repo.get(task_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    return await approvals.list_for_task(task_id)


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, approvals: Approvals) -> Approval:
    approval = await approvals.get(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return approval


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    body: DecisionRequest,
    gate: Gate,
    actor: Annotated[str, Header(alias="X-Actor")] = "anonymous",
) -> Approval:
    """Answer one gate.

    Repeating the same answer is a no-op that returns the same envelope, so a
    double-click or a client retry cannot advance the task twice. A different
    answer to an already-decided gate is a conflict, not an overwrite.
    """
    try:
        return await gate.decide(
            approval_id, body.decision, actor=actor, payload_hash=body.payload_hash
        )
    except ApprovalNotFound:
        raise HTTPException(status_code=404, detail="approval not found") from None
    except PayloadMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ApprovalClosed as exc:
        if exc.idempotent:
            return exc.approval
        raise HTTPException(
            status_code=409, detail=f"approval already {exc.approval.status.value}"
        ) from None


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
        approvals = ApprovalRepo(db.sessions)
        bus = EventBus()
        machine = TaskMachine(repo, bus)
        gate = ApprovalGate(
            approvals,
            machine,
            policy=get_policy(settings.approval_policy),
            ttl_seconds=settings.approval_ttl_seconds,
        )
        app.state.settings = settings
        app.state.repo = repo
        app.state.approvals = approvals
        app.state.bus = bus
        app.state.machine = machine
        app.state.gate = gate

        stop = asyncio.Event()
        worker_task: asyncio.Task[None] | None = None
        if settings.worker_enabled:
            worker = Worker(machine, repo, gate, poll_seconds=settings.worker_poll_seconds)
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
