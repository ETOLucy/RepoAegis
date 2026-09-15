"""Running the benchmark's tests against our patch, inside the official image.

The agent never enters the container. It reads and edits in a git worktree on
the host, where nothing is executed; the container's only job is to apply the
resulting diff and run tests. That split is what let the earlier rounds ship
without a sandbox at all, and it is why the sandbox can stay this small.

The procedure follows SWE-bench's own, and each step exists for a reason:

1. reset the tree to ``base_commit`` -- the image carries an empty marker commit
   on top, and a stray edit from a previous run must not leak into this one;
2. apply our patch, and report a refusal as its own outcome. "The diff did not
   apply" is a different failure from "the fix was wrong", and a harness that
   merges them is lying about where the agent is weak;
3. apply the benchmark's ``test_patch`` *after* ours, so the tests are the
   reference ones no matter what our patch touched;
4. run the named tests and hand the raw output back for parsing.

Files reach the container base64-encoded inside the script. A patch is arbitrary
text -- it will contain quotes, backslashes and lines that look like heredoc
terminators -- and base64 is the one encoding that cannot be misread by a shell.

``command`` exists because Docker is not always reachable as a bare ``docker``:
on this Windows machine the engine lives inside WSL, so the prefix is
``wsl -d Ubuntu -- docker``.
"""

from __future__ import annotations

import asyncio
import base64
import shlex
from dataclasses import dataclass
from typing import Final

import structlog

from repoaegis.eval.dataset import Instance

log = structlog.get_logger(__name__)

DEFAULT_COMMAND: Final = "docker"
WORKDIR: Final = "/testbed"

# Test commands differ per project the way build systems do. pytest covers
# every repository added so far; Django's own runner gets an entry when it does.
TEST_COMMAND: Final[dict[str, str]] = {
    "_default": "python -m pytest -rA --no-header -p no:cacheprovider",
}

APPLY_FAILED: Final = "REPOAEGIS_PATCH_DID_NOT_APPLY"


class SandboxError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunResult:
    """The raw result of one container run, before any judging.

    Named RunResult rather than TestRun because pytest tries to collect any
    class whose name starts with Test.
    """

    output: str
    exit_code: int
    patch_applied: bool
    seconds: float

    @property
    def ran_tests(self) -> bool:
        return self.patch_applied and "= test session starts" in self.output


def command_for(repo: str) -> str:
    """Not test_command_for: pytest would try to collect it."""
    return TEST_COMMAND.get(repo, TEST_COMMAND["_default"])


def _embed(path: str, content: str) -> str:
    """A shell line that writes ``content`` to ``path`` without quoting hazards."""
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    return f"printf %s {encoded} | base64 -d > {path}"


def build_script(instance: Instance, patch: str) -> str:
    """The whole run as one shell script, fed to the container on stdin."""
    tests = " ".join(shlex.quote(t) for t in (*instance.fail_to_pass, *instance.pass_to_pass))
    command = command_for(instance.repo)
    return "\n".join(
        [
            "set -u",
            "mkdir -p /tmp/aegis",
            _embed("/tmp/aegis/model.patch", patch),
            _embed("/tmp/aegis/test.patch", instance.test_patch),
            f"cd {WORKDIR}",
            "git config --global --add safe.directory /testbed",
            # Start from the commit the issue was filed against, whatever a
            # previous run or the image build left behind.
            f"git checkout -f {shlex.quote(instance.base_commit)} -- . >/dev/null 2>&1 || true",
            "git clean -fdq -e build -e '*.egg-info' || true",
            # A patch that does not apply is a distinct, reportable outcome.
            "if ! git apply -v /tmp/aegis/model.patch; then",
            f"  echo {APPLY_FAILED}",
            "  exit 3",
            "fi",
            # The reference tests go on last so ours cannot have altered them.
            "git apply -v /tmp/aegis/test.patch || true",
            f"{command} {tests}",
        ]
    )


class DockerSandbox:
    def __init__(
        self,
        *,
        command: str = DEFAULT_COMMAND,
        timeout_seconds: float = 1800.0,
        memory: str = "4g",
        cpus: str = "4",
    ) -> None:
        self._command = shlex.split(command)
        self._timeout = timeout_seconds
        self._memory = memory
        self._cpus = cpus

    async def _docker(self, *args: str, stdin: str | None = None) -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            *self._command,
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        payload = stdin.encode("utf-8") if stdin is not None else None
        try:
            out, _ = await asyncio.wait_for(process.communicate(payload), self._timeout)
        except TimeoutError:
            process.kill()
            raise SandboxError(f"docker {args[0]} timed out after {self._timeout:.0f}s") from None
        return process.returncode or 0, out.decode("utf-8", "replace")

    async def available(self) -> bool:
        code, _ = await self._docker("version", "--format", "{{.Server.Version}}")
        return code == 0

    async def has_image(self, image: str) -> bool:
        code, _ = await self._docker("image", "inspect", image)
        return code == 0

    async def pull(self, image: str) -> None:
        if await self.has_image(image):
            return
        log.info("sandbox.pulling", image=image)
        code, out = await self._docker("pull", image)
        if code != 0:
            raise SandboxError(f"cannot pull {image}: {out.strip().splitlines()[-1:]}")

    async def run_tests(self, instance: Instance, patch: str) -> RunResult:
        """Apply the patch in a throwaway container and run the benchmark's tests."""
        started = asyncio.get_running_loop().time()
        await self.pull(instance.image)
        code, output = await self._docker(
            "run",
            "--rm",
            "-i",
            "--network",
            "none",  # the tests must not reach the internet
            "--memory",
            self._memory,
            "--cpus",
            self._cpus,
            instance.image,
            "bash",
            "-lc",
            "bash -s",
            stdin=build_script(instance, patch),
        )
        elapsed = asyncio.get_running_loop().time() - started
        applied = APPLY_FAILED not in output
        log.info(
            "sandbox.tests_finished",
            instance=instance.instance_id,
            exit_code=code,
            applied=applied,
            seconds=round(elapsed, 1),
        )
        return RunResult(output=output, exit_code=code, patch_applied=applied, seconds=elapsed)
