"""Test selection is a function of the plan and the diff, so it can be tested itself."""

import io
import tarfile
from pathlib import Path

import pytest

from repoaegis.agent.plan import Plan
from repoaegis.agent.sandbox import SandboxTimeout
from repoaegis.agent.testing import (
    MAX_SELECTED,
    DockerVerifier,
    by_name_and_import,
    from_plan,
    select_tests,
)
from repoaegis.agent.tools import Workspace


def plan(verification: str = "") -> Plan:
    return Plan(diagnosis="d", approach="a", verification=verification)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    files = {
        "src/requests/models.py": "def prepare_body(): ...\n",
        "src/requests/sessions.py": "from .models import prepare_body\n",
        "src/requests/utils.py": "x = 1\n",
        "tests/test_models.py": "from requests import models\n",
        "tests/test_sessions.py": "from requests.sessions import Session\n",
        "tests/test_requests.py": "import requests\n",
        "tests/unit/test_utils_helpers.py": "from requests.utils import x\n",
        "tests/conftest.py": "",
        "docs/howto.md": "# hi\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Workspace(root=tmp_path)


# --- from the plan --------------------------------------------------------


def test_the_plans_pytest_line_is_kept_when_its_paths_exist(ws: Workspace) -> None:
    assert from_plan(plan("pytest tests/test_models.py -k body"), ws) == [
        "tests/test_models.py",
        "-k",
        "body",
    ]


def test_a_python_m_pytest_line_and_node_ids_are_understood(ws: Workspace) -> None:
    got = from_plan(plan("python -m pytest tests/test_models.py::test_prepare -x -q"), ws)
    assert got == ["tests/test_models.py::test_prepare"]


def test_paths_the_plan_made_up_are_dropped(ws: Workspace) -> None:
    assert from_plan(plan("pytest tests/test_nothing.py tests/test_models.py"), ws) == [
        "tests/test_models.py"
    ]


def test_a_non_pytest_verification_line_contributes_nothing(ws: Workspace) -> None:
    assert from_plan(plan("run the django test suite for admin"), ws) == []
    assert from_plan(plan(""), ws) == []


# --- from the diff --------------------------------------------------------


def test_a_changed_module_pulls_in_tests_named_after_it_and_tests_importing_it(
    ws: Workspace,
) -> None:
    got = by_name_and_import(ws, ["src/requests/models.py"])
    # test_models.py by name; test_sessions.py does not import models directly.
    assert got == ["tests/test_models.py"]


def test_imports_of_the_dotted_module_count(ws: Workspace) -> None:
    got = by_name_and_import(ws, ["src/requests/sessions.py"])
    assert got == ["tests/test_sessions.py"]


def test_a_test_file_prefix_match_counts(ws: Workspace) -> None:
    assert by_name_and_import(ws, ["src/requests/utils.py"]) == ["tests/unit/test_utils_helpers.py"]


def test_a_changed_test_file_selects_itself(ws: Workspace) -> None:
    assert by_name_and_import(ws, ["tests/test_requests.py"]) == ["tests/test_requests.py"]


def test_non_python_changes_select_nothing(ws: Workspace) -> None:
    assert by_name_and_import(ws, ["docs/howto.md", "README.md"]) == []


# --- put together ---------------------------------------------------------


def test_plan_and_diff_are_merged_without_duplicates(ws: Workspace) -> None:
    got = select_tests(ws, plan("pytest tests/test_models.py"), ["src/requests/models.py"])
    assert got == ["tests/test_models.py"]


def test_when_nothing_matches_the_tests_directory_is_the_fallback(ws: Workspace) -> None:
    assert select_tests(ws, plan(""), ["src/requests/nothing_tested.py"]) == ["tests"]


def test_selection_is_capped(ws: Workspace) -> None:
    for i in range(MAX_SELECTED + 10):
        (ws.root / "tests" / f"test_big_{i}.py").write_text("from requests import big\n")
    (ws.root / "src" / "requests" / "big.py").write_text("")
    got = select_tests(ws, plan(""), ["src/requests/big.py"])
    assert len(got) == MAX_SELECTED


# --- the verifier over a fake sandbox --------------------------------------

JUNIT_GREEN = (
    b'<testsuite tests="2" failures="0" errors="0" time="0.2">'
    b'<testcase name="a"/><testcase name="b"/></testsuite>'
)


class FakeImages:
    def __init__(self) -> None:
        self.ensured = 0

    async def ensure(self, ws: Workspace) -> str:
        self.ensured += 1
        return "repoaegis/env:fake"


class FakeSandbox:
    """Records the calls; answers the exec with a canned result."""

    def __init__(
        self,
        *,
        exit_code: int = 0,
        output: str = "ok",
        xml: bytes | None = JUNIT_GREEN,
        timeout: bool = False,
    ) -> None:
        self.exit_code, self.output, self.xml, self.timeout = exit_code, output, xml, timeout
        self.started: list[str] = []
        self.trees: list[bytes] = []
        self.commands: list[str] = []
        self.stopped: list[str] = []

    async def start(self, image: str, *, network: bool = False, writable: bool = False) -> str:
        self.started.append(image)
        return "c0ffee"

    async def put_tree(self, container: str, archive: bytes) -> None:
        self.trees.append(archive)

    async def exec(self, container: str, script: str, *, timeout: float) -> tuple[int, str]:
        self.commands.append(script)
        if self.timeout:
            raise SandboxTimeout("pytest exceeded 600s")
        return self.exit_code, self.output

    async def read_file(self, container: str, path: str) -> bytes | None:
        return self.xml

    async def stop(self, container: str) -> None:
        self.stopped.append(container)


def verifier(sandbox: FakeSandbox) -> DockerVerifier:
    return DockerVerifier(sandbox, FakeImages(), timeout_seconds=600)  # type: ignore[arg-type]


async def test_a_green_run_is_parsed_and_its_artefacts_land_in_the_checkout(ws: Workspace) -> None:
    sandbox = FakeSandbox(output="2 passed")
    report = await verifier(sandbox).run(ws, plan("pytest tests/test_models.py"), [])

    assert report.ok and report.passed == 2
    assert report.log_path == ".repoaegis/runs/1/pytest.log"
    run_dir = ws.root / ".repoaegis" / "runs" / "1"
    assert (run_dir / "pytest.log").read_text(encoding="utf-8") == "2 passed"
    assert (run_dir / "report.xml").read_bytes() == JUNIT_GREEN
    assert "tests/test_models.py" in (run_dir / "command.txt").read_text(encoding="utf-8")
    assert sandbox.started == ["repoaegis/env:fake"] and sandbox.stopped == ["c0ffee"]


async def test_the_tree_sent_in_has_the_edits_but_not_our_run_logs(ws: Workspace) -> None:
    sandbox = FakeSandbox()
    await verifier(sandbox).run(ws, plan(""), [])
    await verifier(sandbox).run(ws, plan(""), [])  # second run: .repoaegis/runs/1 exists now

    with tarfile.open(fileobj=io.BytesIO(sandbox.trees[1])) as tar:
        names = tar.getnames()
    assert "src/requests/models.py" in names
    assert not any(n.startswith(".repoaegis") for n in names)


async def test_runs_are_numbered(ws: Workspace) -> None:
    sandbox = FakeSandbox()
    first = await verifier(sandbox).run(ws, plan(""), [])
    second = await verifier(sandbox).run(ws, plan(""), [])
    assert (first.log_path, second.log_path) == (
        ".repoaegis/runs/1/pytest.log",
        ".repoaegis/runs/2/pytest.log",
    )


async def test_no_report_means_an_error_with_the_log_tail(ws: Workspace) -> None:
    sandbox = FakeSandbox(
        exit_code=2, output="ImportError: no module named foo\ncollected 0 items", xml=None
    )
    report = await verifier(sandbox).run(ws, plan(""), [])
    assert not report.ok and report.errors == 1
    assert report.failures[0].kind == "NoReport"
    assert "ImportError" in report.failures[0].excerpt
    assert sandbox.stopped == ["c0ffee"]  # the container is always cleaned up


async def test_a_timeout_is_a_red_report_not_a_crash(ws: Workspace) -> None:
    sandbox = FakeSandbox(timeout=True)
    report = await verifier(sandbox).run(ws, plan(""), [])
    assert not report.ok and report.failures[0].kind == "Timeout"
    assert sandbox.stopped == ["c0ffee"]


async def test_the_command_written_down_is_the_one_run(ws: Workspace) -> None:
    sandbox = FakeSandbox()
    await verifier(sandbox).run(ws, plan("pytest tests/test_models.py"), [])
    command = (ws.root / ".repoaegis" / "runs" / "1" / "command.txt").read_text(encoding="utf-8")
    assert sandbox.commands == [command.strip()]
    assert "--junitxml=/tmp/repoaegis-report.xml" in command
    assert "-p no:cacheprovider" in command
