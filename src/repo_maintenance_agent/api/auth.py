from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials


@dataclass(frozen=True, slots=True)
class Principal:
    tenant_id: str
    subject: str


class StaticTokenAuthenticator:
    """Development authenticator; production swaps this for GitHub App/OIDC auth."""

    def __init__(self, token_to_tenant: Mapping[str, str | Principal]) -> None:
        self._token_hashes = {
            self._digest(token): (
                identity
                if isinstance(identity, Principal)
                else Principal(tenant_id=identity, subject=identity)
            )
            for token, identity in token_to_tenant.items()
        }

    def authenticate(
        self, credentials: HTTPAuthorizationCredentials | None
    ) -> Principal:
        if credentials is None or credentials.scheme.casefold() != "bearer":
            raise _unauthorized()
        candidate = self._digest(credentials.credentials)
        for token_hash, principal in self._token_hashes.items():
            if secrets.compare_digest(candidate, token_hash):
                return principal
        raise _unauthorized()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
