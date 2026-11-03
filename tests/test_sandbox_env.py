import unittest

from repoaegis.sandbox.env import FIXED, SuspiciousEnvError, command_env

# 模拟 Actions 里主程序的环境：有该留的，也有不该给 agent 的
SOURCE = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/home/runner",
    "USER": "runner",
    "https_proxy": "http://172.30.0.10:3128",
    "SSL_CERT_FILE": "/run/awf-lib/trust/ca-certificates.crt",
    "LD_PRELOAD": "/run/awf-lib/one-shot-token.so",
    "GITHUB_REPOSITORY": "ETOLucy/repoaegis-sandbox",
    "GITHUB_TOKEN": "ghs_fake",
    "LANGFUSE_SECRET_KEY": "sk-lf-fake",
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "fake",
    "PAGER": "less",
}


class CommandEnvTest(unittest.TestCase):
    def test_keeps_only_listed_variables_plus_fixed(self):
        env = command_env(SOURCE)
        self.assertEqual(env, {"PATH": "/usr/bin:/bin", "HOME": "/home/runner", "USER": "runner",
                               "https_proxy": "http://172.30.0.10:3128",
                               "SSL_CERT_FILE": "/run/awf-lib/trust/ca-certificates.crt", **FIXED})

    def test_unlisted_variables_never_reach_the_agent(self):
        env = command_env(SOURCE)
        for name in ("LD_PRELOAD", "GITHUB_REPOSITORY", "GITHUB_TOKEN", "LANGFUSE_SECRET_KEY", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
            with self.subTest(name=name):
                self.assertNotIn(name, env)

    def test_fixed_values_win_over_source(self):
        self.assertEqual(command_env(SOURCE)["PAGER"], "cat")

    def test_missing_variables_are_skipped(self):
        self.assertEqual(command_env({}), FIXED)

    def test_extra_is_added_and_wins(self):
        env = command_env(SOURCE, extra={"PATH": "/venv/bin:/usr/bin:/bin", "VIRTUAL_ENV": "/venv"})
        self.assertEqual(env["PATH"], "/venv/bin:/usr/bin:/bin")
        self.assertEqual(env["VIRTUAL_ENV"], "/venv")

    def test_suspicious_names_stop_the_run_without_showing_values(self):
        for name in ("NPM_TOKEN", "openai_api_key", "DB_PASSWORD", "MY_SECRET", "GOOGLE_CREDENTIALS"):
            with self.subTest(name=name):
                with self.assertRaises(SuspiciousEnvError) as caught:
                    command_env(SOURCE, extra={name: "value-should-not-appear"})
                self.assertIn(name, str(caught.exception))
                self.assertNotIn("value-should-not-appear", str(caught.exception))

    def test_all_suspicious_names_are_listed(self):
        with self.assertRaises(SuspiciousEnvError) as caught:
            command_env(SOURCE, extra={"B_TOKEN": "x", "A_KEY": "y"})
        self.assertIn("A_KEY, B_TOKEN", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
