from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from repo_maintenance_agent.api.app import create_app
from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.storage.sql import Base, SqlTaskRepository


def build_application() -> FastAPI:
    settings = Settings()
    tokens = parse_api_tokens()
    database_url = settings.database_url.get_secret_value()
    _prepare_sqlite_directory(database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    Path(settings.artifact_root).mkdir(parents=True, exist_ok=True)
    return create_app(
        repository=SqlTaskRepository(engine),
        authenticator=StaticTokenAuthenticator(tokens),
        production=settings.environment == "production",
        allowed_hosts=settings.allowed_hosts,
    )


def parse_api_tokens() -> dict[str, Principal]:
    raw = os.environ.get("REPO_AGENT_API_TOKENS", "")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("REPO_AGENT_API_TOKENS must be a JSON object") from error
    if not isinstance(parsed, dict) or not parsed:
        raise RuntimeError("REPO_AGENT_API_TOKENS must contain at least one token mapping")
    identities: dict[str, Principal] = {}
    for token, identity in parsed.items():
        if not isinstance(token, str) or not token:
            raise RuntimeError("REPO_AGENT_API_TOKENS keys must be non-empty strings")
        if isinstance(identity, str) and identity:
            identities[token] = Principal(tenant_id=identity, subject=identity)
            continue
        if (
            isinstance(identity, dict)
            and set(identity) == {"tenant_id", "subject"}
            and isinstance(identity["tenant_id"], str)
            and isinstance(identity["subject"], str)
            and identity["tenant_id"]
            and identity["subject"]
        ):
            identities[token] = Principal(
                tenant_id=identity["tenant_id"],
                subject=identity["subject"],
            )
            continue
        raise RuntimeError(
            "REPO_AGENT_API_TOKENS values must be tenant strings or identity objects"
        )
    return identities


def _prepare_sqlite_directory(database_url: str) -> None:
    url = make_url(database_url)
    database = url.database
    if (
        url.drivername.startswith("sqlite")
        and database is not None
        and database not in {"", ":memory:"}
    ):
        Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
