import base64
import gzip
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from repoaegis.langfuse_upload import batches, build, explain_exit, read_trajectory, upload, usage_details

REPO = "octo/demo"
START = {
    "type": "run_start", "time": "2026-10-30T12:00:00+00:00", "repo": REPO, "issue": 8,
    "issue_url": f"https://github.com/{REPO}/issues/8", "labeled_by": "octocat",
    "labeled_at": "2026-10-30T11:59:00Z", "run_url": f"https://github.com/{REPO}/actions/runs/1",
    "base_commit": "9f8e7d6", "version": "abc1234", "model": "deepseek-flash",
    "model_parameters": {"temperature": 0}, "limits": "最多 50 步",
    "messages": [{"role": "system", "content": "You fix issues."}, {"role": "user", "content": "parse_date 崩溃"}],
    "tools": [{"type": "function", "function": {"name": "bash"}}],
}


def call(step, start, end, tool="bash", args='{"command": "ls"}'):
    return {"type": "model_call", "step": step, "start": start, "end": end,
            "response": {"role": "assistant", "content": f"第 {step} 步", "reasoning_content": "想一想",
                         "tool_calls": [{"id": f"c{step}", "type": "function",
                                         "function": {"name": tool, "arguments": args}}]},
            "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_cache_hit_tokens": 600,
                      "completion_tokens_details": {"reasoning_tokens": 20}},
            "finish_reason": "tool_calls", "retries": 0}


def tool(step, start, end, text, args='{"command": "ls"}', error=False, details=None, **extra):
    return {"type": "tool_call", "step": step, "call_id": f"c{step}", "name": "bash", "arguments": args,
            "start": start, "end": end, "text": text, "error": error, "details": details or {}, **extra}


EVENTS = [
    START,
    call(1, "2026-10-30T12:00:01+00:00", "2026-10-30T12:00:03+00:00"),
    tool(1, "2026-10-30T12:00:03+00:00", "2026-10-30T12:00:04+00:00", "Exit code: 0\nREADME.md",
         details={"exit_code": 0, "timed_out": False, "truncated": False, "output_path": None}),
    call(2, "2026-10-30T12:00:04+00:00", "2026-10-30T12:00:06+00:00", args='{"command": "python -m pytest"}'),
    tool(2, "2026-10-30T12:00:06+00:00", "2026-10-30T12:00:10+00:00", "Exit code: 1\n[cut]",
         args='{"command": "python -m pytest"}', full_output="1 failed, 2 passed\n完整输出",
         details={"exit_code": 1, "timed_out": False, "truncated": True, "output_path": "/tmp/out"}),
    call(3, "2026-10-30T12:00:10+00:00", "2026-10-30T12:00:12+00:00", args='{"command": "sleep 999"}'),
    tool(3, "2026-10-30T12:00:12+00:00", "2026-10-30T12:02:12+00:00",
         "Command timed out after 120 seconds and was killed.", args='{"command": "sleep 999"}', error=True,
         details={"exit_code": None, "timed_out": True, "truncated": False, "output_path": None}),
    {"type": "note", "time": "2026-10-30T12:02:13+00:00", "text": "达到步数上限", "level": "WARNING"},
    {"type": "delivery", "name": "push-branch", "start": "2026-10-30T12:02:14+00:00",
     "end": "2026-10-30T12:02:15+00:00", "ok": True, "result": "推送分支成功"},
    {"type": "delivery", "name": "open-pr", "start": "2026-10-30T12:02:15+00:00",
     "end": "2026-10-30T12:02:16+00:00", "ok": False, "result": "开 PR 失败：422"},
    {"type": "run_end", "time": "2026-10-30T12:02:17+00:00", "outcome": "delivery_failed",
     "reason": "开 PR 时 GitHub 返回 422", "report": "改了 parse_date"},
]


def attrs(span):
    return {a["key"]: (a["value"].get("stringValue") if "stringValue" in a["value"]
                       else [v["stringValue"] for v in a["value"]["arrayValue"]["values"]])
            for a in span["attributes"]}


def by_name(spans, name):
    return [s for s in spans if s["name"] == name]


class ExplainTest(unittest.TestCase):
    def test_program_specific_codes_come_first(self):
        self.assertIn("测试文件路径不存在", explain_exit("python -m pytest -q tests/x.py", 4))
        self.assertEqual(explain_exit("grep -rn foo src", 1), "没有找到匹配的内容（按 grep 的约定）")
        self.assertIn("不在仓库里", explain_exit("cd repo && git log", 128))

    def test_program_name_must_be_a_whole_word(self):
        self.assertEqual(explain_exit("./pytest_helper.sh", 4), "非零退出码，含义取决于具体程序")

    def test_generic_codes_and_signals(self):
        self.assertEqual(explain_exit("foo", 127), "找不到这个命令")
        self.assertEqual(explain_exit("make", 137), "被信号 SIGKILL 结束")
        self.assertEqual(explain_exit("make", 3), "非零退出码，含义取决于具体程序")

    def test_usage_split_into_four_parts(self):
        deepseek = {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_cache_hit_tokens": 600,
                    "completion_tokens_details": {"reasoning_tokens": 20}}
        self.assertEqual(usage_details(deepseek), {"input": 400, "input_cached_tokens": 600, "output": 30,
                                                   "output_reasoning_tokens": 20})
        openai = {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 4}}
        self.assertEqual(usage_details(openai)["input_cached_tokens"], 4)
        self.assertIsNone(usage_details(None))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.trace_id, self.spans, self.scores = build(EVENTS, "sandbox")

    def test_structure(self):
        root = by_name(self.spans, "agent-fix")[0]
        self.assertNotIn("parentSpanId", root)
        self.assertEqual(len(by_name(self.spans, "call-model")), 3)
        self.assertEqual(len(by_name(self.spans, "bash")), 3)
        deliver = by_name(self.spans, "deliver")[0]
        for span in self.spans:
            if span["name"] in ("call-model", "bash", "note", "deliver"):
                self.assertEqual(span["parentSpanId"], root["spanId"])
        self.assertEqual(by_name(self.spans, "open-pr")[0]["parentSpanId"], deliver["spanId"])
        self.assertTrue(all(s["traceId"] == self.trace_id for s in self.spans))

    def test_same_trajectory_gives_same_ids(self):
        trace_id, spans, scores = build(EVENTS, "sandbox")
        self.assertEqual(trace_id, self.trace_id)
        self.assertEqual([s["spanId"] for s in spans], [s["spanId"] for s in self.spans])
        self.assertEqual([s["id"] for s in scores], [s["id"] for s in self.scores])

    def test_trace_attributes_and_readable_outcome(self):
        root = attrs(by_name(self.spans, "agent-fix")[0])
        self.assertEqual(root["session.id"], f"{REPO}#8 打标签于 2026-10-30T11:59:00Z")
        self.assertEqual(root["langfuse.trace.tags"], ["agent-fix", "结局：交付失败"])
        self.assertEqual(root["user.id"], "octocat")
        self.assertEqual(root["langfuse.environment"], "sandbox")
        self.assertEqual(root["langfuse.observation.level"], "ERROR")
        self.assertEqual(json.loads(root["langfuse.trace.metadata.结局"]), "delivery_failed（交付失败）")
        output = json.loads(root["langfuse.observation.output"])["content"]
        self.assertIn("结局：交付失败", output)
        self.assertIn("调用模型 3 次、工具 3 次（其中 1 次出错）", output)

    def test_each_model_call_carries_the_conversation_so_far(self):
        calls = [json.loads(attrs(s)["langfuse.observation.input"]) for s in by_name(self.spans, "call-model")]
        self.assertEqual(len(calls[0]["messages"]), 2)
        self.assertEqual([m["role"] for m in calls[1]["messages"]], ["system", "user", "assistant", "tool"])
        self.assertEqual(calls[1]["messages"][3]["tool_call_id"], "c1")
        self.assertEqual(calls[1]["tools"], START["tools"])
        first = attrs(by_name(self.spans, "call-model")[0])
        self.assertEqual(json.loads(first["langfuse.observation.metadata.结束原因"]), "tool_calls（模型要调用工具）")
        self.assertEqual(json.loads(first["langfuse.observation.usage_details"])["input_cached_tokens"], 600)
        self.assertEqual(first["langfuse.observation.model.name"], "deepseek-flash")

    def test_tool_levels_and_explanations(self):
        ok, failing, timed_out = [attrs(s) for s in by_name(self.spans, "bash")]
        self.assertNotIn("langfuse.observation.level", ok)
        self.assertEqual(json.loads(ok["langfuse.observation.metadata.退出码"]), "0（成功）")
        self.assertEqual(failing["langfuse.observation.level"], "WARNING")
        self.assertEqual(failing["langfuse.observation.status_message"],
                         "命令以退出码 1 结束：有测试没通过（按 pytest 的约定）。输出最后一行：完整输出")
        self.assertEqual(failing["langfuse.observation.output"], "1 failed, 2 passed\n完整输出")
        self.assertIn("完整输出", json.loads(failing["langfuse.observation.metadata.说明"]))
        self.assertEqual(timed_out["langfuse.observation.level"], "ERROR")
        self.assertEqual(timed_out["langfuse.observation.status_message"], "命令超时，被强制停止")
        self.assertEqual(json.loads(timed_out["langfuse.observation.metadata.退出码"]), "无（超时被停止）")
        self.assertEqual(json.loads(timed_out["langfuse.observation.metadata.超时被停止"]), "是")

    def test_last_output_line_shows_when_the_guess_is_wrong(self):
        # 2026-10-31 在 Actions 上实测：没装 pytest 时 python -m pytest 也返回 1，只看退出码会误以为测试没通过
        events = [START, tool(1, "2026-10-30T12:00:01+00:00", "2026-10-30T12:00:02+00:00",
                              "Exit code: 1\n/usr/bin/python: No module named pytest\n",
                              args='{"command": "python -m pytest -q"}', details={"exit_code": 1})]
        _, spans, _ = build(events)
        status = attrs(by_name(spans, "bash")[0])["langfuse.observation.status_message"]
        self.assertEqual(status, "命令以退出码 1 结束：有测试没通过（按 pytest 的约定）。"
                                 "输出最后一行：/usr/bin/python: No module named pytest")

    def test_scores(self):
        scores = {s["name"]: s for s in self.scores}
        self.assertEqual(scores["结局"]["value"], "交付失败")
        self.assertEqual(scores["结局"]["comment"], "开 PR 时 GitHub 返回 422")
        self.assertEqual(scores["调用模型次数"]["value"], 3)
        self.assertEqual(scores["工具出错次数"]["value"], 1)

    def test_trajectory_without_run_end_is_marked_unfinished(self):
        _, spans, scores = build(EVENTS[:3])
        root = attrs(by_name(spans, "agent-fix")[0])
        self.assertEqual(root["langfuse.trace.tags"], ["agent-fix", "结局：轨迹中途结束"])
        self.assertEqual(root["langfuse.observation.level"], "ERROR")
        self.assertEqual(scores[0]["value"], "轨迹中途结束")

    def test_trajectory_without_run_start_is_rejected(self):
        with self.assertRaises(ValueError):
            build(EVENTS[1:])


class FileAndBatchTest(unittest.TestCase):
    def test_half_written_last_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trajectory.jsonl")
            with open(path, "w", encoding="utf-8") as file:
                file.write(json.dumps(START, ensure_ascii=False) + "\n\n{\"type\": \"model_c")
            events, bad = read_trajectory(path)
        self.assertEqual(len(events), 1)
        self.assertEqual(bad, 1)

    def test_batches_respect_size_and_keep_oversized_spans_alone(self):
        spans = [{"a": "x" * 40}, {"a": "x" * 40}, {"a": "x" * 500}, {"a": "x" * 40}]
        groups = batches(spans, limit=120)
        self.assertEqual([len(g) for g in groups], [2, 1, 1])


class FakeLangfuse(BaseHTTPRequestHandler):
    received = []
    fail_spans = False

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        FakeLangfuse.received.append((self.path, self.headers["Authorization"], json.loads(body)))
        if FakeLangfuse.fail_spans and "otel" in self.path:
            self._reply(400, {"error": "bad"})
        else:
            self._reply(200, {})

    def do_GET(self):
        self._reply(200, {"data": [{"id": "proj1"}]})

    def _reply(self, status, data):
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class UploadTest(unittest.TestCase):
    def setUp(self):
        FakeLangfuse.received, FakeLangfuse.fail_spans = [], False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLangfuse)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "trajectory.jsonl")
        with open(self.path, "w", encoding="utf-8") as file:
            file.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in EVENTS)
        self.env = {"LANGFUSE_PUBLIC_KEY": "pk-test", "LANGFUSE_SECRET_KEY": "sk-test",
                    "LANGFUSE_BASE_URL": f"http://127.0.0.1:{self.server.server_port}"}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_without_keys_it_skips(self):
        out = io.StringIO()
        self.assertEqual(upload(self.path, env={}, out=out), 0)
        self.assertIn("跳过上传", out.getvalue())

    def test_sends_spans_then_scores_and_prints_the_link(self):
        out = io.StringIO()
        env = {**self.env, "LANGFUSE_SECRET_KEY": "sk-test\r\n"}  # 粘贴时带进来的回车换行要去掉
        self.assertEqual(upload(self.path, env=env, out=out), 0)
        self.assertEqual(FakeLangfuse.received[0][1], "Basic " + base64.b64encode(b"pk-test:sk-test").decode())
        paths = [p for p, _, _ in FakeLangfuse.received]
        self.assertEqual(paths, ["/api/public/otel/v1/traces"] + ["/api/public/scores"] * 3)
        self.assertTrue(all(auth.startswith("Basic ") for _, auth, _ in FakeLangfuse.received))
        spans = FakeLangfuse.received[0][2]["resourceSpans"][0]["scopeSpans"][0]["spans"]
        self.assertEqual(len(spans), 11)
        trace_id = spans[0]["traceId"]
        self.assertIn(f"/project/proj1/traces/{trace_id}", out.getvalue())

    def test_failure_only_warns(self):
        FakeLangfuse.fail_spans = True
        out = io.StringIO()
        self.assertEqual(upload(self.path, env=self.env, out=out), 0)
        self.assertIn("::warning::上传 Langfuse 失败", out.getvalue())
        self.assertIn("返回 400", out.getvalue())


if __name__ == "__main__":
    unittest.main()
