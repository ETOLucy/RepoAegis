import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from repoaegis.loop import ModelError
from repoaegis.model import DeepSeek

TOOLS = [{"type": "function", "function": {"name": "bash", "description": "run", "parameters": {"type": "object"}}}]
MESSAGES = [{"role": "user", "content": "fix the typo"}]
USAGE = {"prompt_tokens": 3200, "completion_tokens": 180, "prompt_cache_hit_tokens": 2944,
         "prompt_cache_miss_tokens": 256, "completion_tokens_details": {"reasoning_tokens": 120}}


def data(obj) -> str:
    return "data: " + json.dumps(obj)


def delta(**fields) -> str:
    return data({"choices": [{"index": 0, "delta": fields, "finish_reason": None}], "usage": None})


def end(reason="tool_calls", usage=USAGE) -> list[str]:
    return [data({"choices": [{"index": 0, "delta": {}, "finish_reason": reason}], "usage": usage}), "data: [DONE]"]


def stream(*lines, status=200, headers=None, delays=None):
    """假 DeepSeek 的一次回应：lines 一行行发出，delays 是 {第几行: 发之前等几秒}。"""
    return {"status": status, "headers": headers or {}, "lines": list(lines), "delays": delays or {}}


def error(status, message, headers=None):
    return stream(json.dumps({"error": {"message": message, "type": "invalid_request_error"}}),
                  status=status, headers=headers)


class FakeDeepSeek:
    """本机起一个假的模型服务，按脚本依次回应；记下每次收到的路径、请求头和请求体。"""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.requests.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(body)})
                item = fake.script.pop(0)
                self.send_response(item["status"])
                content_type = "text/event-stream" if item["status"] == 200 else "application/json"
                self.send_header("Content-Type", content_type)
                for name, value in item["headers"].items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    for number, line in enumerate(item["lines"]):
                        time.sleep(item["delays"].get(number, 0))
                        self.wfile.write((line + "\n\n").encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # 客户端等不及先断开了，正是超时类测试要的情况

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ModelTest(unittest.TestCase):
    def start(self, *script, base_path="", **options):
        self.fake = FakeDeepSeek(*script)
        self.addCleanup(self.fake.close)
        self.logs, self.waits = [], []
        options.setdefault("sleep", self.waits.append)
        return DeepSeek(self.fake.url + base_path, "sk-test\r", log=self.logs.append, **options)

    def test_assembles_reasoning_content_and_tool_calls(self):
        model = self.start(stream(
            ": keep-alive", "",
            delta(role="assistant", content="", reasoning_content="The issue says "),
            delta(reasoning_content="'teh'."),
            delta(tool_calls=[{"index": 0, "id": "call_1", "type": "function",
                               "function": {"name": "bash", "arguments": ""}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": "{\"command\": \"grep -n"}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": " teh README.md\"}"}}]),
            *end()))
        reply = model(MESSAGES, TOOLS, 60)
        self.assertEqual(reply.message, {
            "role": "assistant", "content": None, "reasoning_content": "The issue says 'teh'.",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "bash", "arguments": "{\"command\": \"grep -n teh README.md\"}"}}]})
        self.assertEqual(reply.usage, USAGE)
        self.assertEqual(reply.finish_reason, "tool_calls")
        self.assertEqual(reply.retries, 0)
        self.assertIsNotNone(reply.first_token)

    def test_request_carries_settings_and_key(self):
        model = self.start(stream(delta(content="ok"), *end("stop")), base_path="/v1")
        model(MESSAGES, TOOLS, 60)
        request = self.fake.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(request["body"], {
            "model": "deepseek-flash", "messages": MESSAGES, "stream": True, "stream_options": {"include_usage": True},
            "thinking": {"type": "enabled"}, "reasoning_effort": "high", "tools": TOOLS})

    def test_no_tools_key_when_there_are_no_tools(self):
        model = self.start(stream(delta(content="hi"), *end("stop")))
        reply = model(MESSAGES, [], 60)
        self.assertNotIn("tools", self.fake.requests[0]["body"])
        self.assertEqual(reply.message, {"role": "assistant", "content": "hi"})

    def test_two_tool_calls_by_index(self):
        model = self.start(stream(
            delta(tool_calls=[{"index": 0, "id": "a", "type": "function", "function": {"name": "bash", "arguments": "{"}}]),
            delta(tool_calls=[{"index": 1, "id": "b", "type": "function", "function": {"name": "submit", "arguments": "{"}}]),
            delta(tool_calls=[{"index": 0, "function": {"arguments": "}"}}, {"index": 1, "function": {"arguments": "}"}}]),
            *end()))
        calls = model(MESSAGES, TOOLS, 60).message["tool_calls"]
        self.assertEqual([(c["id"], c["function"]["name"], c["function"]["arguments"]) for c in calls],
                         [("a", "bash", "{}"), ("b", "submit", "{}")])

    def test_repeated_id_and_name_are_not_doubled(self):
        piece = {"index": 0, "id": "a", "type": "function", "function": {"name": "bash"}}
        model = self.start(stream(
            delta(tool_calls=[{**piece, "function": {"name": "bash", "arguments": "{\"command\""}}]),
            delta(tool_calls=[{**piece, "function": {"name": "bash", "arguments": ": \"ls\"}"}}]),
            *end()))
        call = model(MESSAGES, TOOLS, 60).message["tool_calls"][0]
        self.assertEqual((call["id"], call["function"]["name"], call["function"]["arguments"]),
                         ("a", "bash", "{\"command\": \"ls\"}"))

    def test_length_is_returned_not_retried(self):
        model = self.start(stream(delta(content="partial"), *end("length")))
        reply = model(MESSAGES, TOOLS, 60)
        self.assertEqual(reply.finish_reason, "length")
        self.assertEqual(len(self.fake.requests), 1)

    def test_retries_overload_with_growing_waits(self):
        model = self.start(error(503, "busy"), error(500, "oops"), stream(delta(content="ok"), *end("stop")))
        reply = model(MESSAGES, TOOLS, 600)
        self.assertEqual(reply.retries, 2)
        self.assertEqual(self.waits, [8, 16])
        self.assertIn("HTTP 503 服务器过载：busy", self.logs[0])
        self.assertIn("第 1/5 次", self.logs[0])

    def test_retry_after_header_is_followed(self):
        model = self.start(error(429, "slow down", headers={"Retry-After": "3"}), stream(*end("stop")))
        model(MESSAGES, TOOLS, 600)
        self.assertEqual(self.waits, [3])

    def test_retry_after_over_the_cap_is_not_waited(self):
        model = self.start(error(429, "slow down", headers={"Retry-After": "120"}))
        with self.assertRaises(ModelError) as caught:
            model(MESSAGES, TOOLS, 3600)
        self.assertEqual(caught.exception.kind, "failed")
        self.assertIn("要求等 120 秒，超过上限 64 秒", str(caught.exception))
        self.assertEqual(self.waits, [])
        self.assertEqual(len(self.fake.requests), 1)

    def test_gives_up_after_retries(self):
        model = self.start(*[error(502, "Proxy error")] * 3, retries=2)
        with self.assertRaises(ModelError) as caught:
            model(MESSAGES, TOOLS, 600)
        self.assertEqual(caught.exception.kind, "failed")
        self.assertEqual(caught.exception.retries, 2)
        self.assertIn("已重试 2 次", str(caught.exception))

    def test_client_errors_are_not_retried(self):
        for status, words in [(400, "请求格式不对"), (401, "钥匙不对"), (402, "账户余额不足"), (422, "参数不对")]:
            with self.subTest(status=status):
                model = self.start(error(status, "bad"))
                with self.assertRaises(ModelError) as caught:
                    model(MESSAGES, TOOLS, 600)
                self.assertEqual(caught.exception.kind, "failed")
                self.assertIn(words, str(caught.exception))
                self.assertEqual(len(self.fake.requests), 1)
                self.assertEqual(self.waits, [])

    def test_error_after_retry_keeps_retry_count(self):
        model = self.start(error(503, "busy"), error(402, "Insufficient Balance"))
        with self.assertRaises(ModelError) as caught:
            model(MESSAGES, TOOLS, 600)
        self.assertEqual(caught.exception.retries, 1)

    def test_context_too_long(self):
        model = self.start(error(400, "This model's maximum context length is 1048576 tokens."))
        with self.assertRaises(ModelError) as caught:
            model(MESSAGES, TOOLS, 600)
        self.assertEqual(caught.exception.kind, "context_too_long")

    def test_stream_cut_before_done_is_retried(self):
        model = self.start(stream(delta(content="half")), stream(delta(content="whole"), *end("stop")))
        reply = model(MESSAGES, TOOLS, 600)
        self.assertEqual(reply.message["content"], "whole")
        self.assertEqual(reply.retries, 1)
        self.assertIn("回答没收完连接就关了", self.logs[0])

    def test_error_in_the_middle_of_the_answer(self):
        cases = [({"message": "Insufficient Balance", "type": "unknown_error"}, "failed"),
                 ({"message": "Content Exists Risk", "code": 400}, "failed"),
                 ({"message": "This model's maximum context length is 1048576 tokens."}, "context_too_long")]
        for payload, kind in cases:
            with self.subTest(payload=payload):
                model = self.start(stream(delta(content="half"), data({"error": payload})))
                with self.assertRaises(ModelError) as caught:
                    model(MESSAGES, TOOLS, 600)
                self.assertEqual(caught.exception.kind, kind)
                self.assertIn("回答中途报错", str(caught.exception))
                self.assertEqual(len(self.fake.requests), 1)

    def test_transient_error_in_the_middle_is_retried(self):
        model = self.start(stream(delta(content="half"), data({"error": {"message": "Server busy", "code": 503}})),
                           stream(delta(content="whole"), *end("stop")))
        reply = model(MESSAGES, TOOLS, 600)
        self.assertEqual(reply.message["content"], "whole")
        self.assertEqual(self.waits, [8])

    def test_interrupted_generation_is_retried(self):
        model = self.start(stream(delta(content="x"), *end("insufficient_system_resource")),
                           stream(delta(content="y"), *end("stop")))
        self.assertEqual(model(MESSAGES, TOOLS, 600).message["content"], "y")
        self.assertIn("insufficient_system_resource", self.logs[0])

    def test_idle_too_long_is_retried(self):
        model = self.start(stream(delta(content="a"), *end("stop"), delays={1: 1.0}),
                           stream(delta(content="b"), *end("stop")), idle_seconds=0.3)
        reply = model(MESSAGES, TOOLS, 600)
        self.assertEqual(reply.message["content"], "b")
        self.assertIn("超过 0.3 秒没有收到", self.logs[0])

    def test_deadline_stops_a_slow_answer(self):
        model = self.start(stream(delta(content="a"), *end("stop"), delays={1: 2.0}))
        started = time.monotonic()
        with self.assertRaises(ModelError) as caught:
            model(MESSAGES, TOOLS, 0.5)
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertLess(time.monotonic() - started, 1.5)

    def test_no_retry_when_wait_exceeds_remaining_time(self):
        model = self.start(error(503, "busy"))
        with self.assertRaises(ModelError) as caught:
            model(MESSAGES, TOOLS, 5)
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertIn("要等 8 秒再试", str(caught.exception))
        self.assertEqual(self.waits, [])

    def test_connection_refused_is_retried_then_fails(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        waits = []
        model = DeepSeek(f"http://127.0.0.1:{port}", "sk", log=lambda text: None, retries=1, sleep=waits.append)
        with self.assertRaises(ModelError) as caught:
            model(MESSAGES, TOOLS, 600)
        self.assertIn("连不上模型服务", str(caught.exception))
        self.assertEqual(waits, [8])

    def test_progress_lines_while_waiting_and_while_answering(self):
        with mock.patch("repoaegis.model.PROGRESS_SECONDS", 0.2):
            model = self.start(stream(": keep-alive", ": keep-alive", delta(reasoning_content="think"),
                                      delta(content="ok"), *end("stop"), delays={1: 0.3, 3: 0.3}))
            model(MESSAGES, TOOLS, 60)
        self.assertTrue(any("还在排队或准备" in line for line in self.logs), self.logs)
        self.assertTrue(any("还在输出" in line and "收到思考 5 字" in line for line in self.logs), self.logs)

    def test_from_env(self):
        env = {"OPENAI_BASE_URL": "http://172.30.0.30:10000", "OPENAI_API_KEY": "sk-placeholder-for-api-proxy"}
        with mock.patch.dict("os.environ", env, clear=True):
            model = DeepSeek.from_env()
        self.assertEqual((model.url.hostname, model.url.port, model.model), ("172.30.0.30", 10000, "deepseek-flash"))
        with mock.patch.dict("os.environ", {"REPOAEGIS_MODEL": "deepseek-v4-pro", "OPENAI_API_KEY": "k"}, clear=True):
            model = DeepSeek.from_env()
        self.assertEqual((model.url.hostname, model.model), ("api.deepseek.com", "deepseek-v4-pro"))
        with mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            DeepSeek.from_env()


if __name__ == "__main__":
    unittest.main()
