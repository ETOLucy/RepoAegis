import json
import os
import tempfile
import unittest

from repoaegis.loop import NO_TOOL_MESSAGE, Limits, ModelError, ModelReply, Trajectory, run
from repoaegis.tools import Tool, ToolResult

USAGE = {"prompt_tokens": 100, "completion_tokens": 10}


def call(name, arguments, call_id=None):
    return {"id": call_id or f"call-{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}


def reply(*calls, content="", usage=USAGE):
    message = {"role": "assistant", "content": content, "reasoning_content": "thinking"}
    if calls:
        message["tool_calls"] = list(calls)
    return ModelReply(message, usage, "tool_calls" if calls else "stop")


class FakeModel:
    """按脚本依次回复；脚本里放异常就抛出来。记下每次收到的对话和剩余秒数。"""

    def __init__(self, *script):
        self.script = list(script)
        self.seen = []

    def __call__(self, messages, specs, seconds_left):
        self.seen.append((list(messages), specs, seconds_left))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.trajectory = Trajectory(os.path.join(self.tmp.name, "trajectory.jsonl"))
        self.submits = 0
        self.ran = []
        self.has_changes = True

        def echo(args):
            self.ran.append(args)
            return ToolResult(f"ran {args['command']}", details={"exit_code": 0})

        def submit(args):
            # 和真的 submit 一样：第一次复查，第二次才交上
            self.submits += 1
            done = self.submits >= 2
            return ToolResult("submitted" if done else "review",
                              details={"has_changes": self.has_changes, "submitted": done})

        def broken(args):
            raise FileNotFoundError("repo is gone")

        params = {"type": "object", "properties": {}}
        self.tools = {"echo": Tool("echo", "run", params, echo), "submit": Tool("submit", "submit", params, submit),
                      "broken": Tool("broken", "broken", params, broken)}
        self.messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_loop(self, model, **kwargs):
        return run(model, self.tools, self.messages, self.trajectory, log=self.logs.append, **kwargs)

    def events(self):
        with open(self.trajectory.path, encoding="utf-8") as file:
            return [json.loads(line) for line in file]

    def test_submit_twice_ends_normally(self):
        model = FakeModel(reply(call("echo", {"command": "ls"})), reply(call("submit", {})), reply(call("submit", {})))
        outcome = self.run_loop(model)
        self.assertEqual(outcome.outcome, "submitted")
        self.assertEqual([e["type"] for e in self.events()],
                         ["model_call", "tool_call", "model_call", "tool_call", "model_call", "tool_call", "run_end"])
        # 第二次调模型时，对话里有第一次的回复（含思考内容）和工具结果
        second = model.seen[1][0]
        self.assertEqual(second[2]["reasoning_content"], "thinking")
        self.assertEqual(second[3], {"role": "tool", "tool_call_id": "call-echo", "content": "ran ls"})
        self.assertEqual([s["function"]["name"] for s in model.seen[0][1]], ["echo", "submit", "broken"])

    def test_submit_without_changes(self):
        self.has_changes = False
        outcome = self.run_loop(FakeModel(reply(call("submit", {})), reply(call("submit", {}))))
        self.assertEqual(outcome.outcome, "no_change")

    def test_calls_after_submit_in_same_reply_are_skipped(self):
        self.submits = 1
        outcome = self.run_loop(FakeModel(reply(call("submit", {}), call("echo", {"command": "rm -rf x"}))))
        self.assertEqual(outcome.outcome, "submitted")
        self.assertEqual(self.ran, [])
        self.assertIn("后面的 1 个工具调用没有执行", self.events()[-2]["text"])

    def test_three_replies_without_tools_is_error(self):
        model = FakeModel(reply(), reply(), reply())
        outcome = self.run_loop(model)
        self.assertEqual(outcome.outcome, "error")
        self.assertIn("连续 3 次", outcome.reason)
        self.assertEqual(model.seen[1][0][-1], {"role": "user", "content": NO_TOOL_MESSAGE})

    def test_tool_call_resets_no_tool_count(self):
        self.submits = 1
        model = FakeModel(reply(), reply(), reply(call("echo", {"command": "ls"})), reply(), reply(),
                          reply(call("submit", {})))
        self.assertEqual(self.run_loop(model).outcome, "submitted")

    def test_token_limit_checked_before_each_model_call(self):
        # 每次 110 token，上限 300：第 3 次调用后累计 330，第 4 次之前停
        model = FakeModel(*[reply(call("echo", {"command": "ls"})) for _ in range(3)])
        outcome = self.run_loop(model, limits=Limits(tokens=300))
        self.assertEqual(outcome.outcome, "limit")
        self.assertEqual(len(model.seen), 3)
        self.assertEqual(len(self.ran), 3)  # 最后一次回复要的工具照样执行了

    def test_time_limit_counts_earlier_runs(self):
        clock = Clock()

        def slow(args):
            clock.value += 400
            return ToolResult("done")

        self.tools["echo"] = Tool("echo", "run", {"type": "object"}, slow)
        model = FakeModel(*[reply(call("echo", {"command": "ls"})) for _ in range(3)])
        outcome = self.run_loop(model, limits=Limits(seconds=1000), seconds_used=300, clock=clock)
        self.assertEqual(outcome.outcome, "timeout")
        self.assertEqual([seen[2] for seen in model.seen], [700, 300])  # 告诉模型还剩多少秒
        self.assertEqual(self.events()[-2]["level"], "WARNING")

    def test_model_error_ends_with_reason(self):
        model = FakeModel(ModelError("context_too_long", "1,048,576 tokens max", retries=0))
        outcome = self.run_loop(model)
        self.assertEqual(outcome.outcome, "error")
        self.assertIn("对话超过了模型能接收的长度", outcome.reason)
        failed = self.events()[0]
        self.assertNotIn("response", failed)
        self.assertEqual(failed["error"], "context_too_long: 1,048,576 tokens max")

    def test_tool_exception_ends_as_error(self):
        outcome = self.run_loop(FakeModel(reply(call("broken", {}))))
        self.assertEqual(outcome.outcome, "error")
        self.assertIn("FileNotFoundError", outcome.reason)
        self.assertTrue(self.events()[1]["error"])

    def test_bad_tool_name_goes_back_to_model(self):
        self.submits = 1
        model = FakeModel(reply(call("nope", {})), reply(call("submit", {})))
        self.assertEqual(self.run_loop(model).outcome, "submitted")
        self.assertIn("no tool named 'nope'", model.seen[1][0][-1]["content"])

    def test_unexpected_loop_error_still_writes_run_end(self):
        model = FakeModel(ModelReply({"role": "assistant", "tool_calls": [None]}, USAGE))
        outcome = self.run_loop(model)
        self.assertEqual(outcome.outcome, "error")
        self.assertIn("主循环出错", outcome.reason)
        self.assertEqual(self.events()[-1]["type"], "run_end")

    def test_full_output_recorded_when_truncated(self):
        path = os.path.join(self.tmp.name, "bash-1.txt")
        with open(path, "w", encoding="utf-8") as file:
            file.write("whole output")
        self.tools["echo"] = Tool("echo", "run", {"type": "object"},
                                  lambda args: ToolResult("cut", details={"truncated": True, "output_path": path}))
        self.submits = 1
        self.run_loop(FakeModel(reply(call("echo", {})), reply(call("submit", {}))))
        tool_event = self.events()[1]
        self.assertEqual(tool_event["text"], "cut")
        self.assertEqual(tool_event["full_output"], "whole output")

    def test_one_log_line_per_event(self):
        self.submits = 1
        self.run_loop(FakeModel(reply(call("echo", {"command": "pytest\n -x"})), reply(call("submit", {}))))
        self.assertIn("[第 1 步] echo", self.logs[1])
        self.assertIn("退出码 0：", self.logs[1])
        self.assertTrue(self.logs[1].endswith("退出码 0：command=pytest -x"))
        self.assertTrue(self.logs[-1].startswith("结束：submitted"))


if __name__ == "__main__":
    unittest.main()
