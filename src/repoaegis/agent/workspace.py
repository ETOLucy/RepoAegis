"""Getting the code onto disk, once per repository rather than once per task.

Layout::

    <cache>/psf__requests.git     one bare partial clone, shared
    <work>/<task id>/             one worktree per task, checked out at a sha

A bare clone with ``--filter=blob:none`` fetches the history's shape but not
every file's contents, and each worktree then materialises only what it checks
out. Measured on this machine: flask's cache is 5 MB and takes 4 seconds; the
first worktree costs 3 seconds and the second costs none. A benchmark that
walks a dozen repositories hundreds of times pays the download twelve times,
not hundreds.

Every checkout is pinned to a commit sha. A run against "the current main" is
not reproducible, and an evaluation whose score moves because upstream moved is
not measuring the agent.

Git is invoked as a subprocess with an argument list -- never a shell string --
so a repository name can never turn into a command.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_ISSUE_URL = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?"
    r"(?:/(?:issues|pull)/(?P<number>\d+))?/?$"
)


class GitError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RepoRef:
    owner: str
    repo: str
    number: int | None = None

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    @property
    def cache_name(self) -> str:
        # Flat and filesystem-safe: nested directories buy nothing here.
        return f"{self.owner}__{self.repo}.git"


def parse_issue_url(url: str) -> RepoRef:
    match = _ISSUE_URL.match(url.strip())
    if match is None:
        raise ValueError(f"not a GitHub issue or repository URL: {url!r}")
    number = match.group("number")
    return RepoRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
        number=int(number) if number else None,
    )


async def git(*args: str, cwd: Path | None = None, timeout: float = 300.0) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except TimeoutError:
        process.kill()
        raise GitError(f"git {args[0]} timed out after {timeout:.0f}s") from None
    if process.returncode != 0:
        detail = err.decode("utf-8", "replace").strip().splitlines()
        raise GitError(f"git {args[0]} failed: {detail[-1] if detail else process.returncode}")
    return out.decode("utf-8", "replace")


class Workspaces:
    """Owns the cache directory and the per-task checkouts."""

    def __init__(self, cache_dir: Path, work_dir: Path, *, timeout_seconds: float = 300.0) -> None:
        self.cache_dir = cache_dir
        self.work_dir = work_dir
        self._timeout = timeout_seconds
        # One repository is cloned once even if several tasks arrive together.
        self._locks: dict[str, asyncio.Lock] = {}

    async def resolve_head(self, ref: RepoRef) -> str:
        """The sha the default branch points at right now, so the run can pin it."""
        out = await git("ls-remote", ref.clone_url, "HEAD", timeout=self._timeout)
        if not out.strip():
            raise GitError(f"{ref.slug} has no HEAD; does the repository exist?")
        return out.split()[0]

    async def prepare(self, ref: RepoRef, sha: str, *, task_id: str) -> Path:
        """Check ``sha`` out into this task's own directory and return the path."""
        cache = await self._ensure_cache(ref, sha)
        target = self.work_dir / task_id
        if target.exists():
            await self.release(task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        await git(
            "worktree",
            "add",
            "--detach",
            "--force",
            str(target.resolve()),  # relative paths resolve against git's cwd, not ours
            sha,
            cwd=cache,
            timeout=self._timeout,
        )
        log.info("workspace.ready", repo=ref.slug, sha=sha[:12], path=str(target))
        return target

    async def release(self, task_id: str) -> None:
        """Remove one checkout. The cached objects stay for the next task.

        Releasing must never be the thing that fails a task: the checkout is
        already gone by the time the cache is tidied, and a stale worktree
        record only costs a line in git's bookkeeping.
        """
        target = self.work_dir / task_id
        if not target.exists():
            return
        # Ask git where the shared repository is rather than deriving it from
        # the .git pointer file; the layout of that file is git's business.
        cache: Path | None = None
        try:
            common = await git("rev-parse", "--git-common-dir", cwd=target, timeout=30.0)
            candidate = Path(common.strip())
            cache = candidate if candidate.is_absolute() else (target / candidate).resolve()
        except GitError:
            cache = None

        shutil.rmtree(target, ignore_errors=True)
        if cache is not None and cache.exists():
            try:
                await git("worktree", "prune", cwd=cache, timeout=self._timeout)
            except GitError as exc:
                log.warning("workspace.prune_failed", task_id=task_id, error=str(exc))
        log.info("workspace.released", task_id=task_id)

    async def _ensure_cache(self, ref: RepoRef, sha: str) -> Path:
        cache = self.cache_dir / ref.cache_name
        lock = self._locks.setdefault(ref.cache_name, asyncio.Lock())
        async with lock:
            if not cache.exists():
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                log.info("workspace.cloning", repo=ref.slug)
                await git(
                    "clone",
                    "--bare",
                    "--filter=blob:none",
                    ref.clone_url,
                    str(cache.resolve()),
                    timeout=self._timeout,
                )
            if not await self._has_commit(cache, sha):
                log.info("workspace.fetching", repo=ref.slug, sha=sha[:12])
                await git(
                    "fetch", "--filter=blob:none", "origin", sha, cwd=cache, timeout=self._timeout
                )
        return cache

    async def _has_commit(self, cache: Path, sha: str) -> bool:
        try:
            await git("cat-file", "-e", f"{sha}^{{commit}}", cwd=cache, timeout=30.0)
        except GitError:
            return False
        return True
