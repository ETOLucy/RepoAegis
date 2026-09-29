"""URL parsing, git plumbing and issue fetching -- everything here runs offline
except the clone itself, which is exercised by hand rather than in CI."""

from pathlib import Path

import httpx
import pytest

from repoaegis.agent.github import GitHub, GitHubError
from repoaegis.agent.workspace import (
    GitError,
    RepoRef,
    Workspaces,
    git,
    parse_issue_url,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/psf/requests/issues/6800", RepoRef("psf", "requests", 6800)),
        ("https://github.com/psf/requests/pull/12", RepoRef("psf", "requests", 12)),
        ("https://github.com/psf/requests", RepoRef("psf", "requests", None)),
        ("https://github.com/psf/requests.git", RepoRef("psf", "requests", None)),
        ("http://github.com/a-b/c.d/issues/1/", RepoRef("a-b", "c.d", 1)),
    ],
)
def test_issue_urls_parse(url: str, expected: RepoRef) -> None:
    assert parse_issue_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/psf/requests/issues/1",
        "https://github.com/psf",
        "not a url",
        "https://github.com/psf/requests/issues/abc",
    ],
)
def test_other_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError, match="not a GitHub"):
        parse_issue_url(url)


def test_cache_name_is_flat_and_safe() -> None:
    ref = parse_issue_url("https://github.com/psf/requests/issues/1")
    assert ref.cache_name == "psf__requests.git"
    assert ref.clone_url == "https://github.com/psf/requests.git"
    assert "/" not in ref.cache_name


async def test_git_reports_failures_with_the_reason(tmp_path: Path) -> None:
    with pytest.raises(GitError) as caught:
        await git("rev-parse", "HEAD", cwd=tmp_path, timeout=30)
    assert "git rev-parse failed" in str(caught.value)


async def test_git_errors_keep_the_lines_before_the_summary(tmp_path: Path) -> None:
    """Git states the cause first and summarises last. A push refused by
    GitHub ends in "failed to push some refs"; the reason is the line above,
    and dropping it once turned a scope problem into a mystery."""
    await git("init", "--quiet", str(tmp_path), timeout=30)  # no commits, so no HEAD
    with pytest.raises(GitError) as caught:
        await git("push", str(tmp_path / "r.git"), "HEAD:refs/heads/x", cwd=tmp_path, timeout=30)
    message = str(caught.value)
    assert "src refspec HEAD does not match any" in message  # the cause
    assert "failed to push some refs" in message  # the summary


async def test_git_runs_without_a_shell(tmp_path: Path) -> None:
    await git("init", "--quiet", str(tmp_path / "repo"), timeout=30)
    assert (tmp_path / "repo" / ".git").exists()
    # A repository name containing shell metacharacters is just a name.
    await git("init", "--quiet", str(tmp_path / "weird; rm -rf x"), timeout=30)
    assert (tmp_path / "weird; rm -rf x").exists()


async def test_releasing_a_checkout_that_is_not_there_is_quiet(tmp_path: Path) -> None:
    spaces = Workspaces(tmp_path / "cache", tmp_path / "work")
    await spaces.release("never-existed")  # must not raise


async def test_release_removes_the_checkout(tmp_path: Path) -> None:
    spaces = Workspaces(tmp_path / "cache", tmp_path / "work")
    checkout = tmp_path / "work" / "task-1"
    checkout.mkdir(parents=True)
    (checkout / "file.py").write_text("x", encoding="utf-8")

    await spaces.release("task-1")
    assert not checkout.exists()


def _github(handler: object, token: str = "") -> GitHub:
    return GitHub(token=token, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_issue_is_fetched_and_trimmed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/psf/requests/issues/6800"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "number": 6800,
                "title": "  redirects drop the fragment  ",
                "body": "x" * 30_000,
                "state": "open",
                "html_url": "https://github.com/psf/requests/issues/6800",
            },
        )

    issue = await _github(handler).issue(RepoRef("psf", "requests", 6800))
    assert issue.title == "redirects drop the fragment"
    assert issue.is_open
    assert issue.body.endswith("[issue body truncated]")
    assert len(issue.body) < 21_000


async def test_a_token_is_sent_when_configured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"number": 1, "title": "t", "body": "", "state": "closed"})

    issue = await _github(handler, token="secret").issue(RepoRef("o", "r", 1))
    assert not issue.is_open


async def test_a_missing_issue_says_so() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(GitHubError, match="not found: o/r#9"):
        await _github(handler).issue(RepoRef("o", "r", 9))


async def test_rate_limiting_names_the_fix() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})

    with pytest.raises(GitHubError, match="GITHUB_TOKEN"):
        await _github(handler).issue(RepoRef("o", "r", 9))


async def test_a_url_without_a_number_cannot_be_fetched() -> None:
    def handler(_: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("should not reach the network")

    with pytest.raises(GitHubError, match="no issue number"):
        await _github(handler).issue(RepoRef("o", "r", None))


async def test_release_prunes_a_real_worktree(tmp_path: Path) -> None:
    """The cache's bookkeeping must be tidied, not just the directory removed."""
    origin = tmp_path / "origin"
    await git("init", "--quiet", "--bare", str(origin), timeout=30)
    seed = tmp_path / "seed"
    await git("clone", "--quiet", str(origin), str(seed), timeout=30)
    await git(
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "--quiet",
        "--allow-empty",
        "-m",
        "first",
        cwd=seed,
        timeout=30,
    )
    await git("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=seed, timeout=30)

    work = tmp_path / "work"
    work.mkdir()
    await git("worktree", "add", "--detach", str(work / "task-1"), "main", cwd=origin, timeout=30)
    assert (work / "task-1").exists()

    await Workspaces(tmp_path / "cache", work).release("task-1")

    assert not (work / "task-1").exists()
    listed = await git("worktree", "list", cwd=origin, timeout=30)
    assert "task-1" not in listed


async def _repo_with_base(root: Path) -> Path:
    await git("init", "--quiet", str(root), timeout=30)
    (root / "a.py").write_text("base\n", encoding="utf-8")
    (root / "gone.py").write_text("to be deleted\n", encoding="utf-8")
    await git("add", "-A", cwd=root, timeout=30)
    await git(
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "base",
        cwd=root,
        timeout=30,
    )
    return root


async def test_a_checkpoint_is_restored_exactly_without_moving_head(tmp_path: Path) -> None:
    """The snapshot of a finished round: edits, new files and deletions, HEAD untouched."""
    spaces = Workspaces(tmp_path / "cache", tmp_path / "work")
    root = await _repo_with_base(spaces.work_dir / "t1")
    head = (await git("rev-parse", "HEAD", cwd=root, timeout=30)).strip()
    (root / "a.py").write_text("round one\n", encoding="utf-8")
    (root / "new.py").write_text("added in round one\n", encoding="utf-8")
    (root / "gone.py").unlink()

    await spaces.checkpoint("t1", "round-1")
    # A later round that died half-way: mangled, stray, and the deletion undone.
    (root / "a.py").write_text("half-made\n", encoding="utf-8")
    (root / "stray.py").write_text("x\n", encoding="utf-8")
    (root / "gone.py").write_text("back?\n", encoding="utf-8")

    assert await spaces.restore("t1", "round-1")

    assert (root / "a.py").read_text(encoding="utf-8") == "round one\n"
    assert (root / "new.py").read_text(encoding="utf-8") == "added in round one\n"
    assert not (root / "stray.py").exists() and not (root / "gone.py").exists()
    assert (await git("rev-parse", "HEAD", cwd=root, timeout=30)).strip() == head
    assert "round one" in await spaces.diff("t1")  # still uncommitted work against the base


async def test_restoring_without_a_checkpoint_gives_a_clean_base(tmp_path: Path) -> None:
    spaces = Workspaces(tmp_path / "cache", tmp_path / "work")
    root = await _repo_with_base(spaces.work_dir / "t1")
    (root / "a.py").write_text("half-made\n", encoding="utf-8")
    (root / "stray.py").write_text("x\n", encoding="utf-8")

    assert await spaces.restore("t1", None)
    assert not await spaces.restore("t1", "round-9")  # unknown name: clean, but reported

    assert (root / "a.py").read_text(encoding="utf-8") == "base\n"
    assert not (root / "stray.py").exists()


async def test_committing_twice_yields_the_same_commit(tmp_path: Path) -> None:
    """A redone delivery finds its own commit and does not stack an empty one."""
    spaces = Workspaces(tmp_path / "cache", tmp_path / "work")
    root = await _repo_with_base(spaces.work_dir / "t1")
    (root / "a.py").write_text("fixed\n", encoding="utf-8")

    first = await spaces.commit("t1", message="fix", branch="repoaegis/t1")
    second = await spaces.commit("t1", message="fix", branch="repoaegis/t1")

    assert first == second
    log = await git("log", "--oneline", cwd=root, timeout=30)
    assert len(log.strip().splitlines()) == 2  # base + fix, nothing empty on top
