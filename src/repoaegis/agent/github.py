"""Reading the issue. The only thing the agent needs from GitHub before it has
the code: what the report actually says.

Unauthenticated access is 60 requests an hour, which is fine for a person
clicking through the console and not fine for a benchmark run, so a token is
read from the environment when present. The token is used for reading and
nothing else; it never reaches the workspace, where untrusted code will
eventually run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

from repoaegis.agent.workspace import RepoRef

log = structlog.get_logger(__name__)

MAX_BODY = 20_000


class GitHubError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Issue:
    number: int
    title: str
    body: str
    state: str
    url: str

    @property
    def is_open(self) -> bool:
        return self.state == "open"


class IssueReader(Protocol):
    """Where an issue's text comes from.

    Live runs read GitHub. The benchmark supplies the text it already has:
    hitting the API there would burn the rate limit and, worse, could return a
    version of the issue edited after the fix landed.
    """

    async def issue(self, ref: RepoRef) -> Issue: ...


class GitHub:
    def __init__(
        self,
        *,
        token: str = "",
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
        }
        if token:
            headers["authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def issue(self, ref: RepoRef) -> Issue:
        if ref.number is None:
            raise GitHubError(f"{ref.slug} has no issue number in its URL")
        path = f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}"
        try:
            response = await self._client.get(path)
        except httpx.HTTPError as exc:
            raise GitHubError(f"cannot reach GitHub: {exc}") from None

        if response.status_code == 404:
            raise GitHubError(f"no such issue: {ref.slug}#{ref.number}")
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError(
                "GitHub rate limit reached; set GITHUB_TOKEN to raise it from 60/hour to 5000"
            )
        if response.status_code >= 400:
            raise GitHubError(f"GitHub returned {response.status_code} for {ref.slug}#{ref.number}")

        payload = response.json()
        body = (payload.get("body") or "").strip()
        if len(body) > MAX_BODY:
            # A very long report is usually logs; the head carries the report.
            body = body[:MAX_BODY] + "\n… [issue body truncated]"
        return Issue(
            number=int(payload.get("number", ref.number)),
            title=str(payload.get("title") or "").strip(),
            body=body,
            state=str(payload.get("state") or "open"),
            url=str(payload.get("html_url") or ""),
        )
