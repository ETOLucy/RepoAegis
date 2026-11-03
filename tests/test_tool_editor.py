import importlib.util
import os
import tempfile
import unittest

from repoaegis.tools.editor import MAX_VIEW_CHARS, Editor, LintError, _new_errors


def fake_lint(path):
    """含 BAD 的行算一个语法错误，代替 flake8。"""
    with open(path, encoding="utf-8") as source:
        return [LintError(number, 1, "E999", "bad line") for number, line in enumerate(source, 1) if "BAD" in line]


class EditorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(os.path.join(self.tmp.name, "repo"))
        os.makedirs(self.root)
        self.lint_calls = []

        def lint(path):
            self.lint_calls.append(path)
            return fake_lint(path)

        self.editor = Editor(self.root, lint=lint)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def write(self, rel, content, mode="w"):
        full = self.path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, mode, **({} if "b" in mode else {"encoding": "utf-8", "newline": ""})) as out:
            out.write(content)
        return full

    def read(self, rel):
        with open(self.path(rel), "rb") as source:
            return source.read().decode("utf-8")

    def run_tool(self, **args):
        return self.editor.run(args)

    def view(self, rel, **extra):
        return self.run_tool(command="view", path=self.path(rel), **extra)

    def replace(self, rel, old, new):
        return self.run_tool(command="str_replace", path=self.path(rel), old_str=old, new_str=new)

    # ---- 路径 ----

    def test_relative_path_refused_with_suggestion(self):
        result = self.run_tool(command="view", path="src/a.py")
        self.assertTrue(result.error)
        self.assertIn(f"Did you mean {self.path('src/a.py')}?", result.text)

    def test_paths_outside_repo_refused(self):
        outside = os.path.join(self.tmp.name, "secret.txt")
        with open(outside, "w") as out:
            out.write("x")
        os.symlink(outside, self.path("link.txt"))
        for path in (outside, self.path("..", "secret.txt"), self.path("link.txt"), "/etc/passwd"):
            with self.subTest(path=path):
                result = self.run_tool(command="view", path=path)
                self.assertTrue(result.error)
                self.assertIn("outside the repository", result.text)

    def test_git_and_github_view_only(self):
        workflow = self.write(".github/workflows/ci.yml", "on: push\n")
        self.write(".git/config", "[core]\n")
        os.symlink(self.path(".github"), self.path("gh"))
        self.assertFalse(self.view(".github/workflows/ci.yml").error)
        attempts = [dict(command="str_replace", path=workflow, old_str="push", new_str="pull_request"),
                    dict(command="create", path=self.path(".github", "new.yml"), file_text="x"),
                    dict(command="insert", path=self.path("gh", "workflows", "ci.yml"), insert_line=0, new_str="x"),
                    dict(command="create", path=self.path("vendor", "lib", ".git", "hooks"), file_text="x")]
        for args in attempts:
            with self.subTest(args=args):
                result = self.editor.run(args)
                self.assertTrue(result.error)
                self.assertIn("can be viewed but not changed", result.text)
        self.assertEqual(self.read(".github/workflows/ci.yml"), "on: push\n")

    def test_bad_command_and_path(self):
        self.assertIn("`command` must be one of", self.run_tool(command="undo_edit", path=self.root).text)
        self.assertIn("`path` must be a non-empty string", self.run_tool(command="view").text)
        self.assertIn("does not exist", self.view("missing.py").text)

    # ---- 查看（view）----

    def test_view_file_with_line_numbers(self):
        self.write("a.py", "one\n\ttwo\nthree\n")
        result = self.view("a.py")
        self.assertFalse(result.error)
        self.assertEqual(result.text, f"Here is {self.path('a.py')}, lines 1-3 of 3, with line numbers:\n"
                                      "     1\tone\n     2\t\ttwo\n     3\tthree")

    def test_view_range(self):
        self.write("a.txt", "".join(f"line{n}\n" for n in range(1, 11)))
        self.assertTrue(self.view("a.txt", view_range=[3, 4]).text.endswith("     3\tline3\n     4\tline4"))
        self.assertTrue(self.view("a.txt", view_range=[9, -1]).text.endswith("     9\tline9\n    10\tline10"))
        clamped = self.view("a.txt", view_range=[9, 50])
        self.assertFalse(clamped.error)
        self.assertTrue(clamped.text.startswith("Note: the file has only 10 lines, so showing lines 9-10."))
        for bad in ([0, 3], [11, -1], [5, 4], [1], ["1", "2"], [True, 2], "1-2"):
            with self.subTest(view_range=bad):
                self.assertTrue(self.view("a.txt", view_range=bad).error)

    def test_view_empty_file(self):
        self.write("empty.txt", "")
        self.assertEqual(self.view("empty.txt").text, f"{self.path('empty.txt')} is empty.")
        self.assertTrue(self.view("empty.txt", view_range=[1, 1]).error)

    def test_view_long_file_is_cut(self):
        self.write("big.txt", "x" * 100 + "\n" * 1 + ("y" * 100 + "\n") * 300)
        text = self.view("big.txt").text
        self.assertIn("[Output cut at 16000 characters", text)
        self.assertLess(len(text), MAX_VIEW_CHARS + 500)

    def test_view_directory_two_levels_without_hidden(self):
        self.write("src/pkg/deep/x.py", "")
        self.write("src/main.py", "")
        self.write("README.md", "")
        self.write(".env", "TOKEN=1")
        self.write(".gitignore", "")
        self.write("src/.cache/y", "")
        result = self.view(".")
        self.assertEqual(result.text.splitlines(), [
            f"Files and directories up to 2 levels deep in {self.root}, excluding hidden items:",
            self.path("README.md"), self.path("src") + "/", self.path("src", "main.py"), self.path("src", "pkg") + "/",
            f"2 hidden item(s) directly in {self.root} are not shown; use `ls -a` in bash to see them."])
        self.assertTrue(self.view(".", view_range=[1, 2]).error)

    def test_binary_large_and_non_utf8_files(self):
        self.write("img.png", b"\x89PNG\r\n\x00\x00", mode="wb")
        self.assertIn("binary file", self.view("img.png").text)
        with open(self.path("huge.log"), "wb") as out:
            out.truncate(11 * 1024 * 1024)
        self.assertIn("larger than 10 MB", self.view("huge.log").text)
        self.write("latin.txt", "caf\xe9\n".encode("latin-1"), mode="wb")
        viewed = self.view("latin.txt")
        self.assertFalse(viewed.error)
        self.assertIn("not valid UTF-8", viewed.text)
        self.assertIn("not valid UTF-8 and cannot be changed", self.replace("latin.txt", "caf", "bar").text)

    # ---- 先看过才能改 ----

    def test_must_view_before_edit_and_again_after_outside_change(self):
        self.write("a.txt", "hello\n")
        self.assertIn("before changing it", self.replace("a.txt", "hello", "hi").text)
        self.view("a.txt")
        self.assertFalse(self.replace("a.txt", "hello", "hi").error)
        # 本工具自己改完不用重看
        self.assertFalse(self.replace("a.txt", "hi", "hey").error)
        self.write("a.txt", "changed by bash\n")
        self.assertIn("was changed since you last viewed it", self.replace("a.txt", "bash", "x").text)
        self.view("a.txt")
        self.assertFalse(self.replace("a.txt", "bash", "x").error)

    # ---- 替换（str_replace）----

    def test_str_replace_returns_snippet(self):
        self.write("a.txt", "".join(f"line{n}\n" for n in range(1, 21)))
        self.view("a.txt")
        result = self.replace("a.txt", "line10\n", "ten\nTEN\n")
        self.assertFalse(result.error)
        self.assertEqual(result.text.splitlines()[1:-1], [f"{n:6}\t{text}" for n, text in [
            (6, "line6"), (7, "line7"), (8, "line8"), (9, "line9"), (10, "ten"), (11, "TEN"), (12, "line11"),
            (13, "line12"), (14, "line13"), (15, "line14")]])

    def test_str_replace_not_found_multiple_and_same(self):
        self.write("a.txt", "x = 1\nx = 1\naaa\n")
        self.view("a.txt")
        self.assertIn("does not appear verbatim", self.replace("a.txt", "x  = 1", "y").text)
        self.assertIn("appears 2 times in", self.replace("a.txt", "x = 1", "y").text)
        self.assertIn("(lines 1, 2)", self.replace("a.txt", "x = 1", "y").text)
        # 互相重叠的也算出现多次
        self.assertIn("appears 2 times", self.replace("a.txt", "aa", "b").text)
        self.assertIn("are the same", self.replace("a.txt", "aaa", "aaa").text)
        self.assertEqual(self.read("a.txt"), "x = 1\nx = 1\naaa\n")

    def test_str_replace_delete_and_keep_tabs(self):
        self.write("Makefile", "all:\n\techo hi\n\techo bye\n")
        self.view("Makefile")
        self.assertFalse(self.replace("Makefile", "\techo bye\n", "").error)
        self.assertEqual(self.read("Makefile"), "all:\n\techo hi\n")

    def test_str_replace_crlf_file(self):
        self.write("win.txt", "a\r\nb\r\nc\r\n")
        self.view("win.txt")
        self.assertFalse(self.replace("win.txt", "a\nb\n", "A\nB\n").error)
        self.assertEqual(self.read("win.txt"), "A\r\nB\r\nc\r\n")

    def test_str_replace_bad_arguments(self):
        self.write("a.txt", "x\n")
        self.view("a.txt")
        self.assertIn("`old_str` is required", self.run_tool(command="str_replace", path=self.path("a.txt")).text)
        self.assertIn("`new_str` must be a string", self.replace("a.txt", "x", None).text)
        self.assertIn("is a directory", self.run_tool(command="str_replace", path=self.root, old_str="a").text)

    # ---- 插入（insert）----

    def test_insert(self):
        self.write("a.txt", "one\ntwo\n")
        self.view("a.txt")
        self.assertFalse(self.run_tool(command="insert", path=self.path("a.txt"), insert_line=0, new_str="zero").error)
        self.assertFalse(self.run_tool(command="insert", path=self.path("a.txt"), insert_line=2, new_str="1.5\n").error)
        self.assertFalse(self.run_tool(command="insert", path=self.path("a.txt"), insert_line=4, new_str="end").error)
        self.assertEqual(self.read("a.txt"), "zero\none\n1.5\ntwo\nend\n")
        result = self.run_tool(command="insert", path=self.path("a.txt"), insert_line=6, new_str="x")
        self.assertIn("must be from 0 to 5", result.text)

    def test_insert_after_last_line_without_newline_and_crlf(self):
        self.write("a.txt", "one\ntwo")
        self.view("a.txt")
        self.run_tool(command="insert", path=self.path("a.txt"), insert_line=2, new_str="three")
        self.assertEqual(self.read("a.txt"), "one\ntwo\nthree\n")
        self.write("win.txt", "a\r\nb\r\n")
        self.view("win.txt")
        self.run_tool(command="insert", path=self.path("win.txt"), insert_line=1, new_str="x\ny")
        self.assertEqual(self.read("win.txt"), "a\r\nx\r\ny\r\nb\r\n")

    # ---- 新建（create）----

    def test_create(self):
        result = self.run_tool(command="create", path=self.path("new.txt"), file_text="a\nb\n")
        self.assertFalse(result.error)
        self.assertEqual(result.text, f"Created {self.path('new.txt')} (2 lines).")
        # 自己新建的不用再看就能改
        self.assertFalse(self.replace("new.txt", "a", "A").error)
        self.assertIn("already exists", self.run_tool(command="create", path=self.path("new.txt"), file_text="").text)
        missing = self.run_tool(command="create", path=self.path("no", "such", "dir.txt"), file_text="")
        self.assertIn("Create it first with `mkdir -p`", missing.text)
        self.assertFalse(os.path.exists(self.path("no")))

    # ---- 改完检查 ----

    def test_change_with_new_lint_error_is_reverted(self):
        self.write("a.py", "x = 1\ny = 2\n")
        self.view("a.py")
        result = self.replace("a.py", "y = 2", "y = BAD")
        self.assertTrue(result.error)
        self.assertIn("not applied because it introduced new lint errors:\n- line 2 col 1: E999 bad line", result.text)
        self.assertIn("     2\ty = BAD", result.text)
        self.assertIn("     2\ty = 2", result.text)
        self.assertEqual(result.details["reverted"], True)
        self.assertEqual(self.read("a.py"), "x = 1\ny = 2\n")
        # 撤回后文件还是看过时的样子，可以直接再改
        self.assertFalse(self.replace("a.py", "y = 2", "y = 3").error)

    def test_existing_lint_errors_are_not_blamed_on_the_change(self):
        self.write("a.py", "a = 1\nb = BAD\nc = 3\nd = BAD\n")
        self.view("a.py")
        # 在两个旧错误之间加两行：第 4 行的旧错误挪到第 6 行，不算新增
        result = self.replace("a.py", "c = 3\n", "c = 3\nc2 = 4\nc3 = 5\n")
        self.assertFalse(result.error, result.text)
        self.assertEqual(self.read("a.py"), "a = 1\nb = BAD\nc = 3\nc2 = 4\nc3 = 5\nd = BAD\n")

    def test_create_python_with_lint_error_is_not_created(self):
        result = self.run_tool(command="create", path=self.path("b.py"), file_text="x = BAD\n")
        self.assertTrue(result.error)
        self.assertIn("was not created", result.text)
        self.assertFalse(os.path.exists(self.path("b.py")))

    def test_only_python_files_are_linted(self):
        self.write("notes.md", "BAD\n")
        self.view("notes.md")
        self.assertFalse(self.replace("notes.md", "BAD", "BAD BAD").error)
        self.assertEqual(self.lint_calls, [])

    def test_spec(self):
        spec = self.editor.tool().spec()
        self.assertEqual(spec["function"]["name"], "str_replace_editor")
        self.assertEqual(spec["function"]["parameters"]["properties"]["command"]["enum"],
                         ["view", "create", "str_replace", "insert"])


class NewErrorsTest(unittest.TestCase):
    def test_shifts_and_window(self):
        before = [LintError(1, 1, "F821", "a"), LintError(5, 1, "F821", "b"), LintError(9, 1, "F821", "c")]
        # 第 5 行起原来 1 行，改成 3 行（后面的行号挪 2）：第 9 行的挪到第 11 行；第 5 行的旧错误不作数
        after = [LintError(1, 1, "F821", "a"), LintError(5, 1, "F821", "b"), LintError(11, 1, "F821", "c"),
                 LintError(12, 1, "E999", "d")]
        self.assertEqual(_new_errors(before, after, 5, 1, 2), [LintError(5, 1, "F821", "b"), LintError(12, 1, "E999", "d")])

    def test_errors_above_the_change_are_ignored(self):
        self.assertEqual(_new_errors([], [LintError(2, 1, "F821", "x")], 5, 1, 1), [])


@unittest.skipUnless(importlib.util.find_spec("flake8"), "没装 flake8")
class RealFlake8Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.editor = Editor(self.tmp.name)
        self.file = os.path.join(os.path.realpath(self.tmp.name), "m.py")
        with open(self.file, "w") as out:
            out.write("def f(a):\n    return a\n")
        self.editor.run({"command": "view", "path": self.file})

    def tearDown(self):
        self.tmp.cleanup()

    def test_syntax_error_reverted(self):
        result = self.editor.run({"command": "str_replace", "path": self.file, "old_str": "return a", "new_str": "return (a"})
        self.assertTrue(result.error)
        self.assertIn("E999", result.text)

    def test_undefined_name_reverted(self):
        result = self.editor.run({"command": "str_replace", "path": self.file, "old_str": "return a", "new_str": "return b"})
        self.assertTrue(result.error)
        self.assertIn("F821 undefined name 'b'", result.text)

    def test_clean_change_applied(self):
        result = self.editor.run({"command": "str_replace", "path": self.file, "old_str": "return a", "new_str": "return a + 1"})
        self.assertFalse(result.error, result.text)


if __name__ == "__main__":
    unittest.main()
