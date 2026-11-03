"""收尾作业里把一次运行的轨迹文件上传到 Langfuse。

在工作流里用 `python -m repoaegis.langfuse_upload <轨迹文件>` 运行，只用标准库。规则（2026-10-30 定，
依据见 docs/调研/Langfuse记录格式.md）：

- 运行结束后由收尾作业一次性上传，agent 那一步拿不到 Langfuse 密钥。
- 没配 LANGFUSE_PUBLIC_KEY、LANGFUSE_SECRET_KEY 就跳过；上传失败只打警告，不让收尾作业失败。
- 发尽可能多的内容：每次调模型带完整对话，工具带完整输出。凡是代码都配一句人话说明。
- 按 OpenTelemetry 的 JSON 格式直接发到 Langfuse 的接收地址：事后上传要填每一步真实的开始时间，
  langfuse 库的公开接口做不到。属性名照 langfuse 库的 _client/attributes.py。
- 同一份轨迹重复上传是覆盖，不会多出一条：记录和每一步的编号都由运行链接算出来。

密钥遮挡不在这里做：主循环写轨迹文件时就要遮住，因为轨迹文件本身也会作为运行产物公开。

轨迹文件（主循环按这个写）：放在 agent 输出目录（OUTPUT_DIR）下，名叫 trajectory.jsonl，随 agent-output
产物传给收尾作业。每行一个 JSON 对象，type 区分种类，时间都是带时区的 ISO 8601 字符串。
- run_start：time、repo、issue、issue_url、labeled_by、labeled_at、run_url、base_commit、version、model、
  model_parameters、limits（一句话）、messages（开头的系统提示和任务）、tools（发给模型的工具定义）
- model_call：step、start、end、可选 first_token（开始出字的时间）、response（模型回的 assistant 消息，
  含 tool_calls 和 reasoning_content）、usage（接口原样返回的用量）、finish_reason、retries；
  调用失败时没有 response，error 写原因
- tool_call：step、call_id、name、arguments（模型给的参数原文）、start、end、text（交给模型的文字）、
  error、details（ToolResult.details）、可选 full_output（交给模型的被截断时，完整输出）
- note：time、text、可选 level（WARNING 或 ERROR），例如被撤标签暂停、达到上限
- delivery：name、start、end、ok、result（一句话）、可选 url
- run_end：time、outcome（见 OUTCOMES）、reason、report（给人看的结论）
"""

import base64
import gzip
import hashlib
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

TRACE_NAME = "agent-fix"
DEFAULT_BASE_URL = "https://cloud.langfuse.com"  # 和 langfuse 库的默认地址一致（欧盟区）
# 一次请求最多带多少字节（压缩前）。云服务的上限没查到，实测一次 10MB 没问题，留出余量
MAX_REQUEST_BYTES = 8 * 1024 * 1024
RETRIES = 3
TIMEOUT_SECONDS = 60

OUTCOMES = {
    "submitted": "已交卷",
    "no_change": "交卷了但没有改动",
    "paused": "已暂停",
    "timeout": "超时",
    "limit": "达到上限",
    "error": "出错",
    "delivery_failed": "交付失败",
    "unfinished": "轨迹中途结束",
}
OUTCOME_LEVELS = {"error": "ERROR", "delivery_failed": "ERROR", "unfinished": "ERROR",
                  "timeout": "WARNING", "limit": "WARNING", "paused": "WARNING"}

FINISH_REASONS = {
    "stop": "模型自己结束了回答",
    "tool_calls": "模型要调用工具",
    "length": "回答达到长度上限被截断",
    "content_filter": "内容被模型服务过滤",
    "insufficient_system_resource": "模型服务资源不足，回答被中断",
}

# 退出码的意思：先按命令里出现的程序查，再查通用的
PROGRAM_EXIT_CODES = {
    "pytest": {0: "全部测试通过", 1: "有测试没通过", 2: "测试被中断", 3: "pytest 内部出错",
               4: "pytest 用法错误，常见原因是测试文件路径不存在", 5: "没有收集到任何测试"},
    "grep": {0: "找到了匹配的内容", 1: "没有找到匹配的内容", 2: "grep 出错，比如文件不存在"},
    "git": {0: "成功", 1: "git 报告失败或有差异", 128: "git 致命错误，比如不在仓库里或引用不存在"},
}
EXIT_CODES = {0: "成功", 1: "一般性失败", 2: "命令用法错误", 124: "超时被停止", 126: "文件不可执行",
              127: "找不到这个命令"}

DETAIL_LABELS = {
    "exit_code": "退出码", "timed_out": "超时被停止", "truncated": "交给模型的输出被截断",
    "output_path": "完整输出在运行机上的位置", "command": "编辑器命令", "path": "文件",
    "lint_errors": "lint 报错", "reverted": "改动已撤回", "diff": "改动",
    "has_changes": "有改动", "patch_bytes": "补丁大小（字节）", "submitted": "已交卷",
    "patch_path": "补丁文件", "report_path": "汇报文件",
}


def explain_exit(command: str, code: int) -> str:
    # 按命令里出现的程序名猜含义，可能猜错：比如 python -m pytest 在没装 pytest 时也返回 1
    # （2026-10-31 在 Actions 上实测遇到），所以注明"按某某的约定"，并在状态说明里附上输出的最后一行
    for program, codes in PROGRAM_EXIT_CODES.items():
        if re.search(rf"(^|[\s/;&|(]){program}\b", command) and code in codes:
            return f"{codes[code]}（按 {program} 的约定）"
    if code in EXIT_CODES:
        return EXIT_CODES[code]
    if 128 < code < 160:
        try:
            return f"被信号 {signal.Signals(code - 128).name} 结束"
        except ValueError:
            pass
    return "非零退出码，含义取决于具体程序"


def explain_finish(reason: str | None) -> str | None:
    if not reason:
        return None
    return f"{reason}（{FINISH_REASONS.get(reason, '模型服务给的结束原因')}）"


def outcome_label(outcome: str) -> str:
    return OUTCOMES.get(outcome, outcome)


def usage_details(usage: dict | None) -> dict | None:
    """接口原样返回的用量 → Langfuse 的四项：未命中缓存的输入、命中缓存的输入、输出、思考。

    命中缓存的数先看 OpenAI 格式的 prompt_tokens_details，再看 DeepSeek 的 prompt_cache_hit_tokens。
    """
    if not usage:
        return None
    prompt = usage.get("prompt_tokens") or 0
    completion = usage.get("completion_tokens") or 0
    cached = ((usage.get("prompt_tokens_details") or {}).get("cached_tokens")
              or usage.get("prompt_cache_hit_tokens") or 0)
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    return {"input": prompt - cached, "input_cached_tokens": cached,
            "output": completion - reasoning, "output_reasoning_tokens": reasoning}


def read_trajectory(path: str) -> tuple[list[dict], int]:
    """读轨迹文件。运行被强制结束时最后一行可能只写了一半，读不了的行跳过并计数。"""
    events, bad = [], 0
    with open(path, encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    return events, bad


def _ns(stamp: str) -> int:
    return int(datetime.fromisoformat(stamp).timestamp() * 1_000_000_000)


def _hex(seed: str, length: int) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[:length]


def _json(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _last_line(output: str, limit: int = 200) -> str:
    """输出里最后一个非空行，太长就截断；常常就是出错的直接原因。"""
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    return lines[-1] if len(lines[-1]) <= limit else lines[-1][:limit] + "……"


def _detail_value(key: str, value, details: dict, command: str):
    if key == "exit_code":
        if value is None:
            return "无（超时被停止）" if details.get("timed_out") else "无"
        return f"{value}（{explain_exit(command, value)}）"
    if isinstance(value, bool):
        return "是" if value else "否"
    return value


class _Builder:
    """把轨迹事件换成 OpenTelemetry 的 span（JSON 格式）。"""

    def __init__(self, trace_id: str, trace_attrs: dict):
        self.trace_id = trace_id
        self.trace_attrs = trace_attrs
        self.spans = []

    def add(self, name: str, kind: str, start: str, end: str, parent: str | None, *, input=None, output=None,
            metadata: dict | None = None, level: str | None = None, status: str | None = None,
            extra: dict | None = None) -> str:
        span_id = _hex(f"{self.trace_id}:{len(self.spans)}", 16)
        attrs = {**self.trace_attrs, "langfuse.observation.type": kind, **(extra or {})}
        if input is not None:
            attrs["langfuse.observation.input"] = _json(input)
        if output is not None:
            attrs["langfuse.observation.output"] = _json(output)
        if level:
            attrs["langfuse.observation.level"] = level
        if status:
            attrs["langfuse.observation.status_message"] = status
        for key, value in (metadata or {}).items():
            if value is not None:
                attrs[f"langfuse.observation.metadata.{key}"] = json.dumps(value, ensure_ascii=False)
        span = {"traceId": self.trace_id, "spanId": span_id, "name": name, "kind": 1,
                "startTimeUnixNano": str(_ns(start)), "endTimeUnixNano": str(_ns(end)),
                "attributes": [_attribute(k, v) for k, v in attrs.items()]}
        if parent:
            span["parentSpanId"] = parent
        self.spans.append(span)
        return span_id


def _attribute(key: str, value) -> dict:
    if isinstance(value, list):
        return {"key": key, "value": {"arrayValue": {"values": [{"stringValue": str(v)} for v in value]}}}
    return {"key": key, "value": {"stringValue": str(value)}}


def build(events: list[dict], environment: str | None = None) -> tuple[str, list[dict], list[dict]]:
    """轨迹事件 → (记录编号, span 列表, 分数列表)。不联网，方便测试。"""
    start = next((e for e in events if e.get("type") == "run_start"), None)
    if start is None:
        raise ValueError("轨迹里没有 run_start，没法上传")
    end = next((e for e in reversed(events) if e.get("type") == "run_end"), None)
    times = [e.get("end") or e.get("time") for e in events if e.get("end") or e.get("time")]
    last_time = max(times, key=_ns)
    if end is None:
        end = {"time": last_time, "outcome": "unfinished",
               "reason": "轨迹在中途结束，运行可能被取消或被强制结束", "report": ""}

    trace_id = _hex(f"repoaegis:{start.get('run_url') or json.dumps(start, sort_keys=True)}", 32)
    outcome = end.get("outcome", "unfinished")
    label = outcome_label(outcome)
    trace_attrs = {
        "langfuse.trace.name": TRACE_NAME,
        "session.id": f"{start.get('repo')}#{start.get('issue')} 打标签于 {start.get('labeled_at')}",
        "langfuse.trace.tags": [TRACE_NAME, f"结局：{label}"],
    }
    if start.get("labeled_by"):
        trace_attrs["user.id"] = start["labeled_by"]
    if start.get("version"):
        trace_attrs["langfuse.release"] = start["version"]
    if environment:
        trace_attrs["langfuse.environment"] = environment
    builder = _Builder(trace_id, trace_attrs)

    model_calls = [e for e in events if e.get("type") == "model_call"]
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    tool_errors = sum(1 for e in tool_calls if e.get("error"))
    usages = [usage_details(e.get("usage")) or {} for e in model_calls]
    total_tokens = sum(sum(u.values()) for u in usages)
    seconds = (_ns(end["time"]) - _ns(start["time"])) / 1e9

    messages = list(start.get("messages") or [])
    task = next((m.get("content") for m in messages if m.get("role") == "user"), None)
    summary = (f"结局：{label}。{end.get('reason') or ''}\n\n{end.get('report') or ''}\n\n"
               f"共调用模型 {len(model_calls)} 次、工具 {len(tool_calls)} 次（其中 {tool_errors} 次出错），"
               f"token 合计 {total_tokens}，用时 {seconds:.0f} 秒。").strip()
    root = builder.add(
        TRACE_NAME, "agent", start["time"], end["time"], None,
        input=[{"role": "user", "content": task}] if task else None,
        output={"role": "assistant", "content": summary},
        level=OUTCOME_LEVELS.get(outcome),
        status=f"{label}：{end.get('reason') or '没有说明'}" if outcome in OUTCOME_LEVELS else None,
        extra={f"langfuse.trace.metadata.{k}": json.dumps(v, ensure_ascii=False) for k, v in {
            "仓库": start.get("repo"), "issue": start.get("issue_url"), "打标签的人": start.get("labeled_by"),
            "打标签时间": start.get("labeled_at"), "Actions 运行": start.get("run_url"),
            "起点提交": start.get("base_commit"), "RepoAegis 版本": start.get("version"),
            "模型": start.get("model"), "上限": start.get("limits"),
            "结局": f"{outcome}（{label}）", "结局说明": end.get("reason"),
        }.items() if v is not None},
    )

    deliveries = []
    for event in events:
        kind = event.get("type")
        if kind == "model_call":
            response = event.get("response")
            extra = {}
            if start.get("model"):
                extra["langfuse.observation.model.name"] = start["model"]
            if start.get("model_parameters"):
                extra["langfuse.observation.model.parameters"] = _json(start["model_parameters"])
            if usage_details(event.get("usage")):
                extra["langfuse.observation.usage_details"] = _json(usage_details(event.get("usage")))
            if event.get("first_token"):
                extra["langfuse.observation.completion_start_time"] = _json(event["first_token"])
            builder.add(
                "call-model", "generation", event["start"], event["end"], root,
                input={"messages": messages, "tools": start.get("tools") or []}, output=response,
                metadata={"第几步": event.get("step"), "结束原因": explain_finish(event.get("finish_reason")),
                          "重试次数": event.get("retries")},
                level="ERROR" if event.get("error") else None,
                status=f"调用模型失败：{event['error']}" if event.get("error") else None, extra=extra,
            )
            if response:
                messages = messages + [response]
        elif kind == "tool_call":
            details = event.get("details") or {}
            raw = event.get("arguments") or ""
            try:
                arguments = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                arguments = raw
            command = arguments.get("command", "") if isinstance(arguments, dict) else ""
            command = command if isinstance(command, str) else ""
            seconds_used = (_ns(event["end"]) - _ns(event["start"])) / 1e9
            metadata = {"第几步": event.get("step"), "调用编号": event.get("call_id"), "用时": f"{seconds_used:.1f} 秒"}
            for key, value in details.items():
                metadata[DETAIL_LABELS.get(key, key)] = _detail_value(key, value, details, command)
            if event.get("full_output") is not None:
                metadata["说明"] = "交给模型的输出被截断了，这里记的是完整输出"
            level = status = None
            code = details.get("exit_code")
            if details.get("timed_out"):
                level, status = "ERROR", "命令超时，被强制停止"
            elif event.get("error"):
                level = "ERROR"
                first_line = (event.get("text") or "").strip().splitlines()[:1]
                status = "工具没能按要求做完：" + (first_line[0] if first_line else "没有说明")
            elif isinstance(code, int) and code != 0:
                level, status = "WARNING", f"命令以退出码 {code} 结束：{explain_exit(command, code)}"
                last_line = _last_line(event.get("full_output") or event.get("text") or "")
                if last_line:
                    status += f"。输出最后一行：{last_line}"
            builder.add(event.get("name") or "tool", "tool", event["start"], event["end"], root,
                        input=arguments, output=event.get("full_output") or event.get("text") or "",
                        metadata=metadata, level=level, status=status)
            messages = messages + [{"role": "tool", "tool_call_id": event.get("call_id"),
                                    "content": event.get("text") or ""}]
        elif kind == "note":
            builder.add("note", "event", event["time"], event["time"], root, input=event.get("text"),
                        level=event.get("level"), status=event.get("text") if event.get("level") else None)
        elif kind == "delivery":
            deliveries.append(event)

    if deliveries:
        failed = [d for d in deliveries if not d.get("ok")]
        deliver = builder.add(
            "deliver", "chain", min((d["start"] for d in deliveries), key=_ns),
            max((d["end"] for d in deliveries), key=_ns), root,
            output="；".join(d.get("result", "") for d in deliveries),
            level="ERROR" if failed else None,
            status=f"交付失败：{failed[0].get('result')}" if failed else None)
        for d in deliveries:
            builder.add(d.get("name") or "deliver-step", "span", d["start"], d["end"], deliver,
                        output=f"{d.get('result', '')}" + (f"\n{d['url']}" if d.get("url") else ""),
                        metadata={"链接": d.get("url")}, level=None if d.get("ok") else "ERROR",
                        status=None if d.get("ok") else d.get("result"))

    scores = [
        {"name": "结局", "value": label, "dataType": "CATEGORICAL", "comment": end.get("reason") or None},
        {"name": "调用模型次数", "value": len(model_calls), "dataType": "NUMERIC",
         "comment": "每调用一次模型算一次，调用失败的也算"},
        {"name": "工具出错次数", "value": tool_errors, "dataType": "NUMERIC",
         "comment": "工具没能按要求做完的次数（超时、参数不对等），不含退出码非 0"},
    ]
    for score in scores:
        score["traceId"] = trace_id
        score["id"] = _hex(f"{trace_id}:score:{score['name']}", 32)
    return trace_id, builder.spans, scores


def batches(spans: list[dict], limit: int = MAX_REQUEST_BYTES) -> list[list[dict]]:
    """按大小分批，每批压缩前不超过 limit；单个 span 超过 limit 就单独一批。"""
    result, current, size = [], [], 0
    for span in spans:
        span_size = len(json.dumps(span, ensure_ascii=False).encode())
        if current and size + span_size > limit:
            result.append(current)
            current, size = [], 0
        current.append(span)
        size += span_size
    if current:
        result.append(current)
    return result


class Langfuse:
    def __init__(self, base_url: str, public_key: str, secret_key: str):
        self.base_url = base_url.rstrip("/")
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.auth = f"Basic {token}"

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> dict:
        """发一次请求。连不上、429、5xx 重试，最多 RETRIES 次；其余错误直接抛出。"""
        headers = {"Authorization": self.auth, **(headers or {})}
        for attempt in range(1, RETRIES + 1):
            request = urllib.request.Request(self.base_url + path, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    text = response.read().decode() or "{}"
                    return json.loads(text)
            except urllib.error.HTTPError as exc:
                with exc:
                    detail = exc.read().decode(errors="replace")[:300]
                if exc.code != 429 and exc.code < 500 or attempt == RETRIES:
                    raise RuntimeError(f"{method} {path} 返回 {exc.code}：{detail}") from exc
            except urllib.error.URLError as exc:
                if attempt == RETRIES:
                    raise RuntimeError(f"{method} {path} 连不上：{exc.reason}") from exc
            time.sleep(2 ** attempt)
        raise AssertionError("不会走到这里")

    def send_spans(self, spans: list[dict]) -> None:
        body = {"resourceSpans": [{"resource": {"attributes": [_attribute("service.name", "repoaegis")]},
                                   "scopeSpans": [{"scope": {"name": "repoaegis"}, "spans": spans}]}]}
        self.request("POST", "/api/public/otel/v1/traces",
                     gzip.compress(json.dumps(body, ensure_ascii=False).encode()),
                     {"Content-Type": "application/json", "Content-Encoding": "gzip"})

    def send_score(self, score: dict) -> None:
        body = {k: v for k, v in score.items() if v is not None}
        self.request("POST", "/api/public/scores", json.dumps(body, ensure_ascii=False).encode(),
                     {"Content-Type": "application/json"})

    def trace_url(self, trace_id: str) -> str | None:
        """网页地址要用项目编号拼，项目编号要查一次接口；查不到就不给地址。"""
        try:
            projects = self.request("GET", "/api/public/projects").get("data") or []
            return f"{self.base_url}/project/{projects[0]['id']}/traces/{trace_id}" if projects else None
        except (RuntimeError, ValueError, KeyError, TypeError):
            return None


def upload(path: str, env=os.environ, out=sys.stdout) -> int:
    """上传一份轨迹。总是返回 0：监控是可选的，出问题只打警告，不让收尾作业失败。"""
    # 去掉首尾空白：复制粘贴密钥时常带进换行或回车（2026-10-30 实测就遇到过），服务端会当成密钥不对
    public_key = (env.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret_key = (env.get("LANGFUSE_SECRET_KEY") or "").strip()
    if not public_key or not secret_key:
        print("没有配置 Langfuse 密钥（LANGFUSE_PUBLIC_KEY、LANGFUSE_SECRET_KEY），跳过上传。", file=out)
        return 0
    base_url = (env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or DEFAULT_BASE_URL).strip()
    try:
        events, bad = read_trajectory(path)
        if bad:
            print(f"::warning::轨迹文件里有 {bad} 行读不了（可能是运行被强制结束时写了一半），已跳过。", file=out)
        trace_id, spans, scores = build(events, env.get("LANGFUSE_TRACING_ENVIRONMENT"))
        client = Langfuse(base_url, public_key, secret_key)
        groups = batches(spans)
        for group in groups:
            client.send_spans(group)
        for score in scores:
            client.send_score(score)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"::warning::上传 Langfuse 失败，这次运行的记录不会出现在 Langfuse 里：{exc}", file=out)
        return 0
    url = client.trace_url(trace_id)
    print(f"已上传到 Langfuse：{len(spans)} 步、{len(scores)} 个分数，分 {len(groups)} 次发送。"
          f"{url or '记录编号 ' + trace_id}", file=out)
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("用法：python -m repoaegis.langfuse_upload <轨迹文件>")
    sys.exit(upload(sys.argv[1]))


if __name__ == "__main__":
    main()
