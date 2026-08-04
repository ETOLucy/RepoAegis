from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
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
        default="gpt-5.6",
        validation_alias=AliasChoices("OPENAI_MODEL", "REPO_AGENT_OPENAI_MODEL"),
    )
    api_url: str = Field(default="http://127.0.0.1:8000")
    api_token: SecretStr | None = Field(default=None, repr=False)
    api_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    database_url: SecretStr = Field(
        default=SecretStr("sqlite+pysqlite:///data/repo-agent.db"),
        repr=False,
    )
    artifact_root: str = "artifacts"
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    max_iterations: int = Field(default=3, ge=1, le=10)
    worker_id: str = Field(default="repoaegis-worker", min_length=1, max_length=128)
    worker_tenant_ids: tuple[str, ...] = ()
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)

    @property
    def has_openai_credentials(self) -> bool:
        return self.openai_api_key is not None
