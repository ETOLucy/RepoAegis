from __future__ import annotations

import hmac
from pathlib import Path, PurePosixPath
from typing import Protocol

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from repo_maintenance_agent.sandbox.docker import SandboxSpec
from repo_maintenance_agent.tools.process import ProcessResult


class SandboxExecutor(Protocol):
    async def execute(self, spec: SandboxSpec) -> ProcessResult: ...


class RunnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    task_id: str = Field(min_length=1, max_length=128)
    workspace: str = Field(min_length=1, max_length=500)
    image: str = Field(pattern=r"^[A-Za-z0-9._/-]+@sha256:[a-f0-9]{64}$")
    command: tuple[str, ...] = Field(min_length=1, max_length=100)
    cpu_limit: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    memory_limit: str = Field(pattern=r"^[1-9][0-9]*[kmg]$")
    pids_limit: int = Field(ge=16, le=1_024)
    timeout_seconds: int = Field(ge=1, le=1_800)
    network_enabled: bool

    @field_validator("workspace")
    @classmethod
    def safe_relative_workspace(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or normalized.startswith("-"):
            raise ValueError("workspace must be a safe relative path")
        return path.as_posix()


class RunnerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    returncode: int
    stdout: str
    stderr: str
    duration_ms: int = Field(ge=0)


def create_sandbox_runner_app(
    *, sandbox: SandboxExecutor, token: SecretStr, workspace_root: Path
) -> FastAPI:
    root = workspace_root.resolve()
    bearer = HTTPBearer(auto_error=False)
    bearer_dependency = Depends(bearer)

    def authorize(
        credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
    ) -> None:
        supplied = credentials.credentials if credentials is not None else ""
        if credentials is None or not hmac.compare_digest(supplied, token.get_secret_value()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/run", response_model=RunnerResponse, dependencies=[Depends(authorize)])
    async def run(body: RunnerRequest) -> RunnerResponse:
        workspace = (root / body.workspace).resolve()
        if not workspace.is_relative_to(root) or not workspace.is_dir():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="invalid workspace",
            )
        result = await sandbox.execute(
            SandboxSpec(
                task_id=body.task_id,
                workspace=workspace,
                image=body.image,
                command=body.command,
                cpu_limit=body.cpu_limit,
                memory_limit=body.memory_limit,
                pids_limit=body.pids_limit,
                timeout_seconds=body.timeout_seconds,
                network_enabled=body.network_enabled,
            )
        )
        return RunnerResponse(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
        )

    return app
