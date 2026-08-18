from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="REPO_AGENT_",
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "REPO_AGENT_OPENAI_API_KEY"),
        repr=False,
    )
    openai_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("OPENAI_MODEL", "REPO_AGENT_OPENAI_MODEL"),
    )
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_BASE_URL", "REPO_AGENT_OPENAI_BASE_URL"),
    )
    model_api_style: Literal["responses", "chat-json"] = "responses"
    api_url: str = Field(default="http://127.0.0.1:8000")
    api_token: SecretStr | None = Field(default=None, repr=False)
    api_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    database_url: SecretStr = Field(
        default=SecretStr("sqlite+pysqlite:///data/repo-agent.db"),
        repr=False,
    )
    artifact_root: str = "artifacts"
    workspace_root: str = "workspaces"
    repository_locators: dict[str, str] = Field(default_factory=dict, repr=False)
    sandbox_image_digests: dict[str, str] = Field(
        default_factory=lambda: {
            "python-3.12": (
                "python@sha256:"
                "496a05315a012e6f51a465cd89b8d1cae53d01b6c8cf098291a4094706f3e0d4"
            )
        }
    )
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_EMBEDDING_API_KEY", "REPO_AGENT_OPENAI_EMBEDDING_API_KEY"
        ),
        repr=False,
    )
    openai_embedding_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_EMBEDDING_BASE_URL", "REPO_AGENT_OPENAI_EMBEDDING_BASE_URL"
        ),
    )
    chat_repo_root: str | None = None
    github_token: SecretStr | None = Field(default=None, repr=False)
    sandbox_seccomp_profile: Path = Path("sandbox/seccomp.json")
    sandbox_runner_url: str | None = None
    sandbox_runner_token: SecretStr | None = Field(default=None, repr=False)
    sandbox_docker_host: str = "unix:///run/repoaegis-docker/docker.sock"
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    max_iterations: int = Field(default=3, ge=1, le=10)
    worker_id: str = Field(default="repoaegis-worker", min_length=1, max_length=128)
    worker_tenant_ids: tuple[str, ...] = ()
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)

    @model_validator(mode="after")
    def complete_sandbox_runner_configuration(self) -> Settings:
        if self.sandbox_runner_url is not None and self.sandbox_runner_token is None:
            raise ValueError("sandbox runner URL requires a matching token")
        return self

    @property
    def has_openai_credentials(self) -> bool:
        return self.openai_api_key is not None
