"""The container script, and what the sandbox reports back.

No Docker here: the script is a pure function of the instance and the patch,
and the runner's behaviour is exercised by replacing the one method that shells
out. The real thing is validated by hand against the gold patch, which is the
only check that proves the harness can tell a fix from a non-fix.
"""

import base64

import pytest

from repoaegis.eval.dataset import Instance
from repoaegis.eval.sandbox import (
    APPLY_FAILED,
    DockerSandbox,
    RunResult,
    build_script,
    command_for,
)

PATCH = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a\n+b\n"

INSTANCE = Instance(
    instance_id="psf__requests-1142",
    repo="psf/requests",
    base_commit="2262" + "0" * 36,
    problem_statement="x",
    gold_patch="",
    test_patch="diff --git a/test_x.py b/test_x.py\n+def test_new(): pass\n",
    fail_to_pass=("test_x.py::TestCase::test_new",),
    pass_to_pass=("test_x.py::test_old",),
)


def decoded(script: str, marker: str) -> str:
    """Pull one embedded file back out of the script."""
    line = next(ln for ln in script.splitlines() if ln.endswith(f"> {marker}"))
    payload = line.split()[2]
    return base64.b64decode(payload).decode("utf-8")


def test_the_patches_travel_base64_encoded() -> None:
    """A diff contains quotes and backslashes; base64 cannot be misread."""
    script = build_script(INSTANCE, PATCH)
    assert decoded(script, "/tmp/aegis/model.patch") == PATCH
    assert decoded(script, "/tmp/aegis/test.patch") == INSTANCE.test_patch
    assert PATCH not in script  # never inlined raw


def test_the_tree_is_reset_to_the_instances_commit_first() -> None:
    script = build_script(INSTANCE, PATCH)
    lines = script.splitlines()
    reset = next(i for i, ln in enumerate(lines) if "git checkout -f" in ln)
    apply_ours = next(i for i, ln in enumerate(lines) if "model.patch" in ln and "git apply" in ln)
    assert INSTANCE.base_commit in lines[reset]
    assert reset < apply_ours


def test_the_reference_tests_are_applied_after_our_patch() -> None:
    """Otherwise our patch could edit the very tests that judge it."""
    lines = build_script(INSTANCE, PATCH).splitlines()
    ours = next(i for i, ln in enumerate(lines) if "model.patch" in ln and "git apply" in ln)
    theirs = next(i for i, ln in enumerate(lines) if "test.patch" in ln and "git apply" in ln)
    assert ours < theirs


def test_a_patch_that_does_not_apply_stops_the_run_with_a_marker() -> None:
    script = build_script(INSTANCE, PATCH)
    assert APPLY_FAILED in script
    assert "exit 3" in script


def test_both_test_lists_are_run() -> None:
    script = build_script(INSTANCE, PATCH)
    assert "test_x.py::TestCase::test_new" in script
    assert "test_x.py::test_old" in script
    assert "python -m pytest" in script


def test_the_command_falls_back_to_pytest() -> None:
    assert "pytest" in command_for("psf/requests")
    assert "pytest" in command_for("some/unknown-repo")


class FakeSandbox(DockerSandbox):
    """Replaces the one method that shells out."""

    def __init__(self, replies: list[tuple[int, str]]) -> None:
        super().__init__(command="docker")
        self.replies = replies
        self.calls: list[tuple[str, ...]] = []
        self.stdin: list[str] = []

    async def _docker(self, *args: str, stdin: str | None = None) -> tuple[int, str]:
        self.calls.append(args)
        if stdin is not None:
            self.stdin.append(stdin)
        return self.replies.pop(0)


PYTEST_OK = """\
============================= test session starts ==============================
=========================== short test summary info ============================
PASSED test_x.py::TestCase::test_new
PASSED test_x.py::test_old
"""


async def test_a_successful_run_reports_the_output_and_that_the_patch_applied() -> None:
    box = FakeSandbox([(0, "image found"), (0, PYTEST_OK)])
    run = await box.run_tests(INSTANCE, PATCH)

    assert run.patch_applied and run.exit_code == 0 and run.ran_tests
    assert "PASSED test_x.py::TestCase::test_new" in run.output
    assert box.calls[0][:2] == ("image", "inspect")  # the image was already there
    assert "--network" in box.calls[1] and "none" in box.calls[1]
    assert INSTANCE.base_commit in box.stdin[0]


async def test_a_refused_patch_is_reported_as_such_not_as_a_wrong_fix() -> None:
    box = FakeSandbox([(0, "image found"), (3, f"error: patch failed\n{APPLY_FAILED}\n")])
    run = await box.run_tests(INSTANCE, PATCH)

    assert not run.patch_applied and not run.ran_tests
    assert run.exit_code == 3


async def test_a_missing_image_is_pulled_before_the_run() -> None:
    box = FakeSandbox([(1, "no such image"), (0, "pulled"), (0, PYTEST_OK)])
    await box.run_tests(INSTANCE, PATCH)
    assert box.calls[1][0] == "pull" and box.calls[1][1] == INSTANCE.image


def test_ran_tests_needs_both_a_patch_and_a_session() -> None:
    assert not RunResult("no session here", 0, True, 1.0).ran_tests
    assert not RunResult("= test session starts =", 3, False, 1.0).ran_tests
    assert RunResult("= test session starts =", 1, True, 1.0).ran_tests


@pytest.mark.parametrize("command", ["docker", "wsl -d Ubuntu -- docker"])
def test_the_docker_command_may_carry_a_prefix(command: str) -> None:
    """On this Windows machine the engine lives inside WSL."""
    box = DockerSandbox(command=command)
    assert box._command[-1] == "docker"


def test_our_edits_to_test_files_are_discarded_before_the_reference_tests() -> None:
    """A real run found this: the agent added its own tests to the same file the
    benchmark patches, the reference patch then failed to apply, and a correct
    fix scored zero because the target test no longer existed."""
    lines = build_script(INSTANCE, PATCH).splitlines()
    ours = next(i for i, ln in enumerate(lines) if "model.patch" in ln and "git apply" in ln)
    reset = next(i for i, ln in enumerate(lines) if "git checkout" in ln and "test_x.py" in ln)
    theirs = next(i for i, ln in enumerate(lines) if "test.patch" in ln and "git apply" in ln)

    assert ours < reset < theirs
    assert INSTANCE.base_commit in lines[reset]


def test_an_instance_without_a_test_patch_needs_no_reset() -> None:
    from dataclasses import replace

    script = build_script(replace(INSTANCE, test_patch=""), PATCH)
    # The only checkout left is the initial reset of the whole tree.
    checkouts = [ln for ln in script.splitlines() if ln.startswith("git checkout")]
    assert checkouts == [f"git checkout -f {INSTANCE.base_commit} -- . >/dev/null 2>&1 || true"]
