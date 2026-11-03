"""agent 手里的工具。

一共四个（2026-10-25 定）：bash、str_replace_editor、read_issue_ref、submit。不设停止工具（2026-10-27 定）：
agent 做不了时不改代码直接交卷，在汇报里写明为什么没改，外层程序看到补丁为空就只留言、不开 PR。

每个工具对主循环只交出三样东西：名字、发给模型看的参数说明（JSON Schema）、
一个"收到参数 → 返回 ToolResult"的函数。主循环收到模型的工具调用后，统一交给 call_tool。

出错分两种：
- 模型能自己改的（参数不对、没有这个工具、命令超时）：写成文字交回模型，ToolResult.error 为 True，
  主循环据此把这一步在运行记录和 Langfuse 里标成出错。
- 环境坏了（仓库目录不在、bash 不存在）：照常抛异常，主循环按"出错"结束这次运行，
  因为模型改不了环境，交给它只会白白耗步数。
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolResult:
    text: str  # 交给模型的文字
    error: bool = False  # 工具没能按要求做完（参数不对、没有这个工具、超时被杀）
    # 给运行记录和 Langfuse 用的附加信息，不发给模型，例如 bash 的退出码、是否超时、是否截断
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[dict], ToolResult]

    def spec(self) -> dict:
        """发给 DeepSeek 的工具定义，格式同 OpenAI 的工具调用（api-docs.deepseek.com/guides/tool_calls）。"""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def call_tool(tools: dict[str, Tool], name: str, arguments: str) -> ToolResult:
    """执行模型的一次工具调用。arguments 是模型给的参数原文：DeepSeek 返回的是一段 JSON 文字，不是字典。"""
    tool = tools.get(name)
    if tool is None:
        return ToolResult(f"Error: there is no tool named {name!r}. Available tools: {', '.join(tools)}.", error=True)
    try:
        # 没有参数的调用，模型可能给空字符串
        args = json.loads(arguments) if arguments.strip() else {}
    except json.JSONDecodeError as exc:
        return ToolResult(f"Error: the arguments for {name} are not valid JSON ({exc}).", error=True)
    if not isinstance(args, dict):
        return ToolResult(f"Error: the arguments for {name} must be a JSON object.", error=True)
    return tool.run(args)
