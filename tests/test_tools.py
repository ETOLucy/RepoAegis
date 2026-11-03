import unittest

from repoaegis.tools import Tool, ToolResult, call_tool


def _echo(args):
    return ToolResult(f"got {args}")


TOOLS = {"echo": Tool("echo", "Echo the arguments.", {"type": "object"}, _echo),
         "noop": Tool("noop", "Do nothing.", {"type": "object"}, lambda args: ToolResult("done"))}


class CallToolTest(unittest.TestCase):
    def test_passes_parsed_arguments(self):
        result = call_tool(TOOLS, "echo", '{"command": "ls", "timeout": 5}')
        self.assertEqual(result, ToolResult("got {'command': 'ls', 'timeout': 5}"))

    def test_empty_arguments_mean_no_arguments(self):
        self.assertEqual(call_tool(TOOLS, "echo", "").text, "got {}")
        self.assertEqual(call_tool(TOOLS, "echo", "  ").text, "got {}")

    def test_unknown_tool(self):
        result = call_tool(TOOLS, "search", "{}")
        self.assertTrue(result.error)
        self.assertEqual(result.text, "Error: there is no tool named 'search'. Available tools: echo, noop.")

    def test_invalid_json(self):
        result = call_tool(TOOLS, "echo", '{"command": "ls"')
        self.assertTrue(result.error)
        self.assertIn("not valid JSON", result.text)

    def test_arguments_must_be_an_object(self):
        result = call_tool(TOOLS, "echo", '["ls"]')
        self.assertTrue(result.error)
        self.assertIn("must be a JSON object", result.text)

    def test_tool_exceptions_are_not_swallowed(self):
        # 环境坏了由主循环按出错结束，不交给模型
        def broken(args):
            raise FileNotFoundError("repo dir missing")

        with self.assertRaises(FileNotFoundError):
            call_tool({"broken": Tool("broken", "", {"type": "object"}, broken)}, "broken", "{}")

    def test_spec(self):
        self.assertEqual(TOOLS["echo"].spec(), {"type": "function", "function": {
            "name": "echo", "description": "Echo the arguments.", "parameters": {"type": "object"}}})


if __name__ == "__main__":
    unittest.main()
