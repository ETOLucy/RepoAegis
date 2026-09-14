"""The tools are the model's only senses, so their edges are what matter:
what they refuse, what they hide, and what they say when they clamp."""

from pathlib import Path

import pytest

from repoaegis.agent.tools import (
    MAX_MATCHES,
    ToolError,
    Workspace,
    grep,
    list_files,
    read_file,
    run_tool,
)


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "adapters.py").write_text(
        "class HTTPAdapter:\n"
        "    def send(self, request):\n"
        "        return self.build_response(request)\n"
        "\n"
        "    def resolve_redirects(self, resp):\n"
        "        return resp\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "sessions.py").write_text(
        "def resolve_redirects(resp):\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# demo\nresolve_redirects is documented here\n", "utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("resolve_redirects", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "secret.txt").write_text("not mine", encoding="utf-8")
    return Workspace(root=tmp_path)


def test_list_files_hides_git_and_binaries(ws: Workspace) -> None:
    out = list_files(ws)
    assert "src/adapters.py" in out
    assert "README.md" in out
    assert ".git" not in out
    assert "logo.png" not in out


def test_list_files_matches_on_full_path_or_basename(ws: Workspace) -> None:
    assert list_files(ws, glob="*.py").splitlines() == ["src/adapters.py", "src/sessions.py"]
    assert list_files(ws, glob="adapters.py") == "src/adapters.py"
    assert "no files match" in list_files(ws, glob="*.rs")


def test_list_files_says_when_it_truncates(ws: Workspace) -> None:
    out = list_files(ws, glob="*", limit=1)
    assert out.count("\n") == 1
    assert "more; narrow the glob" in out


def test_grep_returns_path_line_text(ws: Workspace) -> None:
    lines = grep(ws, pattern=r"def resolve_redirects").splitlines()
    assert lines == [
        "src/adapters.py:5:    def resolve_redirects(self, resp):",
        "src/sessions.py:1:def resolve_redirects(resp):",
    ]


def test_grep_defaults_to_python_but_takes_a_glob(ws: Workspace) -> None:
    assert "README.md" not in grep(ws, pattern="resolve_redirects")
    assert "README.md:2" in grep(ws, pattern="resolve_redirects", glob="*.md")


def test_grep_reports_an_empty_result_usefully(ws: Workspace) -> None:
    out = grep(ws, pattern="nonexistent_symbol")
    assert "no match" in out and "2 files" in out


def test_grep_clamps_and_says_so(ws: Workspace) -> None:
    (ws.root / "many.py").write_text("hit\n" * 200, encoding="utf-8")
    out = grep(ws, pattern="hit", limit=5)
    assert len([ln for ln in out.splitlines() if ln.startswith("many.py")]) == 5
    assert "stopped at 5 matches" in out


def test_grep_limit_cannot_be_raised_past_the_cap(ws: Workspace) -> None:
    (ws.root / "many.py").write_text("hit\n" * 500, encoding="utf-8")
    out = grep(ws, pattern="hit", limit=10_000)
    assert f"stopped at {MAX_MATCHES} matches" in out


def test_bad_regex_is_a_message_not_a_crash(ws: Workspace) -> None:
    assert "bad regular expression" in run_tool(ws, "grep", {"pattern": "("})


def test_read_file_numbers_the_lines(ws: Workspace) -> None:
    out = read_file(ws, path="src/adapters.py", start=2, lines=2)
    assert out.splitlines()[0] == "src/adapters.py lines 2-3 of 6"
    assert out.splitlines()[1].startswith("     2  ")
    assert "def send(self, request):" in out.splitlines()[1]


def test_read_file_past_the_end_explains_itself(ws: Workspace) -> None:
    assert "past the end" in read_file(ws, path="src/adapters.py", start=99)


def test_read_file_rejects_a_directory(ws: Workspace) -> None:
    with pytest.raises(ToolError):
        read_file(ws, path="src")


@pytest.mark.parametrize("escape", ["../secret", "src/../../outside.txt", "../../../../etc/passwd"])
def test_paths_cannot_escape_the_workspace(ws: Workspace, escape: str) -> None:
    with pytest.raises(ToolError) as caught:
        ws.resolve(escape)
    assert "escapes the workspace" in str(caught.value)


def test_escape_through_run_tool_is_refused_in_text(ws: Workspace) -> None:
    out = run_tool(ws, "read_file", {"path": "../../../secrets.env"})
    assert out.startswith("error: path escapes the workspace")


def test_unknown_tool_lists_the_real_ones(ws: Workspace) -> None:
    out = run_tool(ws, "delete_everything", {})
    assert "no such tool" in out and "grep" in out


def test_bad_arguments_come_back_as_text(ws: Workspace) -> None:
    assert "bad arguments" in run_tool(ws, "grep", {"needle": "x"})
    assert "bad arguments" in run_tool(ws, "read_file", {})
