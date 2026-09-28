"""The sandbox drives the Docker CLI; here the CLI is a recorder."""

import io
import tarfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from repoaegis.agent.sandbox import (
    Docker,
    DockerSandbox,
    EnvironmentImages,
    Limits,
    SandboxError,
    archive_tree,
    manifest_digest,
)
from repoaegis.agent.tools import Workspace

Answer = tuple[int, bytes]  # exit code, stdout


class Recorder:
    """A fake ``docker``: records argv and stdin, answers from a script."""

    def __init__(self, answers: dict[str, Answer | list[Answer]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.stdins: list[bytes | None] = []
        self.answers = answers or {}

    async def __call__(
        self, argv: Sequence[str], stdin: bytes | None, timeout: float
    ) -> tuple[int, bytes, bytes]:
        self.calls.append(list(argv))
        self.stdins.append(stdin)
        verb = argv[argv.index("docker") + 1]
        answer = self.answers.get(verb, (0, b"c0ffee\n"))
        if isinstance(answer, list):  # one per call, in order; the last one repeats
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        code, out = answer
        # A failing docker command complains on stderr, like the real one.
        return code, (out if code == 0 else b""), (b"" if code == 0 else out)

    def argv(self, verb: str) -> list[str]:
        return next(c for c in self.calls if verb in c)


def sandbox(recorder: Recorder, prefix: Sequence[str] = ()) -> DockerSandbox:
    return DockerSandbox(Docker(prefix, runner=recorder), limits=Limits(memory="1g"))


async def test_the_prefix_reaches_an_engine_on_another_host() -> None:
    rec = Recorder()
    await sandbox(rec, prefix=("wsl", "-d", "Ubuntu", "--")).image_exists("x")
    assert rec.calls[0][:5] == ["wsl", "-d", "Ubuntu", "--", "docker"]


async def test_a_test_container_is_hardened() -> None:
    rec = Recorder()
    container = await sandbox(rec).start("repoaegis/env:abc")

    assert container == "c0ffee"
    argv = rec.argv("run")
    for flag in ("--network", "none", "--read-only", "--cap-drop", "ALL", "--memory", "1g"):
        assert flag in argv
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert argv[-3:] == ["repoaegis/env:abc", "sleep", "infinity"]
    assert "--tmpfs" in argv and any(a.startswith("/testbed:") for a in argv)


async def test_a_build_container_gets_network_and_a_writable_root() -> None:
    rec = Recorder()
    await sandbox(rec).start("python:3.12-slim", network=True, writable=True)
    argv = rec.argv("run")
    assert "--network" not in argv and "--read-only" not in argv
    assert "--cap-drop" in argv  # still no capabilities, even while building


async def test_the_tree_goes_in_on_stdin() -> None:
    rec = Recorder()
    await sandbox(rec).put_tree("c0ffee", b"TARBYTES")
    assert rec.argv("exec")[-8:] == ["exec", "-i", "c0ffee", "tar", "-xf", "-", "-C", "/testbed"]
    assert rec.stdins[0] == b"TARBYTES"


async def test_exec_returns_the_code_and_output_without_raising() -> None:
    rec = Recorder({"exec": (1, b"1 failed\n")})
    code, out = await sandbox(rec).exec("c0ffee", "pytest", timeout=10)
    assert (code, out) == (1, "1 failed\n")
    assert rec.argv("exec")[-3:] == ["sh", "-c", "(pytest) 2>&1"]


async def test_read_file_is_none_when_the_file_is_missing() -> None:
    rec = Recorder({"exec": (1, b"cat: no such file")})
    assert await sandbox(rec).read_file("c0ffee", "/tmp/x") is None


async def test_a_failing_docker_command_raises_with_the_tail() -> None:
    rec = Recorder({"run": (125, b"docker: Error response from daemon: no such image\n")})
    with pytest.raises(SandboxError, match="no such image"):
        await sandbox(rec).start("nope")


# --- the archive ------------------------------------------------------------


async def test_the_archive_keeps_edits_and_binaries_but_not_git_or_our_logs(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("edited\n")
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "img.png").write_bytes(b"\x89PNG")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref\n")
    (tmp_path / ".repoaegis" / "runs" / "1").mkdir(parents=True)
    (tmp_path / ".repoaegis" / "runs" / "1" / "pytest.log").write_text("x")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.pyc").write_bytes(b"\x00")

    with tarfile.open(fileobj=io.BytesIO(await archive_tree(tmp_path))) as tar:
        names = set(tar.getnames())
    assert names == {"src/a.py", "fixtures/img.png"}


async def test_a_symlink_git_tracks_is_shipped_as_a_symlink_even_from_a_flat_checkout(
    tmp_path: Path,
) -> None:
    """Windows git writes a symlink as a text file holding its target; the tar must not."""
    from repoaegis.agent.workspace import git

    await git("init", "--quiet", str(tmp_path), timeout=30)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "ca.crt").write_text("CERT\n")
    # What a checkout without symlink support leaves on disk:
    (tmp_path / "certs").write_text("fixtures\n")
    blob = (await git("hash-object", "-w", "certs", cwd=tmp_path, timeout=30)).strip()
    await git(
        "update-index", "--add", "--cacheinfo", f"120000,{blob},certs", cwd=tmp_path, timeout=30
    )

    with tarfile.open(fileobj=io.BytesIO(await archive_tree(tmp_path))) as tar:
        member = tar.getmember("certs")
    assert member.issym() and member.linkname == "fixtures"


# --- the environment image cache ---------------------------------------------


def test_the_digest_follows_the_manifests_and_the_base_image(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    before = manifest_digest(tmp_path, "python:3.12-slim")
    assert manifest_digest(tmp_path, "python:3.12-slim") == before
    assert manifest_digest(tmp_path, "python:3.11-slim") != before
    (tmp_path / "requirements.txt").write_text("pytest\n")
    assert manifest_digest(tmp_path, "python:3.12-slim") != before
    (tmp_path / "src.py").write_text("code changes do not\n")
    assert len(before) == 12


async def test_an_existing_image_is_reused_without_a_build(tmp_path: Path) -> None:
    rec = Recorder({"image": (0, b"sha256:abc\n")})
    images = EnvironmentImages(sandbox(rec), base_image="python:3.12-slim")
    image = await images.ensure(Workspace(root=tmp_path))
    assert image.startswith("repoaegis/env:")
    assert [c[1] for c in rec.calls] == ["image"]


async def test_a_missing_image_is_built_committed_and_the_builder_removed(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    rec = Recorder({"image": (1, b"No such image\n"), "exec": (0, b"pytest 8.0\n")})
    images = EnvironmentImages(sandbox(rec), base_image="python:3.12-slim")

    image = await images.ensure(Workspace(root=tmp_path))

    verbs = [c[1] for c in rec.calls]
    assert verbs == ["image", "run", "exec", "exec", "commit", "rm"]  # 1st exec unpacks
    run = rec.argv("run")
    assert "--network" not in run  # the build may download
    assert run[-3] == "python:3.12-slim"
    assert rec.argv("commit")[-1] == image
    assert "pip install" in rec.calls[3][-1]


async def test_a_failed_install_raises_and_still_removes_the_builder(tmp_path: Path) -> None:
    rec = Recorder({"image": (1, b""), "exec": (1, b"ERROR: no matching distribution\n")})
    images = EnvironmentImages(sandbox(rec), base_image="python:3.12-slim")
    with pytest.raises(SandboxError, match="no matching distribution"):
        await images.ensure(Workspace(root=tmp_path))
    assert [c[1] for c in rec.calls][-1] == "rm"
    assert "commit" not in [c[1] for c in rec.calls]
