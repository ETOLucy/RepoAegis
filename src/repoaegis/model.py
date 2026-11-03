"""调用 DeepSeek：把整段对话和工具定义发过去，把回答一段段收回来，拼成一条 assistant 消息交给主循环。

主循环（repoaegis.loop）每走一步调用一次 DeepSeek 对象，交来对话、工具定义、这次运行还剩多少秒，
拿回 ModelReply，失败时收到 ModelError。只用标准库。规则（2026-11-02 定，依据见 docs/调研/调用DeepSeek.md）：

- 地址和钥匙从 OPENAI_BASE_URL、OPENAI_API_KEY 读。在 Actions 上这两个由 AWF 设好：地址是它的密钥代理，
  钥匙是占位值，真钥匙只在代理里。本机测试时指向 https://api.deepseek.com 和真钥匙。不走 HTTP 代理。
- 默认 deepseek-flash、开思考、强度 high，都写明在请求里；不设 max_tokens，不传温度（思考模式下不起作用）。
- 用流式接收：两段内容之间超过 IDLE_SECONDS 没有动静就算卡住；主循环给的剩余时间用完也停。
  DeepSeek 排队时会发 ": keep-alive"，也算有动静。
- 临时故障重试：连不上、读超时、流中途断开、408、409、429、500、502、503、504，以及结束原因是
  insufficient_system_resource、aborted 的。最多再试 RETRIES 次，间隔 8 秒起翻倍到 64 秒；
  服务端给了 Retry-After 就按它等，但要求超过 64 秒就不等了。剩下的时间不够等就不再试。
  其余错误（400、401、402、422 等）不重试。回答中途报的错也先分类：余额、钥匙、格式、内容被拦这类直接失败，
  其余重问（2026-11-03 照 docs/调研/流式接收的做法.md 加，同时工具调用的编号和名字改为不拼接）。
- 思考内容必须原样回传（请求带工具时漏了就是 400），所以拼出来的消息带着 reasoning_content，
  主循环原样追加进对话即可。
- 用量原样交回，日志和 Langfuse 都按它显示 token；不估算钱。回答收得久时每 PROGRESS_SECONDS 打一行进度。
"""

import http.client
import json
import os
import socket
import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime, timezone

from repoaegis.loop import ModelError, ModelReply

# 暂定的默认值，以后按实测调整（理由见模块说明里的报告）
MODEL = "deepseek-flash"
REASONING_EFFORT = "high"
DEFAULT_BASE_URL = "https://api.deepseek.com"
CONNECT_SECONDS = 30
IDLE_SECONDS = 5 * 60  # 照 OpenHands、Codex 的 300 秒
RETRIES = 5
RETRY_FIRST_WAIT = 8  # 照 OpenHands：8、16、32、64、64 秒
RETRY_MAX_WAIT = 64
PROGRESS_SECONDS = 30

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
RETRY_FINISH = {"insufficient_system_resource", "aborted"}
STATUS_MEANINGS = {
    400: "请求格式不对", 401: "钥匙不对", 402: "账户余额不足", 404: "地址不对",
    408: "请求超时", 409: "请求冲突", 413: "请求太大", 422: "参数不对", 429: "请求太快或超过并发上限",
    500: "服务器出错", 502: "代理连不上模型服务", 503: "服务器过载", 504: "代理等模型服务超时",
}
# 对话超长时 DeepSeek 回 400，错误信息里会提到上下文长度。具体措辞没查到，按这几个词认【待验证】
CONTEXT_WORDS = ("context length", "context_length", "maximum context", "too long")
# 回答中途报的错里出现这些，重问也没用（余额、钥匙、格式、内容被拦），直接按失败结束。
# DeepSeek 中途报错的格式没查到，按错误码和关键词认【待验证】
FATAL_CODES = {"400", "401", "402", "422"}
FATAL_WORDS = ("insufficient balance", "insufficient_balance", "authentication", "api key", "invalid_request",
               "content_filter", "content filter", "content risk")
ERROR_BODY_BYTES = 64 * 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Retry(Exception):
    """这次尝试遇到临时故障，可以再试。wait 是服务端要求等的秒数，没给就是 None。"""

    def __init__(self, message: str, wait: float | None = None):
        super().__init__(message)
        self.wait = wait


class _Deadline(Exception):
    """主循环给的剩余时间用完了。"""


class DeepSeek:
    def __init__(self, base_url: str, api_key: str, model: str = MODEL, effort: str = REASONING_EFFORT,
                 log: Callable[[str], None] | None = None, idle_seconds: float = IDLE_SECONDS,
                 retries: int = RETRIES, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        url = urllib.parse.urlsplit(base_url.strip().rstrip("/"))
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError(f"模型服务地址不对：{base_url!r}")
        if not api_key.strip():
            raise ValueError("没有模型服务的钥匙")
        self.url = url
        # 钥匙两头的空白去掉：从 Windows 写的 .env 读出来会带 \r
        self.api_key = api_key.strip()
        self.model = model
        self.effort = effort
        self.log = log or (lambda text: print(text, flush=True))
        self.idle_seconds = idle_seconds
        self.retries = retries
        self.sleep = sleep
        self.clock = clock

    @classmethod
    def from_env(cls, log: Callable[[str], None] | None = None) -> "DeepSeek":
        return cls(os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL, os.environ.get("OPENAI_API_KEY", ""),
                   model=os.environ.get("REPOAEGIS_MODEL") or MODEL, log=log)

    def parameters(self) -> dict:
        """写进轨迹 run_start 的 model_parameters：请求里除了对话和工具以外的设置。"""
        return {"thinking": {"type": "enabled"}, "reasoning_effort": self.effort}

    def __call__(self, messages: list[dict], tools: list[dict], seconds: float) -> ModelReply:
        deadline = self.clock() + seconds
        body = {"model": self.model, "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}, **self.parameters()}
        if tools:
            body["tools"] = tools
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        retries = 0
        while True:
            try:
                return self._attempt(data, deadline, retries)
            except ModelError as exc:
                exc.retries = retries
                raise
            except _Deadline as exc:
                raise ModelError("timeout", str(exc), retries) from None
            except _Retry as exc:
                if retries >= self.retries:
                    raise ModelError("failed", f"{exc}（已重试 {retries} 次）", retries) from None
                # 服务端要求等得比上限还久，就不等了（照 OpenHands，2026-11-03 加）
                if exc.wait is not None and exc.wait > RETRY_MAX_WAIT:
                    raise ModelError("failed", f"{exc}；服务端要求等 {exc.wait:g} 秒，超过上限 {RETRY_MAX_WAIT} 秒，不再重试",
                                     retries) from None
                wait = exc.wait if exc.wait is not None else min(RETRY_FIRST_WAIT * 2 ** retries, RETRY_MAX_WAIT)
                left = deadline - self.clock()
                if wait >= left:
                    raise ModelError("timeout", f"{exc}；要等 {wait:g} 秒再试，但这次运行只剩 {max(left, 0):.0f} 秒",
                                     retries) from None
                retries += 1
                self.log(f"[模型] {exc}，{wait:g} 秒后重试（第 {retries}/{self.retries} 次）")
                self.sleep(wait)

    def _attempt(self, data: bytes, deadline: float, retries: int) -> ModelReply:
        if self.clock() >= deadline:
            raise _Deadline("这次运行的剩余时间用完了")
        connection_class = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self.url.hostname, self.url.port,
                                      timeout=min(CONNECT_SECONDS, max(deadline - self.clock(), 0.1)))
        try:
            try:
                connection.request("POST", self.url.path + "/chat/completions", body=data, headers={
                    "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                    "Accept": "text/event-stream"})
                self._wait_limit(connection, deadline)
                response = connection.getresponse()
            except (TimeoutError, socket.timeout):
                self._check_deadline(deadline)
                raise _Retry("连接模型服务超时") from None
            except (OSError, http.client.HTTPException) as exc:
                raise _Retry(f"连不上模型服务（{type(exc).__name__}: {exc}）") from None
            try:
                if response.status != 200:
                    self._fail(response)
                return self._read_stream(connection, response, deadline, retries)
            finally:
                response.close()
        finally:
            connection.close()

    def _wait_limit(self, connection, deadline: float) -> None:
        """之后每次读数据最多等多久：两段之间的上限和剩余时间里较小的那个。"""
        if connection.sock is not None:
            connection.sock.settimeout(max(min(self.idle_seconds, deadline - self.clock()), 0.1))

    def _check_deadline(self, deadline: float) -> None:
        if self.clock() >= deadline:
            raise _Deadline("这次运行的剩余时间用完了，模型的回答没收完")

    def _fail(self, response) -> None:
        """非 200 的回应：能重试的抛 _Retry，其余直接按失败结束。"""
        status = response.status
        text = response.read(ERROR_BODY_BYTES).decode("utf-8", errors="replace")
        try:
            detail = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = text.strip()[:500]
        message = f"HTTP {status} {STATUS_MEANINGS.get(status, '')}".rstrip() + (f"：{detail}" if detail else "")
        if status in RETRY_STATUS:
            raise _Retry(message, _retry_after(response.getheader("Retry-After")))
        if status == 400 and any(word in detail.lower() for word in CONTEXT_WORDS):
            raise ModelError("context_too_long", message)
        raise ModelError("failed", message)

    def _read_stream(self, connection, response, deadline: float, retries: int) -> ModelReply:
        reasoning, content, calls = [], [], {}
        usage, finish_reason, first_token = {}, None, None
        started = last_progress = self.clock()
        done = False
        while not done:
            self._wait_limit(connection, deadline)
            try:
                raw = response.readline()
            except (TimeoutError, socket.timeout):
                self._check_deadline(deadline)
                raise _Retry(f"超过 {self.idle_seconds:g} 秒没有收到模型的任何内容") from None
            except (OSError, http.client.HTTPException) as exc:
                raise _Retry(f"接收回答时连接断了（{type(exc).__name__}: {exc}）") from None
            if not raw:
                raise _Retry("回答没收完连接就关了")
            # 放在解析之前：排队时只收到 keep-alive，也要打进度，日志才不会长时间不动
            if self.clock() - last_progress >= PROGRESS_SECONDS:
                last_progress = self.clock()
                if reasoning or content or calls:
                    self.log(f"[模型] 还在输出，已用 {last_progress - started:.0f} 秒，收到思考 {_chars(reasoning):,} 字、"
                             f"回答 {_chars(content):,} 字、工具调用 {len(calls)} 个")
                else:
                    self.log(f"[模型] 还在排队或准备，已等 {last_progress - started:.0f} 秒")
            line = raw.decode("utf-8", errors="replace").strip()
            # 空行是事件之间的分隔，冒号开头的是注释（排队时的 ": keep-alive"）
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                done = True
                continue
            try:
                chunk = json.loads(payload)
            except ValueError:
                raise _Retry(f"回答里有一段不是合法的 JSON：{payload[:200]}") from None
            if chunk.get("error"):
                _stream_error(chunk["error"])
            if chunk.get("usage"):
                usage = chunk["usage"]
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    reasoning.append(delta["reasoning_content"])
                if delta.get("content"):
                    content.append(delta["content"])
                for call in delta.get("tool_calls") or []:
                    _merge_call(calls, call)
                if first_token is None and (reasoning or content or calls):
                    first_token = now()
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        if finish_reason is None:
            raise _Retry("回答结束了，但没有给出结束原因")
        if finish_reason in RETRY_FINISH:
            raise _Retry(f"模型服务中断了回答（结束原因 {finish_reason}）")
        message = {"role": "assistant", "content": "".join(content) or None}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        return ModelReply(message=message, usage=usage, finish_reason=finish_reason, retries=retries,
                          first_token=first_token)


def _stream_error(error) -> None:
    """回答中途收到报错：重问也没用的直接按失败结束，其余当临时故障重问（照 Codex 先分类，2026-11-03 加）。"""
    text = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
    message = f"模型服务在回答中途报错：{text[:500]}"
    lowered = text.lower()
    if any(word in lowered for word in CONTEXT_WORDS):
        raise ModelError("context_too_long", message)
    code = str(error.get("code", "")) if isinstance(error, dict) else ""
    if code in FATAL_CODES or any(word in lowered for word in FATAL_WORDS):
        raise ModelError("failed", message)
    raise _Retry(message)


def _merge_call(calls: dict[int, dict], piece: dict) -> None:
    """工具调用按 index 分开：第一段带编号、类型和函数名，后面的段只带参数的碎片，接起来。

    编号和函数名取第一次给出的值，不拼接（照 litellm，2026-11-03 改）：服务端哪天每段都重发一次名字，
    也不会拼成 "bashbash"。只有参数是真正的碎片，按到达顺序接起来。
    """
    call = calls.setdefault(piece.get("index", 0),
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
    function = piece.get("function") or {}
    if piece.get("id") and not call["id"]:
        call["id"] = piece["id"]
    if piece.get("type"):
        call["type"] = piece["type"]
    if function.get("name") and not call["function"]["name"]:
        call["function"]["name"] = function["name"]
    call["function"]["arguments"] += function.get("arguments") or ""


def _chars(parts: list[str]) -> int:
    return sum(len(part) for part in parts)


def _retry_after(value: str | None) -> float | None:
    """Retry-After 只认秒数；写成日期的不认，按自己的间隔等。"""
    try:
        return max(float(value), 0.0) if value else None
    except ValueError:
        return None
