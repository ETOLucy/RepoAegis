import json
import os
import subprocess
import tempfile
import unittest

from repoaegis.tools.submit import MAX_SHOWN_PATCH_CHARS, Submit

REPORT = {"title": "Fix date format", "summary": "Use ISO 8601.", "testing": "pytest passed.",
          "assumptions": "", "skipped_requests": ""}


class SubmitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "repo")
        self.output_dir = os.path.join(self.tmp.name, "outputs")
        os.makedirs(self.root)
        self.env = {"PATH": os.environ["PATH"], "HOME": self.tmp.name}
        self.git("init", "-q")
        self.write("a.py", "x = 1\n")
        self.write(".gitignore", "*.log\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.checked = []

        def check(patch, report):
            self.checked.append((patch, report))
            return ["touches a protected file"] if b"protected" in patch else []

        self.submit = Submit(self.root, self.base, self.output_dir, check, env=self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                              cwd=self.root, env=self.env, check=True, capture_output=True, text=True).stdout

    def write(self, rel, content):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as out:
            out.write(content)

    def call(self, **changes):
        return self.submit.run({**REPORT, **changes})

    def submitted_patch(self):
        with open(os.path.join(self.output_dir, "patch.diff"), encoding="utf-8") as source:
            return source.read()

    # ---- 两次调用 ----

    def test_first_call_reviews_second_call_submits(self):
        self.write("a.py", "x = 2\n")
        first = self.call()
        self.assertFalse(first.error)
        self.assertFalse(first.details["submitted"])
        self.assertIn("This is the patch that will be submitted", first.text)
        self.assertIn("a.py | 2 +-", first.text)
        self.assertIn("+x = 2", first.text)
        self.assertIn("Before confirming, check:", first.text)
        self.assertFalse(self.submit.submitted)
        self.assertFalse(os.path.exists(self.output_dir))

        second = self.call(summary="Final summary.")
        self.assertFalse(second.error)
        self.assertTrue(second.details["submitted"])
        self.assertTrue(self.submit.submitted)
        self.assertIn("+x = 2", self.submitted_patch())
        with open(second.details["report_path"], encoding="utf-8") as source:
            self.assertEqual(json.load(source), {**REPORT, "summary": "Final summary."})

    def test_change_between_calls_starts_review_over(self):
        self.write("a.py", "x = 2\n")
        self.call()
        self.write("a.py", "x = 3\n")
        second = self.call()
        self.assertFalse(second.details["submitted"])
        self.assertTrue(second.details["restarted"])
        self.assertIn("this is a new review", second.text)
        self.assertIn("+x = 3", second.text)
        self.assertTrue(self.call().details["submitted"])
        self.assertIn("+x = 3", self.submitted_patch())

    def test_no_changes_can_be_submitted(self):
        first = self.call(summary="Already fixed on main.")
        self.assertIn("You have made no changes", first.text)
        self.assertNotIn("Before confirming", first.text)
        second = self.call(summary="Already fixed on main.")
        self.assertTrue(second.details["submitted"])
        self.assertFalse(second.details["has_changes"])
        self.assertEqual(self.submitted_patch(), "")

    def test_calls_after_submission_are_refused(self):
        self.call()
        self.call()
        result = self.call()
        self.assertTrue(result.error)
        self.assertIn("already been submitted", result.text)

    # ---- 补丁的内容 ----

    def test_patch_has_new_and_deleted_files_but_not_ignored_ones(self):
        self.write("new.py", "y = 1\n")
        self.write("debug.log", "noise\n")
        os.remove(os.path.join(self.root, "a.py"))
        self.call()
        self.call()
        patch = self.submitted_patch()
        self.assertIn("new file mode", patch)
        self.assertIn("+y = 1", patch)
        self.assertIn("deleted file mode", patch)
        self.assertNotIn("debug.log", patch)

    def test_commits_made_by_the_agent_are_included(self):
        self.write("a.py", "x = 2\n")
        self.git("commit", "-qam", "agent commit")
        self.call()
        self.call()
        self.assertIn("+x = 2", self.submitted_patch())

    def test_agent_index_is_left_alone(self):
        self.write("a.py", "x = 2\n")
        self.write("new.py", "y = 1\n")
        self.call()
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertIn("?? new.py", self.git("status", "--porcelain"))

    def test_binary_files_are_in_the_patch(self):
        with open(os.path.join(self.root, "data.bin"), "wb") as out:
            out.write(b"\0\1\2")
        self.call()
        self.call()
        self.assertIn("GIT binary patch", self.submitted_patch())

    def test_long_patch_is_cut_in_review_but_submitted_whole(self):
        self.write("big.txt", "z" * (MAX_SHOWN_PATCH_CHARS + 1000) + "\n")
        first = self.call()
        self.assertIn("more characters not shown", first.text)
        self.call()
        self.assertIn("z" * (MAX_SHOWN_PATCH_CHARS + 1000), self.submitted_patch())

    # ---- 产出检查的提醒 ----

    def test_check_warnings_are_shown(self):
        self.write("a.py", "protected = 1\n")
        first = self.call()
        self.assertIn("Problems found", first.text)
        self.assertIn("- touches a protected file", first.text)
        self.assertEqual(first.details["warnings"], ["touches a protected file"])
        patch, report = self.checked[0]
        self.assertIn(b"+protected = 1", patch)
        self.assertEqual(report, REPORT)

    # ---- 汇报字段 ----

    def test_invalid_report_is_refused_without_resetting_review(self):
        self.write("a.py", "x = 2\n")
        self.call()
        cases = [({"title": ""}, "`title` must not be empty"),
                 ({"title": "a\nb"}, "`title` must be one line"),
                 ({"title": "t" * 101}, "the limit is 100"),
                 ({"testing": "t" * 3001}, "the limit is 3000"),
                 ({"summary": 5}, "`summary` must be a string"),
                 ({"extra": "x"}, "unknown field `extra`")]
        for changes, message in cases:
            with self.subTest(changes=changes):
                result = self.call(**changes)
                self.assertTrue(result.error)
                self.assertIn(message, result.text)
        missing = self.submit.run({"title": "t"})
        self.assertIn("`summary` is missing", missing.text)
        self.assertTrue(self.call().details["submitted"])

    def test_spec(self):
        spec = self.submit.tool().spec()
        self.assertEqual(spec["function"]["name"], "submit")
        self.assertEqual(spec["function"]["parameters"]["required"],
                         ["title", "summary", "testing", "assumptions", "skipped_requests"])

    def test_broken_repository_raises(self):
        submit = Submit(self.root, "0" * 40, self.output_dir, lambda patch, report: [], env=self.env)
        with self.assertRaises(RuntimeError):
            submit.run(REPORT)


if __name__ == "__main__":
    unittest.main()
