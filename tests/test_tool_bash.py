import os
import tempfile
import time
import unittest

from repoaegis.tools.bash import MAX_TIMEOUT, Bash


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # 已退出但还没被回收的僵尸进程也算死了；读的时候刚好被回收，也算死了
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().split(")")[-1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False


def _dies_soon(pid: int, within: float = 2.0) -> bool:
    """信号发出后进程要过一小会儿才真正死掉，立刻检查偶尔还看得到它，所以最多等 within 秒。"""
    deadline = time.monotonic() + within
    while _alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class BashTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workdir = os.path.join(self.tmp.name, "repo")
        self.output_dir = os.path.join(self.tmp.name, "outputs")
        os.makedirs(self.workdir)
        self.bash = Bash(self.workdir, self.output_dir, env={"PATH": os.environ["PATH"], "GREETING": "hi"})

    def tearDown(self):
        self.tmp.cleanup()

    def run_command(self, command, **extra):
        return self.bash.run({"command": command, **extra})

    def test_exit_code_and_output(self):
        result = self.run_command("echo hello")
        self.assertEqual(result.text, "Exit code: 0\nhello\n")
        self.assertFalse(result.error)
        self.assertEqual(result.details, {"exit_code": 0, "timed_out": False, "truncated": False, "output_path": None})

    def test_failing_command_is_not_a_tool_error(self):
        result = self.run_command("echo oops >&2; exit 3")
        self.assertEqual(result.text, "Exit code: 3\noops\n")
        self.assertFalse(result.error)
        self.assertEqual(result.details["exit_code"], 3)

    def test_runs_in_workdir_and_forgets_cd_and_export(self):
        self.run_command("cd /; export LEFTOVER=1")
        self.assertEqual(self.run_command('pwd; echo "[$LEFTOVER]"').text, f"Exit code: 0\n{self.workdir}\n[]\n")

    def test_env_is_what_the_program_gives(self):
        self.assertEqual(self.run_command("echo $GREETING").text, "Exit code: 0\nhi\n")

    def test_default_env_hides_program_variables(self):
        os.environ["REPOAEGIS_TEST_SECRET"] = "should-not-leak"
        self.addCleanup(os.environ.pop, "REPOAEGIS_TEST_SECRET")
        bash = Bash(self.workdir, self.output_dir)
        result = bash.run({"command": "env"})
        self.assertNotIn("should-not-leak", result.text)
        self.assertIn("PAGER=cat", result.text)

    def test_no_stdin(self):
        started = time.monotonic()
        self.assertEqual(self.run_command("cat").text, "Exit code: 0\n")
        self.assertLess(time.monotonic() - started, 5)

    def test_timeout_kills_command_and_children(self):
        started = time.monotonic()
        result = self.run_command("sleep 100 & echo $! > child.pid; echo started; sleep 100", timeout=1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result.text,
                         "Command timed out after 1 seconds and was killed. Output before it was killed:\nstarted\n")
        self.assertTrue(result.error)
        self.assertEqual(result.details["exit_code"], None)
        self.assertTrue(result.details["timed_out"])
        with open(os.path.join(self.workdir, "child.pid")) as pid_file:
            self.assertTrue(_dies_soon(int(pid_file.read())))

    def test_background_process_killed_when_command_ends(self):
        started = time.monotonic()
        result = self.run_command("sleep 100 & echo $!")
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(_dies_soon(int(result.text.splitlines()[1])))

    def test_long_output_keeps_head_and_tail_and_saves_full_text(self):
        result = self.run_command("printf 'A%.0s' $(seq 6000); printf 'B%.0s' $(seq 6000)")
        lines = result.text.splitlines()
        self.assertEqual(lines[0], "Exit code: 0")
        self.assertIn("cut to the first 5000 and last 5000 characters", lines[1])
        path = lines[1].split("Full output: ")[1].rstrip("]")
        self.assertTrue(result.details["truncated"])
        self.assertEqual(result.details["output_path"], path)
        self.assertFalse(result.error)
        with open(path) as full:
            self.assertEqual(full.read(), "A" * 6000 + "B" * 6000)
        self.assertIn("A" * 5000 + "\n\n[... middle of output omitted ...]\n\n" + "B" * 5000, result.text)
        self.assertNotIn("A" * 5001, result.text)

    def test_huge_output_reads_only_both_ends(self):
        result = self.run_command("head -c 3000000 /dev/zero | tr '\\0' x; echo END")
        self.assertIn("Output was 3000004 bytes", result.text)
        self.assertTrue(result.text.endswith("x" * 4996 + "END\n"))

    def test_short_output_leaves_no_file(self):
        self.run_command("echo short")
        self.assertEqual(os.listdir(self.output_dir), [])

    def test_bad_arguments(self):
        cases = [({}, "`command` must be"), ({"command": "   "}, "`command` must be"),
                 ({"command": "echo a\0b"}, "NUL characters")]
        cases += [({"command": "echo hi", "timeout": timeout}, "`timeout` must be")
                  for timeout in (0, MAX_TIMEOUT + 1, "60", True, 1.5)]
        for args, message in cases:
            with self.subTest(args=args):
                result = self.bash.run(args)
                self.assertIn(message, result.text)
                self.assertTrue(result.error)

    def test_spec(self):
        spec = self.bash.tool().spec()
        self.assertEqual(spec["type"], "function")
        self.assertEqual(spec["function"]["name"], "bash")
        self.assertEqual(spec["function"]["parameters"]["required"], ["command"])


if __name__ == "__main__":
    unittest.main()
