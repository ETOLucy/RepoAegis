from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.api.schemas import (
    ApprovalRequest,
    EvaluationReplayRequest,
    EvaluationRunCreateRequest,
    EvaluationRunListResponse,
    EvaluationRunResponse,
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
)
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
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
        )
        return response

    security = HTTPBearer(auto_error=False)

    async def resolve_principal(
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> Principal:
        return authenticator.authenticate(credentials)

    principal_marker = Depends(resolve_principal)

    router = APIRouter(prefix="/v1", tags=["tasks"])

    @router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
    async def create_task(
        body: TaskCreateRequest,
        principal: Principal = principal_marker,
    ) -> TaskResponse:
        state = RepoTaskState.create(
            tenant_id=principal.tenant_id,
            subject=principal.subject,
            repo_id=body.repo_id,
            commit_sha=body.commit_sha,
            base_branch=body.base_branch,
            issue=body.issue,
        )
        await repository.save(state)
        return TaskResponse.from_state(state)

    @router.get("/tasks", response_model=TaskListResponse)
    async def list_tasks(
        limit: int = 50,
        principal: Principal = principal_marker,
    ) -> TaskListResponse:
        if not 1 <= limit <= 200:
            raise ConcurrentUpdate("task list limit is invalid")
        states = await repository.list(principal.tenant_id, limit=limit)
        return TaskListResponse(items=[TaskResponse.from_state(s) for s in states])

    @router.get("/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(
        task_id: str,
        principal: Principal = principal_marker,
    ) -> TaskResponse:
        state = await repository.get(principal.tenant_id, task_id)
        return TaskResponse.from_state(state)

    @router.post("/tasks/{task_id}/approve", response_model=TaskResponse)
    async def approve_task(
        task_id: str,
        body: ApprovalRequest,
        principal: Principal = principal_marker,
    ) -> TaskResponse:
        state = await repository.get(principal.tenant_id, task_id)
        if state.status is not TaskStatus.NEEDS_APPROVAL:
            raise InvalidStateTransition("task is not awaiting approval")
        if body.plan_hash != state.plan_hash:
            raise ConcurrentUpdate("plan hash mismatch — plan has changed since approval was requested")
        decision = ApprovalDecision(
            approved=body.approved,
            approver=principal.subject,
            plan_hash=body.plan_hash,
            target_commit=body.target_commit,
            allowed_tools=body.allowed_tools,
            reason=body.reason,
        )
        updated = state.apply_approval(decision)
        await repository.save(updated)
        return TaskResponse.from_state(updated)

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

    @router.get("/health", include_in_schema=False)
    async def v1_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health", include_in_schema=False)
    async def root_health() -> dict[str, str]:
        return {"status": "ok"}

    return app
