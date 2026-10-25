import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from repoaegis.precheck import Decision, GitHub, Response, check, last_labeler, write_outputs

REPO = "octo/demo"
LABEL = "agent-fix"
ISSUE = f"/repos/{REPO}/issues/8"
EVENTS = f"/repos/{REPO}/issues/8/events?per_page=100"


class FakeGitHub:
    """按路径返回事先准备好的 Response，代替真的 GitHub 接口。"""

    def __init__(self, routes):
        self.routes = routes

    def get(self, path_or_url):
        return self.routes[path_or_url]


def issue(state="open", labels=(LABEL,), **extra):
    return Response(200, {"number": 8, "state": state,
                          "labels": [{"name": name} for name in labels], **extra})


def labeled(login, label=LABEL, created_at="2026-10-25T10:00:00Z", event_id=1, event="labeled"):
    return {"id": event_id, "event": event, "created_at": created_at,
            "actor": {"login": login}, "label": {"name": label}}


def permission(login, base, role):
    return (f"/repos/{REPO}/collaborators/{login}/permission",
            Response(200, {"permission": base, "role_name": role, "user": {"login": login}}))


def routes(issue_response, events=(), *permissions):
    table = {ISSUE: issue_response, EVENTS: Response(200, list(events))}
    table.update(dict(permissions))
    return FakeGitHub(table)


class CheckTest(unittest.TestCase):
    def test_start_when_open_and_labeled_by_writer(self):
        github = routes(issue(), [labeled("alice")], permission("alice", "write", "write"))
        self.assertEqual(check(github, REPO, 8, LABEL),
                         Decision("start", "alice（write）打上了 agent-fix", "alice"))

    def test_maintain_and_admin_can_start(self):
        for base, role in (("write", "maintain"), ("admin", "admin")):
            with self.subTest(role=role):
                github = routes(issue(), [labeled("alice")], permission("alice", base, role))
                self.assertEqual(check(github, REPO, 8, LABEL).action, "start")

    def test_skip_when_issue_moved_or_deleted(self):
        for status in (301, 404, 410):
            with self.subTest(status=status):
                github = routes(Response(status))
                self.assertEqual(check(github, REPO, 8, LABEL).action, "skip")

    def test_skip_when_pull_request(self):
        github = routes(issue(pull_request={"url": "..."}))
        self.assertEqual(check(github, REPO, 8, LABEL).reason, "#8 是 PR，不是 issue")

    def test_skip_when_closed(self):
        github = routes(issue(state="closed"))
        self.assertEqual(check(github, REPO, 8, LABEL).reason, "issue #8 已关闭")

    def test_skip_when_label_removed(self):
        github = routes(issue(labels=("bug",)))
        self.assertEqual(check(github, REPO, 8, LABEL).reason, "issue #8 上已没有 agent-fix 标签")

    def test_deny_when_triage(self):
        github = routes(issue(), [labeled("tom")], permission("tom", "read", "triage"))
        self.assertEqual(check(github, REPO, 8, LABEL),
                         Decision("deny", "tom 在仓库里的角色是 triage，需要 Write、Maintain 或 Admin", "tom"))

    def test_deny_when_not_collaborator(self):
        github = routes(issue(), [labeled("someone[bot]")],
                        (f"/repos/{REPO}/collaborators/someone%5Bbot%5D/permission", Response(404)))
        self.assertEqual(check(github, REPO, 8, LABEL),
                         Decision("deny", "someone[bot] 不是仓库协作者", "someone[bot]"))

    def test_deny_when_no_label_record(self):
        github = routes(issue(), [])
        self.assertEqual(check(github, REPO, 8, LABEL).action, "deny")

    def test_unexpected_status_raises(self):
        github = routes(Response(403))
        with self.assertRaises(RuntimeError):
            check(github, REPO, 8, LABEL)


class LastLabelerTest(unittest.TestCase):
    def test_latest_labeled_event_across_pages(self):
        page2 = "https://api.github.com/repositories/1/issues/8/events?per_page=100&page=2"
        github = FakeGitHub({
            EVENTS: Response(200, [
                labeled("alice", event_id=1, created_at="2026-10-25T10:00:00Z"),
                labeled("alice", event_id=2, created_at="2026-10-25T10:05:00Z", event="unlabeled"),
            ], next_url=page2),
            page2: Response(200, [
                labeled("bob", event_id=3, created_at="2026-10-25T10:10:00Z"),
                labeled("carol", label="bug", event_id=4, created_at="2026-10-25T10:20:00Z"),
            ]),
        })
        self.assertEqual(last_labeler(github, REPO, 8, LABEL), "bob")


class WriteOutputsTest(unittest.TestCase):
    def test_one_line_per_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "output")
            write_outputs(path, Decision("deny", "第一行\n第二行", "tom"))
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "action=deny\nreason=第一行 第二行\nlabeler=tom\n")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
        elif self.path == "/gone":
            self.send_response(410)
            self.end_headers()
        else:
            body = json.dumps([{"auth": self.headers["Authorization"]}]).encode()
            self.send_response(200)
            self.send_header("Link", '<http://x/page2>; rel="next", <http://x/page9>; rel="last"')
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


class GitHubClientTest(unittest.TestCase):
    """用本机上的一个小 HTTP 服务代替 GitHub，检查客户端怎么处理状态码和分页。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.github = GitHub("t0ken", f"http://127.0.0.1:{cls.server.server_port}", retries=1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_redirect_is_not_followed(self):
        self.assertEqual(self.github.get("/moved").status, 301)

    def test_client_error_is_returned(self):
        self.assertEqual(self.github.get("/gone"), Response(410))

    def test_ok_with_token_and_next_link(self):
        response = self.github.get("/list")
        self.assertEqual(response, Response(200, [{"auth": "Bearer t0ken"}], "http://x/page2"))


if __name__ == "__main__":
    unittest.main()
