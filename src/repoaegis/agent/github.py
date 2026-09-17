"""Talking to GitHub: reading the issue, and later publishing the fix.

Unauthenticated access is 60 requests an hour, which is fine for a person
clicking through the console and not fine for a benchmark run, so a token is
read from the environment when present.

The token never reaches the workspace. Untrusted repository code runs in the
container, and a credential that can push to your account has no business being
anywhere near it.

Publishing always goes to a fork under the token owner's own account, never to
the upstream project. That is a deliberate boundary rather than a limitation:
machine-generated pull requests sent to maintainers who did not ask for them
are a real cost to real people, and several projects now refuse AI-generated
contributions outright because of it. Pushing inside your own namespace makes
that impossible by construction -- upstream is never even notified.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

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

    async def _call(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            return await self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise GitHubError(f"cannot reach GitHub: {exc}") from None

    @staticmethod
    def _check(response: httpx.Response, what: str) -> httpx.Response:
        if response.status_code == 404:
            raise GitHubError(f"not found: {what}")
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            raise GitHubError(
                "GitHub rate limit reached; set GITHUB_TOKEN to raise it from 60/hour to 5000"
            )
        if response.status_code == 401:
            raise GitHubError("GitHub rejected the token; check GITHUB_TOKEN")
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("message", ""))
            except ValueError:
                detail = response.text[:120]
            raise GitHubError(f"GitHub returned {response.status_code} for {what}: {detail}")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def issue(self, ref: RepoRef) -> Issue:
        if ref.number is None:
            raise GitHubError(f"{ref.slug} has no issue number in its URL")
        what = f"{ref.slug}#{ref.number}"
        response = self._check(
            await self._call("GET", f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}"),
            what,
        )

        payload = self._json(response)
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


@dataclass(frozen=True, slots=True)
class PullRequest:
    number: int
    url: str
    branch: str
    repo: str

    def __str__(self) -> str:
        return f"{self.repo}#{self.number}"


class GitHubWriter:
    """The publishing half: fork, then open a pull request inside that fork.

    Kept apart from reading because the two need different trust. Reading works
    anonymously; publishing needs a token that can create repositories and push
    branches, and the smaller the surface that holds it, the better.
    """

    def __init__(self, client: GitHub) -> None:
        self._client = client

    async def login(self) -> str:
        """Whose account the token belongs to. Also the cheapest token check."""
        response = self._client._check(await self._client._call("GET", "/user"), "the token owner")
        return str(self._client._json(response).get("login") or "")

    async def default_branch(self, ref: RepoRef) -> str:
        response = self._client._check(
            await self._client._call("GET", f"/repos/{ref.owner}/{ref.repo}"), ref.slug
        )
        return str(self._client._json(response).get("default_branch") or "main")

    async def ensure_fork(self, ref: RepoRef, *, attempts: int = 20, pause: float = 3.0) -> RepoRef:
        """Fork under the token owner, and wait until GitHub has actually made it.

        Forking is asynchronous: the API answers 202 long before the repository
        can be pushed to, so the only reliable signal is the fork answering a
        plain GET.
        """
        owner = await self.login()
        fork = RepoRef(owner=owner, repo=ref.repo)
        if await self._exists(fork):
            return fork

        log.info("github.forking", upstream=ref.slug, into=fork.slug)
        self._client._check(
            await self._client._call("POST", f"/repos/{ref.owner}/{ref.repo}/forks"),
            f"fork of {ref.slug}",
        )
        for _ in range(attempts):
            if await self._exists(fork):
                return fork
            await asyncio.sleep(pause)
        raise GitHubError(f"fork {fork.slug} did not become available in time")

    async def _exists(self, ref: RepoRef) -> bool:
        response = await self._client._call("GET", f"/repos/{ref.owner}/{ref.repo}")
        return response.status_code == 200

    async def open_pull_request(
        self, ref: RepoRef, *, head: str, base: str, title: str, body: str
    ) -> PullRequest:
        response = self._client._check(
            await self._client._call(
                "POST",
                f"/repos/{ref.owner}/{ref.repo}/pulls",
                json={"title": title[:250], "head": head, "base": base, "body": body},
            ),
            f"pull request on {ref.slug}",
        )
        payload = self._client._json(response)
        return PullRequest(
            number=int(payload.get("number", 0)),
            url=str(payload.get("html_url") or ""),
            branch=head,
            repo=ref.slug,
        )
