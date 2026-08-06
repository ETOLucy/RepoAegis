import pytest
from pydantic import ValidationError

from repo_maintenance_agent.agents.patches import render_patch
from repo_maintenance_agent.agents.schemas import PatchEdit, PatchProposal


def proposal(*edits: PatchEdit) -> PatchProposal:
    return PatchProposal(summary="Apply exact edits.", edits=list(edits))


def test_render_patch_replaces_unique_exact_text() -> None:
    rendered = render_patch(
        proposal(PatchEdit(path="app.py", old_text="value = 1", new_text="value = 2")),
        current_files={"app.py": "value = 1\n"},
        declared_files=("app.py",),
    )

    assert rendered.changed_files == ("app.py",)
    assert rendered.data == (
        b"diff --git a/app.py b/app.py\n"
        b"--- a/app.py\n"
        b"+++ b/app.py\n"
        b"@@ -1 +1 @@\n"
        b"-value = 1\n"
        b"+value = 2\n"
    )


def test_render_patch_adapts_lf_model_edit_to_crlf_source() -> None:
    rendered = render_patch(
        proposal(
            PatchEdit(
                path="app.py",
                old_text="first = 1\nsecond = 2",
                new_text="first = 1\nsecond = 3",
            )
        ),
        current_files={"app.py": "first = 1\r\nsecond = 2\r\n"},
        declared_files=("app.py",),
    )

    assert b"-second = 2\r\n" in rendered.data
    assert b"+second = 3\r\n" in rendered.data


def test_render_patch_creates_a_missing_text_file() -> None:
    rendered = render_patch(
        proposal(PatchEdit(path="new.py", old_text=None, new_text="VALUE = 1\n")),
        current_files={"new.py": {"error": "not_found"}},
        declared_files=("new.py",),
    )

    assert rendered.changed_files == ("new.py",)
    assert rendered.data == (
        b"diff --git a/new.py b/new.py\n"
        b"new file mode 100644\n"
        b"--- /dev/null\n"
        b"+++ b/new.py\n"
        b"@@ -0,0 +1 @@\n"
        b"+VALUE = 1\n"
    )


def test_render_patch_marks_missing_trailing_newline() -> None:
    rendered = render_patch(
        proposal(PatchEdit(path="app.py", old_text="one", new_text="two")),
        current_files={"app.py": "one"},
        declared_files=("app.py",),
    )

    assert rendered.data.endswith(
        b"-one\n\\ No newline at end of file\n+two\n\\ No newline at end of file\n"
    )


@pytest.mark.parametrize(
    "path",
    ["../secret.py", "/absolute.py", "-option.py", "bad\tname.py", "bad\nname.py"],
)
def test_patch_edit_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="safe repository-relative path"):
        PatchEdit(path=path, old_text="old", new_text="new")


def test_patch_edit_normalizes_windows_separators() -> None:
    edit = PatchEdit(path="src\\app.py", old_text="old", new_text="new")

    assert edit.path == "src/app.py"


@pytest.mark.parametrize(
    ("old_text", "new_text", "message"),
    [("", "new", "old_text must be non-empty"), ("same", "same", "no-op")],
)
def test_patch_edit_rejects_invalid_replacements(
    old_text: str, new_text: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        PatchEdit(path="app.py", old_text=old_text, new_text=new_text)


@pytest.mark.parametrize(
    ("current", "message"),
    [("before\nafter\n", "not found"), ("old\nold\n", "more than once")],
)
def test_render_patch_requires_one_exact_match(current: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        render_patch(
            proposal(PatchEdit(path="app.py", old_text="old", new_text="new")),
            current_files={"app.py": current},
            declared_files=("app.py",),
        )


def test_render_patch_rejects_overlapping_edits() -> None:
    with pytest.raises(ValueError, match="overlap"):
        render_patch(
            proposal(
                PatchEdit(path="app.py", old_text="abc", new_text="one"),
                PatchEdit(path="app.py", old_text="bcd", new_text="two"),
            ),
            current_files={"app.py": "abcde\n"},
            declared_files=("app.py",),
        )


def test_render_patch_rejects_undeclared_paths() -> None:
    with pytest.raises(ValueError, match="not declared"):
        render_patch(
            proposal(PatchEdit(path="secret.py", old_text="old", new_text="new")),
            current_files={"secret.py": "old\n"},
            declared_files=("app.py",),
        )


def test_render_patch_rejects_creation_when_target_exists() -> None:
    with pytest.raises(ValueError, match="already exists"):
        render_patch(
            proposal(PatchEdit(path="app.py", old_text=None, new_text="new\n")),
            current_files={"app.py": "old\n"},
            declared_files=("app.py",),
        )


def test_render_patch_rejects_duplicate_file_creation() -> None:
    with pytest.raises(ValueError, match="created more than once"):
        render_patch(
            proposal(
                PatchEdit(path="new.py", old_text=None, new_text="one\n"),
                PatchEdit(path="new.py", old_text=None, new_text="two\n"),
            ),
            current_files={"new.py": {"error": "not_found"}},
            declared_files=("new.py",),
        )
