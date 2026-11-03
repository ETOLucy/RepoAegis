"""read_issue_ref 的测试，按流程图的第 2、8 步分组。读 issue 服务用一个假的代替。"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from repoaegis.tools import call_tool
from repoaegis.tools.read_issue_ref import MAX_BODY_CHARS, SKIPPED, ReadIssueRef

ISSUE = 10
SERVICE_URL = "http://host.docker.internal:18080"
LONG_BODY = "".join(f"{i:05d}\n" for i in range(7000))  # 42000 字符

# 假的读 issue 服务：路径 → (状态码, 回答原文)
ANSWERS = {
    "/issue/3": (200, {"read": True, "title": "parse_date 遇到空字符串时崩溃", "state": "OPEN",
                       "body": "传入 \"\" 时抛出 IndexError。"}),
    "/issue/5": (200, {"read": True, "title": "很长的背景", "state": "closed", "body": LONG_BODY}),
    "/issue/6": (200, {"read": True, "title": "只有标题", "state": "open", "body": ""}),
    "/issue/7": (200, {"read": False, "skipped": "pull_request"}),
    "/issue/8": (200, {"read": False, "skipped": "untrusted_writer", "writers": ["mallory-x"]}),
    "/issue/9": (502, {"error": "GraphQL 查询失败：HTTP 403"}),
    "/issue/11": (500, {"error": "internal error in the issue service: boom"}),
    "/issue/12": (200, "<html>not json</html>"),
}


class FakeService(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):
        FakeService.requests.append((self.path, self.headers["Host"]))
        # 当代理用时，请求行是完整网址
        path = self.path.removeprefix(SERVICE_URL)
        status, answer = ANSWERS.get(path, (404, {"error": "unknown"}))
        data = (answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


class WithFakeService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeService)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.address = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeService.requests = []
        # 本机测试直接连假服务；"走代理"单独测
        self.tool = ReadIssueRef(ISSUE, self.address)

    def read(self, number, **more):
        return self.tool.run({"number": number, **more})


class Step2CheckArgsTest(WithFakeService):
    def test_bad_arguments_are_not_sent(self):
        for args in ({}, {"number": "3"}, {"number": True}, {"number": 0}, {"number": 3, "offset": -1},
                     {"number": 3, "offset": 1.5}):
            self.assertTrue(self.tool.run(args).error, args)
        self.assertEqual(FakeService.requests, [])

    def test_own_issue_is_not_sent(self):
        result = self.read(ISSUE)
        self.assertTrue(result.error)
        self.assertIn("the issue you are working on", result.text)
        self.assertEqual(FakeService.requests, [])


class Step2AskServiceTest(WithFakeService):
    def test_goes_through_proxy(self):
        # 容器里：连 AWF 的代理，请求行写服务的完整网址，Host 是服务的地址
        tool = ReadIssueRef(ISSUE, SERVICE_URL, proxy=self.address)
        self.assertFalse(tool.run({"number": 3}).error)
        self.assertEqual(FakeService.requests, [(f"{SERVICE_URL}/issue/3", "host.docker.internal:18080")])

    def test_from_env(self):
        tool = ReadIssueRef.from_env({"issue": ISSUE}, {"ISSUE_SERVICE_URL": SERVICE_URL, "HTTP_PROXY": self.address})
        self.assertFalse(tool.run({"number": 3}).error)
        self.assertEqual(FakeService.requests[0][0], f"{SERVICE_URL}/issue/3")

    def test_broken_service_raises(self):
        # 500：服务自己出错；200 但不是 JSON：回答看不懂
        for number in (11, 12):
            with self.assertRaises(RuntimeError):
                self.read(number)


class Step8ToTextTest(WithFakeService):
    def test_reads_issue(self):
        result = self.read(3)
        self.assertFalse(result.error)
        self.assertEqual(result.text, '<issue number="3" state="open">\n'
                                      "<title>parse_date 遇到空字符串时崩溃</title>\n"
                                      "<body>\n传入 \"\" 时抛出 IndexError。\n</body>\n"
                                      "</issue>")
        self.assertEqual(result.details, {"number": 3, "offset": 0, "shown": [0, 21], "body_chars": 21})

    def test_long_body_in_parts(self):
        first = self.read(5)
        self.assertIn(LONG_BODY[:MAX_BODY_CHARS] + "\n</body>", first.text)
        self.assertTrue(first.text.endswith(f"[Body characters 0-16000 of {len(LONG_BODY)} shown. "
                                            "To read the next part, call read_issue_ref with number=5 and offset=16000.]"))
        last = self.read(5, offset=32000)
        self.assertIn("<title>很长的背景</title>", last.text)
        self.assertIn(LONG_BODY[32000:] + "\n</body>", last.text)
        self.assertTrue(last.text.endswith(f"[Body characters 32000-{len(LONG_BODY)} of {len(LONG_BODY)} shown. "
                                           "This is the end of the body.]"))

    def test_offset_past_the_end(self):
        result = self.read(5, offset=len(LONG_BODY))
        self.assertTrue(result.error)
        self.assertEqual(result.details["reason"], "bad_offset")

    def test_empty_body(self):
        result = self.read(6)
        self.assertFalse(result.error)
        self.assertIn("<body>\n\n</body>", result.text)

    def test_not_available(self):
        for number, reason in ((7, "pull_request"), (8, "untrusted_writer")):
            result = self.read(number)
            self.assertTrue(result.error)
            self.assertEqual(result.text, f"Error: #{number} {SKIPPED[reason]}")
            self.assertEqual(result.details["reason"], reason)
        # 服务记下的人名不发给模型
        self.assertNotIn("mallory-x", self.read(8).text)

    def test_github_error_is_passed_on(self):
        result = self.read(9)
        self.assertTrue(result.error)
        self.assertIn("GraphQL 查询失败：HTTP 403", result.text)
        self.assertEqual(result.details["reason"], "github_error")

    def test_called_through_call_tool(self):
        tool = self.tool.tool()
        self.assertEqual(tool.spec()["function"]["parameters"]["required"], ["number"])
        self.assertFalse(call_tool({tool.name: tool}, "read_issue_ref", '{"number": 3}').error)


if __name__ == "__main__":
    unittest.main()
