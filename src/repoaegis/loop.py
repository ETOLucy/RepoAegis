"""agent 主循环：把对话交给模型，执行模型要的工具，结果交回模型，直到交卷或到上限。

照 mini-swe-agent 的设计自己写（2026-10-25 定）。怎么结束、设哪些上限（2026-10-31 定，见
docs/agent怎么干活.md 第一节）：

- agent 交卷（submit 返回 details["submitted"]）就正常结束；交上的补丁是空的算"交卷了但没有改动"。
- 回复里没有工具调用就打回去提醒，连续 3 次按出错结束：DeepSeek 不能强制模型调用工具。
- 用时累计 60 分钟、token 累计到上限就停。上限按 token 总数算、往宽了设是 10-31 定的；
  默认 2000 万是暂填的，要在测试仓库实测一次运行用多少再定。每次调模型之前检查，所以最后一次回复要的工具照样执行完，
  刚好在这一步交卷算交上（照 OpenHands）。暂停后接着做时，之前用掉的时间由 seconds_used 传进来。
- token 按账单算：每次调用的输入（含命中缓存的）加输出（含思考的），不按钱算。
- 不设步数上限，不做卡死检测，不做遮蔽和摘要。
- 模型调用失败（临时错误由模型那边自己重试，重试完还失败）或对话超长，按出错结束。
- 工具抛异常说明环境坏了，按出错结束（见 repoaegis.tools）。

每一步写进轨迹文件（格式见 repoaegis.langfuse_upload 的说明），同时往标准输出打一行，
在 Actions 页面上能实时看到。轨迹的第一行 run_start 由调用方写，它知道仓库、issue 这些信息。
"""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from repoaegis.tools import Tool, call_tool

DEFAULT_SECONDS = 60 * 60
DEFAULT_TOKENS = 20_000_000  # 暂填，实测后再定
NO_TOOL_REPLIES = 3
# 交给模型的输出被截断时，轨迹里记完整输出；超过这么大就不记了。实测 Langfuse 云服务一个字段 2MB 能收下
FULL_OUTPUT_BYTES = 2 * 1024 * 1024

NO_TOOL_MESSAGE = ("Your last reply did not call any tool. Every reply must call exactly the tools you need next. "
                   "When the work is done, or you decide not to change anything, call `submit`.")


@dataclass(frozen=True)
class ModelReply:
    """模型的一次回复。由调模型的那部分代码填好交给循环。"""
    message: dict  # 接口返回的 assistant 消息原样：content、reasoning_content、tool_calls
    usage: dict  # 接口原样返回的用量
    finish_reason: str | None = None
    retries: int = 0  # 临时错误重试了几次
    first_token: str | None = None  # 开始出字的时间，流式调用才有


class ModelError(Exception):
    """调模型失败，循环按出错结束。kind 区分原因：context_too_long（对话超长）、failed（其余）。"""

    def __init__(self, kind: str, message: str, retries: int = 0):
        super().__init__(message)
        self.kind = kind
        self.retries = retries


# 调模型：收到整段对话、工具定义、最多还能用多少秒，返回一次回复，失败抛 ModelError
Model = Callable[[list[dict], list[dict], float], ModelReply]


@dataclass(frozen=True)
class Limits:
    seconds: float = DEFAULT_SECONDS
    tokens: int = DEFAULT_TOKENS

    def describe(self) -> str:
        """写进轨迹 run_start 的一句话。"""
        return (f"累计用时 {self.seconds / 60:g} 分钟，累计 {self.tokens:,} token，"
                f"连续 {NO_TOOL_REPLIES} 次不调用工具按出错结束")


@dataclass(frozen=True)
class Outcome:
    outcome: str  # repoaegis.langfuse_upload.OUTCOMES 里的一种
    reason: str


class Trajectory:
    """轨迹文件：每行一个 JSON。每写一行就刷到磁盘，进程被强杀时前面的记录还在。"""

    def __init__(self, path: str):
        self.path = path

    def write(self, event: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _State:
    started: float  # 本次运行开始时 clock() 的值
    seconds_used: float  # 之前几次运行用掉的时间
    tokens: int = 0
    no_tool_replies: int = 0
    step: int = 0
    outcome: Outcome | None = None


def run(model: Model, tools: dict[str, Tool], messages: list[dict], trajectory: Trajectory,
        limits: Limits = Limits(), seconds_used: float = 0.0,
        clock: Callable[[], float] = time.monotonic, log: Callable[[str], None] = print) -> Outcome:
    """跑到结束，返回结局。messages 是开头的系统提示和任务，循环往里追加，调用方可以拿来存档。"""
    specs = [tool.spec() for tool in tools.values()]
    state = _State(started=clock(), seconds_used=seconds_used)
    try:
        while state.outcome is None:
            state.outcome = _step(model, tools, specs, messages, trajectory, limits, state, clock, log)
    except Exception as exc:  # 循环自己的毛病也要留下结局，收尾作业才知道发生了什么
        state.outcome = Outcome("error", f"主循环出错：{type(exc).__name__}: {exc}")
    trajectory.write({"type": "run_end", "time": now(), "outcome": state.outcome.outcome,
                      "reason": state.outcome.reason, "report": ""})
    log(f"结束：{state.outcome.outcome}，{state.outcome.reason}")
    return state.outcome


def _step(model, tools, specs, messages, trajectory, limits, state, clock, log) -> Outcome | None:
    """走一步：检查上限 → 调模型 → 执行工具。返回结局表示结束，None 表示继续。"""
    elapsed = state.seconds_used + clock() - state.started
    if elapsed >= limits.seconds:
        return _stop(trajectory, log, "timeout", f"累计用时 {elapsed / 60:.1f} 分钟，达到 {limits.seconds / 60:g} 分钟上限")
    if state.tokens >= limits.tokens:
        return _stop(trajectory, log, "limit", f"累计用了 {state.tokens:,} token，达到 {limits.tokens:,} 上限")

    state.step += 1
    start, tick = now(), clock()
    try:
        reply = model(messages, specs, limits.seconds - elapsed)
    except ModelError as exc:
        trajectory.write({"type": "model_call", "step": state.step, "start": start, "end": now(),
                          "retries": exc.retries, "error": f"{exc.kind}: {exc}"})
        if exc.kind == "context_too_long":
            return Outcome("error", f"对话超过了模型能接收的长度：{exc}")
        return Outcome("error", f"调用模型失败（重试 {exc.retries} 次后）：{exc}")
    event = {"type": "model_call", "step": state.step, "start": start, "end": now(), "response": reply.message,
             "usage": reply.usage, "finish_reason": reply.finish_reason, "retries": reply.retries}
    if reply.first_token:
        event["first_token"] = reply.first_token
    trajectory.write(event)
    state.tokens += (reply.usage.get("prompt_tokens") or 0) + (reply.usage.get("completion_tokens") or 0)
    messages.append(reply.message)
    calls = reply.message.get("tool_calls") or []
    log(f"[第 {state.step} 步] 调模型 {clock() - tick:.1f} 秒，{len(calls)} 个工具调用，累计 {state.tokens:,} token")

    if not calls:
        state.no_tool_replies += 1
        if state.no_tool_replies >= NO_TOOL_REPLIES:
            return Outcome("error", f"模型连续 {NO_TOOL_REPLIES} 次回复都没有调用工具")
        messages.append({"role": "user", "content": NO_TOOL_MESSAGE})
        _note(trajectory, log, f"第 {state.step} 步的回复没有调用工具，已提醒（连续第 {state.no_tool_replies} 次）",
              "WARNING")
        return None
    state.no_tool_replies = 0

    for index, call in enumerate(calls):
        function = call.get("function") or {}
        name, arguments = function.get("name") or "", function.get("arguments") or ""
        start, tick = now(), clock()
        try:
            result = call_tool(tools, name, arguments)
        except Exception as exc:
            trajectory.write({"type": "tool_call", "step": state.step, "call_id": call.get("id"), "name": name,
                              "arguments": arguments, "start": start, "end": now(),
                              "text": f"{type(exc).__name__}: {exc}", "error": True, "details": {}})
            return Outcome("error", f"工具 {name} 执行时环境出错：{type(exc).__name__}: {exc}")
        event = {"type": "tool_call", "step": state.step, "call_id": call.get("id"), "name": name,
                 "arguments": arguments, "start": start, "end": now(), "text": result.text,
                 "error": result.error, "details": result.details}
        full = _full_output(result.details)
        if full is not None:
            event["full_output"] = full
        trajectory.write(event)
        messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": result.text})
        log(f"[第 {state.step} 步] {name} {clock() - tick:.1f} 秒{_status(result)}：{_brief(arguments)}")

        if result.details.get("submitted"):
            skipped = len(calls) - index - 1
            if skipped:
                _note(trajectory, log, f"已交卷，同一回复里后面的 {skipped} 个工具调用没有执行")
            if not result.details.get("has_changes"):
                return Outcome("no_change", "agent 交卷了，没有改动代码")
            return Outcome("submitted", "agent 交卷了")
    return None


def _stop(trajectory, log, outcome: str, reason: str) -> Outcome:
    _note(trajectory, log, reason, "WARNING")
    return Outcome(outcome, reason)


def _note(trajectory, log, text: str, level: str | None = None) -> None:
    event = {"type": "note", "time": now(), "text": text}
    if level:
        event["level"] = level
    trajectory.write(event)
    log(text)


def _full_output(details: dict) -> str | None:
    """bash 的输出被截断时，完整输出留在 output_path 指的文件里；不太大就读出来记进轨迹。"""
    path = details.get("output_path")
    if not path or not os.path.isfile(path) or os.path.getsize(path) > FULL_OUTPUT_BYTES:
        return None
    with open(path, "rb") as file:
        return file.read().decode("utf-8", errors="replace")


def _status(result) -> str:
    if result.details.get("timed_out"):
        return "，超时被停止"
    if result.error:
        return "，出错"
    code = result.details.get("exit_code")
    return f"，退出码 {code}" if code is not None else ""


def _brief(arguments: str, limit: int = 100) -> str:
    """日志里给参数的前一段：能解析就写成"名字=值"，换行压成空格。"""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        arguments = " ".join(f"{key}={value}" for key, value in parsed.items())
    text = " ".join(arguments.split())
    return text if len(text) <= limit else text[:limit] + "…"
