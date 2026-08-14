from __future__ import annotations

import json
import os

from fastapi import FastAPI

from repo_maintenance_agent.api.app import create_app
from repo_maintenance_agent.api.auth import Principal, StaticTokenAuthenticator
from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.runtime import build_runtime


def build_application() -> FastAPI:
    settings = Settings()
    tokens = parse_api_tokens()
    runtime = build_runtime(settings)
    return create_app(
        repository=runtime.tasks,
        evaluation_repository=runtime.evaluations,
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
