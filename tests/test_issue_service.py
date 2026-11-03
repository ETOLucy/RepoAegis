"""读 issue 服务的测试，按流程图的第 3 到 7 步分组。"""

import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer

from repoaegis.content_check import Issue, version_at
from repoaegis.issue_service import IssueService, ask_github, check_version, clean, make_handler, read_issue

LABELED_AT = "2026-10-25T10:00:00Z"
MAINTAINER, OUTSIDER = "maint", "mallory-x"


def issue(number, body="", title="标题", author=MAINTAINER, created_at="2026-10-20T08:00:00Z",
          edits=(), renames=(), state="open"):
    return Issue(number, state, title, body, author, created_at, tuple(edits), tuple(renames))


class FakeSource:
    """代替 content_check.Source：编号对应事先准备好的 Issue、"PullRequest"，或要抛的异常。"""

    def __init__(self, issues, allowed=(MAINTAINER,)):
        self._issues = issues
        self._allowed = set(allowed)
        self.asked = []

    def issues(self, numbers):
        self.asked.extend(numbers)
        found = {number: self._issues.get(number) for number in numbers}
        for item in found.values():
            if isinstance(item, Exception):
                raise item
        return found

    def is_allowed(self, login):
        return login in self._allowed


class Step3ReceiveTest(unittest.TestCase):
    """第 3 步：真的起一个服务，在本机端口上问。"""

    def setUp(self):
        self.source = FakeSource({3: issue(3, "正文"), 4: KeyError("坏数据")})
        service = IssueService(self.source, LABELED_AT, log=io.StringIO())
        self.server = HTTPServer(("127.0.0.1", 0), make_handler(service))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_issue(self):
        self.assertEqual(self.get("/issue/3"), (200, {"read": True, "title": "标题", "state": "open", "body": "正文"}))

    def test_health(self):
        self.assertEqual(self.get("/health"), (200, {"ok": True}))

    def test_only_issue_paths(self):
        for path in ("/issue/0", "/issue/abc", "/issue/3/comments", "/repos/octo/demo/issues/3", "/"):
            self.assertEqual(self.get(path)[0], 404, path)
        self.assertEqual(self.source.asked, [])

    def test_internal_error_still_answers(self):
        # 服务把出错经过打到 stderr，测试时收起来
        with contextlib.redirect_stderr(io.StringIO()) as printed:
            status, body = self.get("/issue/4")
        self.assertIn("KeyError", printed.getvalue())
        self.assertEqual(status, 500)
        self.assertIn("internal error", body["error"])


class Step3And7RememberTest(unittest.TestCase):
    """第 3 步读过的直接给，第 7 步记住、记日志；GitHub 出错的不记。"""

    def service(self, issues):
        self.log = io.StringIO()
        self.source = FakeSource(issues)
        return IssueService(self.source, LABELED_AT, log=self.log)

    def records(self):
        return [json.loads(line) for line in self.log.getvalue().splitlines()]

    def test_read_once_then_remembered(self):
        service = self.service({3: issue(3, "正文")})
        self.assertEqual(service.answer(3), service.answer(3))
        self.assertEqual(self.source.asked, [3])
        self.assertEqual([(r["number"], r["cached"], r["read"]) for r in self.records()],
                         [(3, False, True), (3, True, True)])

    def test_github_error_is_passed_back_and_not_remembered(self):
        service = self.service({3: RuntimeError("GraphQL 查询失败：HTTP 403")})
        self.assertEqual(service.answer(3), (502, {"error": "GraphQL 查询失败：HTTP 403"}))
        service.answer(3)
        self.assertEqual(self.source.asked, [3, 3])
        self.assertEqual(self.records()[0]["error"], "GraphQL 查询失败：HTTP 403")

    def test_writers_only_in_the_log(self):
        service = self.service({3: issue(3, "外人写的", author=OUTSIDER)})
        self.assertEqual(service.answer(3), (200, {"read": False, "skipped": "untrusted_writer",
                                                   "writers": [OUTSIDER]}))
        self.assertEqual(self.records()[0]["writers"], [OUTSIDER])


class Step4AskGitHubTest(unittest.TestCase):
    def test_passes_through_what_github_says(self):
        source = FakeSource({3: issue(3), 7: "PullRequest"})
        self.assertIsInstance(ask_github(source, 3), Issue)
        self.assertEqual(ask_github(source, 7), "PullRequest")
        self.assertIsNone(ask_github(source, 9))

    def test_number_too_large_is_not_asked(self):
        source = FakeSource({})
        self.assertIsNone(ask_github(source, 2**31))
        self.assertEqual(source.asked, [])


class Step5CheckVersionTest(unittest.TestCase):
    def check(self, item):
        return check_version(FakeSource({}), item, LABELED_AT)

    def test_trusted_issue_passes_with_label_time_version(self):
        edits = [("2026-10-26T09:00:00Z", MAINTAINER, "打标签后改的"), ("2026-10-20T08:00:00Z", MAINTAINER, "当时的")]
        self.assertEqual(self.check(issue(3, "打标签后改的", edits=edits)).body, "当时的")

    def test_pull_request_and_missing(self):
        self.assertEqual(self.check("PullRequest"), {"read": False, "skipped": "pull_request"})
        self.assertEqual(self.check(None), {"read": False, "skipped": "not_found"})

    def test_created_after_label(self):
        self.assertEqual(self.check(issue(3, created_at="2026-10-26T00:00:00Z")),
                         {"read": False, "skipped": "created_after_label"})

    def test_written_by_outsider(self):
        self.assertEqual(self.check(issue(3, "外人写的", author=OUTSIDER)),
                         {"read": False, "skipped": "untrusted_writer", "writers": [OUTSIDER]})

    def test_body_edited_by_maintainer_but_title_still_outsiders(self):
        edits = [("2026-10-24T09:00:00Z", MAINTAINER, "整理过"), ("2026-10-20T08:00:00Z", OUTSIDER, "原文")]
        self.assertEqual(self.check(issue(3, "整理过", author=OUTSIDER, edits=edits))["skipped"], "untrusted_writer")

    def test_edited_by_deleted_account(self):
        edits = [("2026-10-24T09:00:00Z", "", "改过"), ("2026-10-20T08:00:00Z", MAINTAINER, "原文")]
        self.assertEqual(self.check(issue(3, "改过", edits=edits))["writers"], ["ghost"])

    def test_body_deleted(self):
        edits = [("2026-10-24T09:00:00Z", MAINTAINER, None), ("2026-10-20T08:00:00Z", MAINTAINER, "原文")]
        self.assertEqual(self.check(issue(3, edits=edits)), {"read": False, "skipped": "body_deleted"})


class Step6CleanTest(unittest.TestCase):
    def test_cleaned_and_images_omitted(self):
        item = issue(3, "如图 ![](https://example.com/a.png)<!-- 藏起来的话 -->", state="closed")
        self.assertEqual(clean(version_at(item, LABELED_AT), item),
                         {"read": True, "title": "标题", "state": "closed", "body": "如图 [image omitted]"})


class Steps4To6TogetherTest(unittest.TestCase):
    def test_read_issue(self):
        source = FakeSource({3: issue(3, "背景说明", title="相关问题"), 7: "PullRequest"})
        self.assertEqual(read_issue(source, 3, LABELED_AT),
                         {"read": True, "title": "相关问题", "state": "open", "body": "背景说明"})
        self.assertEqual(read_issue(source, 7, LABELED_AT), {"read": False, "skipped": "pull_request"})


if __name__ == "__main__":
    unittest.main()
