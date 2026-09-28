"""A throwaway Docker container the repository's tests run in.

Running a repository's tests means executing code we did not write and have
not read: a ``conftest.py`` can do anything Python can. So tests run in a
container that is switched off from the network, whose root filesystem is
read-only, with every Linux capability dropped, a memory and process cap, and
a deadline after which it is killed. That is the industry's floor for a
single-tenant sandbox (hardened one-shot Docker; see the survey, 3.11), and it
is a handful of ``docker run`` flags rather than new machinery.

Only the Docker CLI is used, through ``asyncio`` subprocesses, so the same
class works whether the engine is local or -- as on the development machine --
inside WSL, reached through a command prefix such as ``wsl -d Ubuntu --``. The
code goes in as a tar stream on stdin (``docker cp -``), not a bind mount:
mounts across the WSL boundary are slow, and a test run must not be able to
leave ``__pycache__`` or scratch files in the checkout the reviewer's diff is
taken from.

Dependencies are the slow part. They are installed once per repository, in a
container that *is* allowed network, and the result is committed as an image
keyed by a hash of the dependency manifests. Every later round starts from
that image in seconds. This is the one place the sandbox talks to the network,
and it is a build step, never a test run.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tarfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

from repoaegis.agent.tools import Workspace

log = structlog.get_logger(__name__)

TESTBED = "/testbed"
# Directories that never go into the container. ``.repoaegis`` holds our own
# run logs; the rest are caches or the checkout's own git metadata.
EXCLUDED_DIRS = frozenset({".git", ".repoaegis", "__pycache__", ".venv", "venv", "node_modules"})
# What decides which dependencies get installed, and therefore the image key.
MANIFESTS = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "requirements_dev.txt",
    "requirements_test.txt",
    "tox.ini",
    "Pipfile",
)
INSTALL_SCRIPT = r"""
set -e
cd /testbed
export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_INPUT=1
python -m pip install -q --upgrade pip >/dev/null 2>&1 || true
if [ -f pyproject.toml ] || [ -f setup.py ]; then
  python -m pip install -q -e ".[test]" 2>/dev/null \
  || python -m pip install -q -e ".[tests]" 2>/dev/null \
  || python -m pip install -q -e ".[dev]" 2>/dev/null \
  || python -m pip install -q -e .
fi
for f in requirements-dev.txt requirements_dev.txt requirements-test.txt \
         requirements_test.txt requirements.txt; do
  [ -f "$f" ] && python -m pip install -q -r "$f"
done
python -m pip install -q pytest
python -c "import pytest; print('pytest', pytest.__version__)"
"""

Runner = Callable[[Sequence[str], bytes | None, float], Awaitable[tuple[int, bytes, bytes]]]


class SandboxError(RuntimeError):
    pass


class SandboxTimeout(SandboxError):
    pass


async def _subprocess_runner(
    argv: Sequence[str], stdin: bytes | None, timeout: float
) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin), timeout)
    except TimeoutError:
        process.kill()
        raise SandboxTimeout(f"{argv[-1] if argv else 'docker'} exceeded {timeout:.0f}s") from None
    return process.returncode or 0, out, err


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").replace(chr(0), "")


class Docker:
    """The Docker CLI, optionally behind a prefix that reaches another host."""

    def __init__(self, prefix: Sequence[str] = (), *, runner: Runner = _subprocess_runner) -> None:
        self._prefix = tuple(prefix)
        self._runner = runner

    async def run(
        self, *args: str, stdin: bytes | None = None, timeout: float = 120.0
    ) -> tuple[int, str, str]:
        """Exit code, stdout, stderr. Only stdout is ever parsed.

        The two streams stay apart because wsl.exe writes its own notices --
        in UTF-16, hence the NUL stripping -- on stderr, ahead of anything the
        engine says; mixed into stdout they would corrupt a container id or
        a JUnit file.
        """
        code, out, err = await self._runner([*self._prefix, "docker", *args], stdin, timeout)
        return code, _text(out), _text(err)

    async def check(self, *args: str, stdin: bytes | None = None, timeout: float = 120.0) -> str:
        code, out, err = await self.run(*args, stdin=stdin, timeout=timeout)
        if code != 0:
            tail = (err.strip() or out.strip()).splitlines()[-3:]
            raise SandboxError(f"docker {args[0]} failed ({code}): {' | '.join(tail)}")
        return out


@dataclass(frozen=True, slots=True)
class Limits:
    memory: str = "4g"
    pids: int = 512
    cpus: str = "2"
    testbed_size: str = "2g"


class DockerSandbox:
    def __init__(self, docker: Docker, *, limits: Limits | None = None) -> None:
        self._docker = docker
        self._limits = limits or Limits()

    async def start(self, image: str, *, network: bool = False, writable: bool = False) -> str:
        """Start a container that idles until told what to run. Returns its id.

        ``network`` and ``writable`` are for the dependency build only; a test
        run gets neither.
        """
        lim = self._limits
        flags = [
            "run",
            "-d",
            "--rm",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            lim.memory,
            "--pids-limit",
            str(lim.pids),
            "--cpus",
            lim.cpus,
            "-e",
            "HOME=/tmp",
            "-w",
            TESTBED,  # created by docker when missing, so the build needs no mkdir
        ]
        if not network:
            flags += ["--network", "none"]
        if not writable:
            # Nothing outside the code and scratch space can be written to.
            flags += [
                "--read-only",
                "--tmpfs",
                "/tmp:rw,exec,size=1g",
                "--tmpfs",
                f"{TESTBED}:rw,exec,size={lim.testbed_size}",
            ]
        out = await self._docker.check(*flags, image, "sleep", "infinity")
        container = out.strip().splitlines()[-1]
        log.info("sandbox.started", container=container[:12], image=image, network=network)
        return container

    async def put_tree(self, container: str, archive: bytes) -> None:
        """Unpack a tar archive into the container's ``/testbed``.

        Streamed to ``tar`` inside the container rather than ``docker cp``:
        ``cp`` refuses any container whose root is read-only, even into a
        writable tmpfs, and the read-only root is the point.
        """
        await self._docker.check(
            "exec", "-i", container, "tar", "-xf", "-", "-C", TESTBED, stdin=archive, timeout=300
        )

    async def exec(self, container: str, script: str, *, timeout: float) -> tuple[int, str]:
        """Run a shell script inside; returns the exit code and its combined output.

        The merge happens inside the container so the host side's stderr (the
        CLI's own complaints) stays out of a test log.
        """
        code, out, err = await self._docker.run(
            "exec", container, "sh", "-c", f"({script}) 2>&1", timeout=timeout
        )
        return code, out if out.strip() or code == 0 else err

    async def read_file(self, container: str, path: str) -> bytes | None:
        code, out, _ = await self._docker.run("exec", container, "cat", path, timeout=60)
        return out.encode("utf-8") if code == 0 else None

    async def commit(self, container: str, image: str) -> None:
        await self._docker.check("commit", container, image, timeout=600)

    async def image_exists(self, image: str) -> bool:
        code, _, _ = await self._docker.run("image", "inspect", "--format", "{{.Id}}", image)
        return code == 0

    async def stop(self, container: str) -> None:
        # ``--rm`` on the run means removing is the same as stopping.
        code, _, _ = await self._docker.run("rm", "-f", container, timeout=60)
        if code == 0:
            log.info("sandbox.stopped", container=container[:12])


async def archive_tree(root: Path) -> bytes:
    """A tar of the working tree as it is on disk -- edits included, git excluded.

    Binary files are kept (test fixtures are often images or archives), which
    is why this does not reuse ``Workspace.walk``. Symbolic links are taken
    from git's index rather than from the filesystem: a Windows checkout
    without symlink support writes them as ordinary files holding the target
    path, and shipped that way a ``tests/certs -> ../fixtures`` link becomes a
    one-line text file and every test behind it fails.
    """
    links = await _git_symlinks(root)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for folder, subdirs, files in os.walk(root):
            subdirs[:] = sorted(d for d in subdirs if d not in EXCLUDED_DIRS)
            for name in sorted(files):
                path = Path(folder) / name
                rel = path.relative_to(root).as_posix()
                if rel in links:
                    info = tarfile.TarInfo(rel)
                    info.type = tarfile.SYMTYPE
                    info.linkname = links[rel]
                    tar.addfile(info)
                    continue
                tar.add(path, arcname=rel, recursive=False)
    return buffer.getvalue()


async def _git_symlinks(root: Path) -> dict[str, str]:
    """path -> link target for every symlink git tracks under ``root``; {} if not a repo."""
    try:
        process = await asyncio.create_subprocess_exec(
            "git",
            "ls-files",
            "-s",
            "-z",
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(process.communicate(), 60)
    except (OSError, TimeoutError):
        return {}
    if process.returncode != 0:
        return {}
    links: dict[str, str] = {}
    for entry in out.decode("utf-8", "replace").split("\0"):
        if not entry.startswith("120000 "):
            continue
        _meta, _, rel = entry.partition("\t")
        target = root / rel
        try:
            if target.is_symlink():
                links[rel] = os.readlink(target)
            elif target.is_file():
                links[rel] = target.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return links


def manifest_digest(root: Path, base_image: str) -> str:
    """Twelve hex characters that change when the dependencies would."""
    digest = hashlib.sha256(base_image.encode())
    for name in MANIFESTS:
        path = root / name
        if path.is_file():
            digest.update(name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


class EnvironmentImages:
    """Builds and caches ``repoaegis/env:<digest>`` images, one per dependency set."""

    def __init__(
        self, sandbox: DockerSandbox, *, base_image: str, build_timeout: float = 1800.0
    ) -> None:
        self._sandbox = sandbox
        self._base = base_image
        self._build_timeout = build_timeout
        self._locks: dict[str, asyncio.Lock] = {}

    def image_for(self, ws: Workspace) -> str:
        return f"repoaegis/env:{manifest_digest(ws.root, self._base)}"

    async def ensure(self, ws: Workspace) -> str:
        """Return an image with this tree's dependencies installed, building it once."""
        image = self.image_for(ws)
        lock = self._locks.setdefault(image, asyncio.Lock())
        async with lock:
            if await self._sandbox.image_exists(image):
                return image
            log.info("sandbox.building", image=image, base=self._base)
            container = await self._sandbox.start(self._base, network=True, writable=True)
            try:
                await self._sandbox.put_tree(container, await archive_tree(ws.root))
                code, out = await self._sandbox.exec(
                    container, INSTALL_SCRIPT, timeout=self._build_timeout
                )
                if code != 0:
                    tail = "\n".join(out.strip().splitlines()[-15:])
                    raise SandboxError(f"dependency install failed ({code}):\n{tail}")
                await self._sandbox.commit(container, image)
            finally:
                await self._sandbox.stop(container)
            log.info("sandbox.built", image=image)
            return image
