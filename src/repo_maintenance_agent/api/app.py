from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.api.schemas import ApprovalRequest, TaskCreateRequest, TaskResponse
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


def create_app(
    *,
    repository: TaskRepository,
    authenticator: StaticTokenAuthenticator,
    production: bool,
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver"),
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        yield

    app = FastAPI(
        title="Repo Maintenance Agent",
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
    async def request_context(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
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
        if task.status is not TaskStatus.NEEDS_APPROVAL or task.plan_hash != body.plan_hash:
            raise ConcurrentUpdate("approval does not match the active plan")
        decision = ApprovalDecision(
            approved=body.approved,
            approver=principal.subject,
            plan_hash=body.plan_hash,
            reason=body.reason,
        )
        target = TaskStatus.CODING if body.approved else TaskStatus.FAILED
        decided = task.model_copy(update={"approval": decision}).transition(target)
        saved = await repository.save(decided, expected_version=task.version)
        return TaskResponse.from_state(saved)

    app.include_router(router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
