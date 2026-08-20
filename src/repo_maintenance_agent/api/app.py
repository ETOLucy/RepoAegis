from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.api.schemas import (
    ApprovalRequest,
    ChatHit,
    ChatRequest,
    ChatResponse,
    EvaluationReplayRequest,
    EvaluationRunCreateRequest,
    EvaluationRunListResponse,
    EvaluationRunResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
)
from repo_maintenance_agent.chat import ChatEngine
from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.domain.errors import (
    ConcurrentUpdate,
    InvalidStateTransition,
    ResourceNotFound,
)
from repo_maintenance_agent.domain.models import (
    ApprovalDecision,
    RepoTaskState,
    TaskStatus,
)
from repo_maintenance_agent.domain.ports import TaskRepository
from repo_maintenance_agent.evaluation.harness import (
    EvaluationHarness,
    ObservationExecutor,
)
from repo_maintenance_agent.evaluation.reports import render_markdown_report
from repo_maintenance_agent.evaluation.storage import (
    EvaluationRepository,
    InMemoryEvaluationRepository,
)

chat_router = APIRouter(prefix="/v1", tags=["chat"])

_chat_engine: ChatEngine | None = None
_chat_engine_lock = asyncio.Lock()


async def _get_chat_engine() -> ChatEngine | None:
    global _chat_engine
    if _chat_engine is not None:
        return _chat_engine
    async with _chat_engine_lock:
        if _chat_engine is not None:
            return _chat_engine
        repo_root = os.environ.get("REPO_AGENT_CHAT_REPO_ROOT")
        if not repo_root:
            return None
        _chat_engine = ChatEngine(Settings(), repo_root=Path(repo_root))
        return _chat_engine


@chat_router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    engine = await _get_chat_engine()
    if engine is None:
        raise ConcurrentUpdate("chat is not configured (REPO_AGENT_CHAT_REPO_ROOT missing)")
    result = await engine.answer(body.query, top_k=body.top_k)
    raw_hits = cast(list[dict[str, Any]], result["hits"])
    return ChatResponse(
        answer=cast(str, result["answer"]),
        hits=tuple(ChatHit(**hit) for hit in raw_hits),
        repo_id=cast(str, result["repo_id"]),
        commit_sha=cast(str, result["commit_sha"]),
    )


def create_app(
    *,
    repository: TaskRepository,
    evaluation_repository: EvaluationRepository | None = None,
    authenticator: StaticTokenAuthenticator,
    production: bool,
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver"),
) -> FastAPI:
    evaluation_runs = evaluation_repository or InMemoryEvaluationRepository()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        yield

    app = FastAPI(
        title="RepoAegis",
        version="0.1.0",
        debug=False,
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.exception_handler(ResourceNotFound)
    async def resource_not_found(
        request: Request,
        error: ResourceNotFound,
    ) -> JSONResponse:
        del request, error
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "not found"})

    @app.exception_handler(ConcurrentUpdate)
    @app.exception_handler(InvalidStateTransition)
    async def task_state_conflict(
        request: Request,
        error: ConcurrentUpdate | InvalidStateTransition,
    ) -> JSONResponse:
        del request, error
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "task state conflict"},
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    bearer = HTTPBearer(auto_error=False)
    bearer_dependency = Depends(bearer)

    def principal_dependency(
        credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
    ) -> Principal:
        return authenticator.authenticate(credentials)

    principal_marker = Depends(principal_dependency)
    router = APIRouter(prefix="/v1", dependencies=[])

    @router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
    async def create_task(
        body: TaskCreateRequest,
        principal: Principal = principal_marker,
    ) -> TaskResponse:
        state = RepoTaskState(
            tenant_id=principal.tenant_id,
            repo_id=body.repo_id,
            commit_sha=body.commit_sha,
            base_branch=body.base_branch,
            issue=body.issue,
        )
        created = await repository.create(state)
        return TaskResponse.from_state(created)

    @router.get("/tasks", response_model=TaskListResponse)
    async def list_tasks(
        limit: int = 50,
        principal: Principal = principal_marker,
    ) -> TaskListResponse:
        if not 1 <= limit <= 200:
            raise ConcurrentUpdate("task list limit is invalid")
        tasks = await repository.list(principal.tenant_id, limit=limit)
        return TaskListResponse(items=[TaskResponse.from_state(task) for task in tasks])

    @router.get("/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(
        task_id: str,
        principal: Principal = principal_marker,
    ) -> TaskResponse:
        task = await repository.get(principal.tenant_id, task_id)
        return TaskResponse.from_state(task)

    @router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
    async def cancel_task(
        task_id: str,
        principal: Principal = principal_marker,
    ) -> TaskResponse:
        task = await repository.get(principal.tenant_id, task_id)
        cancelled = task.transition(TaskStatus.CANCELLED)
        saved = await repository.save(cancelled, expected_version=task.version)
        return TaskResponse.from_state(saved)

    @router.post("/tasks/{task_id}/approval", response_model=TaskResponse)
    async def decide_approval(
        task_id: str,
        body: ApprovalRequest,
        principal: Principal = principal_marker,
    ) -> TaskResponse:
        task = await repository.get(principal.tenant_id, task_id)
        if (
            task.status is not TaskStatus.NEEDS_APPROVAL
            or task.plan_hash != body.plan_hash
            or task.commit_sha != body.target_commit
            or task.allowed_tools != body.allowed_tools
        ):
            raise ConcurrentUpdate("approval does not match the active plan")
        decision = ApprovalDecision(
            approved=body.approved,
            approver=principal.subject,
            plan_hash=body.plan_hash,
            target_commit=body.target_commit,
            allowed_tools=body.allowed_tools,
            reason=body.reason,
        )
        target = TaskStatus.CODING if body.approved else TaskStatus.FAILED
        decided = task.model_copy(update={"approval": decision}).transition(target)
        saved = await repository.save(decided, expected_version=task.version)
        return TaskResponse.from_state(saved)

    @router.post(
        "/evaluations/runs",
        response_model=EvaluationRunResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_evaluation_run(
        body: EvaluationRunCreateRequest,
        principal: Principal = principal_marker,
    ) -> EvaluationRunResponse:
        baseline = (
            await evaluation_runs.get(principal.tenant_id, body.baseline_run_id)
            if body.baseline_run_id is not None
            else None
        )
        harness = EvaluationHarness(ObservationExecutor(body.observations))
        run = await harness.run(
            tenant_id=principal.tenant_id,
            suite=body.suite,
            candidate_label=body.candidate_label,
            provenance=body.provenance,
            baseline=baseline,
        )
        await evaluation_runs.create(run)
        return EvaluationRunResponse.from_run(run)

    @router.get("/evaluations/runs", response_model=EvaluationRunListResponse)
    async def list_evaluation_runs(
        limit: int = 50,
        principal: Principal = principal_marker,
    ) -> EvaluationRunListResponse:
        if not 1 <= limit <= 200:
            raise ConcurrentUpdate("evaluation list limit is invalid")
        runs = await evaluation_runs.list(principal.tenant_id, limit=limit)
        return EvaluationRunListResponse(
            items=[EvaluationRunResponse.from_run(run) for run in runs]
        )

    @router.get(
        "/evaluations/runs/{run_id}",
        response_model=EvaluationRunResponse,
    )
    async def get_evaluation_run(
        run_id: str,
        principal: Principal = principal_marker,
    ) -> EvaluationRunResponse:
        run = await evaluation_runs.get(principal.tenant_id, run_id)
        return EvaluationRunResponse.from_run(run)

    @router.post(
        "/evaluations/runs/{run_id}/replay",
        response_model=EvaluationRunResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def replay_evaluation_run(
        run_id: str,
        body: EvaluationReplayRequest,
        principal: Principal = principal_marker,
    ) -> EvaluationRunResponse:
        source = await evaluation_runs.get(principal.tenant_id, run_id)
        observations = {
            result.case_id: result.observation
            for result in source.results
            if result.observation is not None
        }
        harness = EvaluationHarness(ObservationExecutor(observations))
        replay = await harness.replay(source, case_ids=body.case_ids)
        await evaluation_runs.create(replay)
        return EvaluationRunResponse.from_run(replay)

    @router.get(
        "/evaluations/runs/{run_id}/report.json",
        response_model=EvaluationRunResponse,
    )
    async def export_evaluation_json(
        run_id: str,
        principal: Principal = principal_marker,
    ) -> EvaluationRunResponse:
        run = await evaluation_runs.get(principal.tenant_id, run_id)
        return EvaluationRunResponse.from_run(run)

    @router.get(
        "/evaluations/runs/{run_id}/report.md",
        response_class=PlainTextResponse,
    )
    async def export_evaluation_markdown(
        run_id: str,
        principal: Principal = principal_marker,
    ) -> PlainTextResponse:
        run = await evaluation_runs.get(principal.tenant_id, run_id)
        if run.aggregate is None or run.gate_decision is None:
            raise ConcurrentUpdate("evaluation report is not ready")
        markdown = render_markdown_report(
            run_id=run.run_id,
            candidate_label=run.candidate_label,
            aggregate=run.aggregate,
            comparison=run.comparison,
            decision=run.gate_decision,
            results=run.results,
        )
        return PlainTextResponse(markdown, media_type="text/markdown")

    app.include_router(router)
    app.include_router(chat_router)

    console_root = Path(__file__).resolve().parents[1] / "console"

    @app.get("/console", include_in_schema=False, response_class=FileResponse)
    async def console() -> FileResponse:
        return FileResponse(console_root / "index.html", media_type="text/html")

    @app.get("/console/app.css", include_in_schema=False, response_class=FileResponse)
    async def console_styles() -> FileResponse:
        return FileResponse(console_root / "app.css", media_type="text/css")

    @app.get("/console/app.js", include_in_schema=False, response_class=FileResponse)
    async def console_script() -> FileResponse:
        return FileResponse(
            console_root / "app.js",
            media_type="text/javascript",
        )

    @app.get("/favicon.ico", include_in_schema=False, status_code=status.HTTP_204_NO_CONTENT)
    async def favicon() -> Response:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
