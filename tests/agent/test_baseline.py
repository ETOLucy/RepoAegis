"""What was broken before the patch is not the patch's regression."""

from pathlib import Path

import pytest

from repoaegis.agent.plan import Plan
from repoaegis.agent.rounds import failing_ids, parse_junitxml
from repoaegis.agent.sandbox import SandboxTimeout
from repoaegis.agent.testing import DockerVerifier
from repoaegis.agent.tools import Workspace
from repoaegis.agent.workspace import git


def suite(*cases: tuple[str, str | None]) -> bytes:
    """A JUnit file; a case with a message fails, one with ``None`` passes."""
    body = "".join(
        f'<testcase classname="tests.test_x" name="{name}">'
        + (f'<failure message="{msg}"/>' if msg else "")
        + "</testcase>"
        for name, msg in cases
    )
    return f'<testsuite tests="{len(cases)}">{body}</testsuite>'.encode()


PATCHED = suite(
    ("test_fix", None),
    ("test_net", "ConnectionError: no route"),
    ("test_new", "AssertionError: 1 != 2"),
)
BASE = suite(
    ("test_fix", "AssertionError: old bug"),
    ("test_net", "ConnectionError: no route"),
    ("test_new", None),
)


# --- the parser ------------------------------------------------------------


def test_failing_ids_are_uncapped_and_use_the_same_naming_as_the_report() -> None:
    assert failing_ids(BASE.decode()) == {"tests.test_x::test_fix", "tests.test_x::test_net"}
    report = parse_junitxml(PATCHED.decode())
    assert {f.test for f in report.failures} == {"tests.test_x::test_net", "tests.test_x::test_new"}


def test_known_failures_are_counted_as_preexisting_not_as_failures() -> None:
    report = parse_junitxml(PATCHED.decode(), known_failing=failing_ids(BASE.decode()))
    assert (report.passed, report.failed, report.preexisting) == (1, 1, 1)
    assert [f.test for f in report.failures] == ["tests.test_x::test_new"]
    assert "1 tests fail on the unchanged code as well" in report.render()


def test_the_kind_is_taken_from_the_first_line_only() -> None:
    xml = suite(("t", "failed on setup with &quot;file x.py, line 1&#10;def t(httpbin): ...&quot;"))
    (failure,) = parse_junitxml(xml.decode()).failures
    assert failure.kind == ""  # no "Kind:" prefix on the first line
    assert "\n" not in failure.kind


# --- the verifier ------------------------------------------------------------


class TwoTreeSandbox:
    """Answers the patched run first, then the baseline run; records the trees."""

    def __init__(
        self, patched: bytes | None, base: bytes | None, *, base_timeout: bool = False
    ) -> None:
        self.xmls = [patched, base]
        self.base_timeout = base_timeout
        self.trees: list[bytes] = []
        self.started = 0

    async def start(self, image: str, *, network: bool = False, writable: bool = False) -> str:
        self.started += 1
        return f"c{self.started}"

    async def put_tree(self, container: str, archive: bytes) -> None:
        self.trees.append(archive)

    async def exec(self, container: str, script: str, *, timeout: float) -> tuple[int, str]:
        if self.base_timeout and container == "c2":
            raise SandboxTimeout("baseline exceeded")
        return 1, f"ran in {container}"

    async def read_file(self, container: str, path: str) -> bytes | None:
        return (
            self.xmls[int(container[1:]) - 1]
            if int(container[1:]) <= len(self.xmls)
            else self.xmls[-1]
        )

    async def stop(self, container: str) -> None:
        pass


class Images:
    async def ensure(self, ws: Workspace) -> str:
        return "repoaegis/env:x"


async def checkout(tmp_path: Path) -> Workspace:
    """A git repository with a committed file, then an uncommitted edit on top."""
    await git("init", "--quiet", str(tmp_path), timeout=30)
    (tmp_path / "x.py").write_text("v = 1\n", encoding="utf-8")
    await git("add", "-A", cwd=tmp_path, timeout=30)
    await git(
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "base",
        cwd=tmp_path,
        timeout=30,
    )
    (tmp_path / "x.py").write_text("v = 2\n", encoding="utf-8")
    return Workspace(root=tmp_path)


def verifier(sandbox: TwoTreeSandbox) -> DockerVerifier:
    return DockerVerifier(sandbox, Images(), timeout_seconds=60)  # type: ignore[arg-type]


async def test_a_red_run_is_compared_against_the_committed_tree(tmp_path: Path) -> None:
    ws = await checkout(tmp_path)
    sandbox = TwoTreeSandbox(PATCHED, BASE)

    report = await verifier(sandbox).run(ws, Plan(diagnosis="d", approach="a"), ["x.py"])

    assert sandbox.started == 2
    assert b"v = 2" in sandbox.trees[0] and b"v = 1" in sandbox.trees[1]  # edited, then committed
    assert (report.failed, report.preexisting) == (1, 1)
    assert [f.test for f in report.failures] == ["tests.test_x::test_new"]
    assert (tmp_path / ".repoaegis" / "runs" / "1" / "baseline.log").read_text() == "ran in c2"
    assert list((tmp_path / ".repoaegis" / "baseline").glob("*.xml"))


async def test_the_baseline_is_cached_across_rounds(tmp_path: Path) -> None:
    ws = await checkout(tmp_path)
    sandbox = TwoTreeSandbox(PATCHED, BASE)
    v = verifier(sandbox)
    await v.run(ws, Plan(diagnosis="d", approach="a"), ["x.py"])
    sandbox.xmls = [PATCHED]  # only a patched answer is available now
    report = await v.run(ws, Plan(diagnosis="d", approach="a"), ["x.py"])

    assert sandbox.started == 3  # two rounds plus one baseline, not two
    assert report.preexisting == 1


async def test_a_green_run_never_pays_for_a_baseline(tmp_path: Path) -> None:
    ws = await checkout(tmp_path)
    sandbox = TwoTreeSandbox(suite(("t", None)), BASE)
    report = await verifier(sandbox).run(ws, Plan(diagnosis="d", approach="a"), ["x.py"])
    assert report.ok and sandbox.started == 1


async def test_without_a_git_checkout_nothing_is_excused(tmp_path: Path) -> None:
    (tmp_path / "x.py").write_text("v = 2\n")
    sandbox = TwoTreeSandbox(PATCHED, BASE)
    report = await verifier(sandbox).run(
        Workspace(root=tmp_path), Plan(diagnosis="d", approach="a"), []
    )
    assert sandbox.started == 1 and report.failed == 2 and report.preexisting == 0


async def test_a_baseline_timeout_excuses_nothing(tmp_path: Path) -> None:
    ws = await checkout(tmp_path)
    sandbox = TwoTreeSandbox(PATCHED, BASE, base_timeout=True)
    report = await verifier(sandbox).run(ws, Plan(diagnosis="d", approach="a"), ["x.py"])
    assert report.failed == 2 and report.preexisting == 0


@pytest.mark.parametrize("known", [set(), {"nothing::matches"}])
def test_an_unrelated_known_set_changes_nothing(known: set[str]) -> None:
    report = parse_junitxml(PATCHED.decode(), known_failing=known)
    assert report.failed == 2 and report.preexisting == 0
