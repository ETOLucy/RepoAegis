"""Write tools. The interesting cases are the refusals: a span that does not
match, a span that matches twice, and a rewrite asked for too early."""

from pathlib import Path

import pytest

from repoaegis.agent.edits import EditLog, run_edit
from repoaegis.agent.tools import Workspace

SOURCE = "def send(self, request):\n    return self.build_response(request)\n"


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "adapters.py").write_text(SOURCE, encoding="utf-8")
    return Workspace(root=tmp_path)


@pytest.fixture
def edits() -> EditLog:
    return EditLog()


def test_replace_swaps_one_span(ws: Workspace, edits: EditLog) -> None:
    out = run_edit(
        ws,
        edits,
        "replace",
        {
            "path": "src/adapters.py",
            "old": "return self.build_response(request)",
            "new": "return self.build_response(request, verify=True)",
        },
    )
    assert out.startswith("replaced 1 span")
    assert "verify=True" in (ws.root / "src" / "adapters.py").read_text(encoding="utf-8")
    assert edits.changed == {"src/adapters.py"}


def test_a_span_that_does_not_match_is_counted_not_raised(ws: Workspace, edits: EditLog) -> None:
    out = run_edit(
        ws, edits, "replace", {"path": "src/adapters.py", "old": "no such line", "new": "x"}
    )
    assert "no match" in out and "attempt 1" in out
    assert "quote it exactly" in out
    assert edits.failures["src/adapters.py"] == 1


def test_an_ambiguous_span_asks_for_more_context(ws: Workspace, edits: EditLog) -> None:
    (ws.root / "twice.py").write_text("x = 1\ny = 2\nx = 1\n", encoding="utf-8")
    out = run_edit(ws, edits, "replace", {"path": "twice.py", "old": "x = 1", "new": "x = 9"})
    assert "2 matches" in out and "unique" in out
    assert (ws.root / "twice.py").read_text(encoding="utf-8").count("x = 1") == 2  # untouched


def test_rewrite_is_refused_until_replace_has_failed_twice(ws: Workspace, edits: EditLog) -> None:
    rewrite = {"path": "src/adapters.py", "content": "# replaced wholesale\n"}

    assert "fallback, not a first choice" in run_edit(ws, edits, "rewrite_file", rewrite)

    run_edit(ws, edits, "replace", {"path": "src/adapters.py", "old": "nope", "new": "x"})
    assert "fallback, not a first choice" in run_edit(ws, edits, "rewrite_file", rewrite)

    second = run_edit(ws, edits, "replace", {"path": "src/adapters.py", "old": "nope", "new": "x"})
    assert "attempt 2" in second and "rewrite_file" in second  # the hint changes

    assert run_edit(ws, edits, "rewrite_file", rewrite).startswith("rewrote")
    assert (ws.root / "src" / "adapters.py").read_text(encoding="utf-8") == "# replaced wholesale\n"


def test_a_success_clears_the_failure_count(ws: Workspace, edits: EditLog) -> None:
    run_edit(ws, edits, "replace", {"path": "src/adapters.py", "old": "nope", "new": "x"})
    run_edit(
        ws, edits, "replace", {"path": "src/adapters.py", "old": "def send", "new": "def post"}
    )
    assert "src/adapters.py" not in edits.failures
    assert not edits.may_rewrite("src/adapters.py")


def test_crlf_files_match_lf_quotes(tmp_path: Path, edits: EditLog) -> None:
    """A Windows checkout must not make every edit look like a bad quote."""
    (tmp_path / "crlf.py").write_bytes(b"def a():\r\n    return 1\r\n")
    ws = Workspace(root=tmp_path)

    out = run_edit(
        ws, edits, "replace", {"path": "crlf.py", "old": "    return 1", "new": "    return 2"}
    )

    assert out.startswith("replaced 1 span")
    raw = (tmp_path / "crlf.py").read_bytes()
    assert raw == b"def a():\r\n    return 2\r\n"  # the file keeps its own line endings


def test_create_file_makes_parents_and_refuses_to_clobber(ws: Workspace, edits: EditLog) -> None:
    out = run_edit(ws, edits, "create_file", {"path": "tests/new/test_x.py", "content": "pass\n"})
    assert out.startswith("created")
    assert (ws.root / "tests" / "new" / "test_x.py").read_text(encoding="utf-8") == "pass\n"

    again = run_edit(ws, edits, "create_file", {"path": "tests/new/test_x.py", "content": "x"})
    assert "already exists" in again


def test_delete_file(ws: Workspace, edits: EditLog) -> None:
    assert (
        run_edit(ws, edits, "delete_file", {"path": "src/adapters.py"}) == "deleted src/adapters.py"
    )
    assert not (ws.root / "src" / "adapters.py").exists()
    assert "is not a file" in run_edit(ws, edits, "delete_file", {"path": "src/adapters.py"})


@pytest.mark.parametrize("tool", ["replace", "create_file", "delete_file", "rewrite_file"])
def test_no_edit_escapes_the_workspace(ws: Workspace, edits: EditLog, tool: str) -> None:
    arguments = {"path": "../../../escaped.py", "old": "a", "new": "b", "content": "x"}
    allowed = {"replace": ("path", "old", "new"), "delete_file": ("path",)}.get(
        tool, ("path", "content")
    )
    out = run_edit(ws, edits, tool, {k: arguments[k] for k in allowed})
    assert "escapes the workspace" in out or "fallback" in out
    assert not (ws.root.parent.parent.parent / "escaped.py").exists()


def test_an_unknown_edit_tool_lists_the_real_ones(ws: Workspace, edits: EditLog) -> None:
    out = run_edit(ws, edits, "sudo_rm", {})
    assert "no such tool" in out and "replace" in out


def test_bad_arguments_come_back_as_text(ws: Workspace, edits: EditLog) -> None:
    assert "bad arguments" in run_edit(ws, edits, "replace", {"path": "src/adapters.py"})


def test_an_empty_old_is_refused(ws: Workspace, edits: EditLog) -> None:
    out = run_edit(ws, edits, "replace", {"path": "src/adapters.py", "old": "", "new": "x"})
    assert "must not be empty" in out and "create_file" in out
