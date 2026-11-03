"""bash 工具：执行一条命令，返回退出码和输出。

规则（2026-10-26、2026-10-27 定）：

- 每条命令单独开一个 bash 进程，在仓库根目录执行；cd、export 不会留到下一条（照 mini-swe-agent）。
- 不设命令黑白名单，安全靠沙箱和发布前的产出检查。
- 命令只看到白名单里的环境变量，不继承主程序的全部环境（2026-10-27 定，见 repoaegis.sandbox.env）。
- 超时默认 120 秒，agent 可以传 timeout 调大，最多 600 秒（照 Claude Code 的 Bash 工具）。
- 命令结束或超时后，把它启动的进程（同一进程组）一起杀掉，不让后台进程留到下一条命令。
- 输出超过 1 万字符时只给开头 5000、结尾 5000（照 mini-swe-agent），
  全文留在文件里，把路径告诉模型，要细看再用 grep、sed 去读（照 OpenHands）。

标准输出和标准错误合在一起，直接写进文件而不是管道：命令留下的后台进程即使还开着输出，
也不会让这里卡住；输出再多也不会全部读进内存。
"""

import itertools
import os
import signal
import subprocess

from repoaegis.sandbox.env import command_env
from repoaegis.tools import Tool, ToolResult

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
OUTPUT_LIMIT = 10_000  # 字符
HEAD_CHARS = TAIL_CHARS = 5_000
# 输出文件不超过这么大就整个读进来按字符截；更大的只读开头和结尾两段字节
READ_ALL_BYTES = 1_000_000

NAME = "bash"
DESCRIPTION = """Run a bash command in the repository root and return its exit code and output (stdout and stderr combined).
* Each call runs in a new shell: `cd` and `export` do not carry over to the next call. Chain them in one command if needed, e.g. `cd src && pytest`.
* There is no stdin. Do not run interactive commands.
* The command is killed after `timeout` seconds (default 120, max 600), together with any processes it started. Background processes are also killed when the command finishes.
* Output longer than 10000 characters is cut to the first and last 5000; the full output is saved to a file whose path is given, so you can read parts of it with grep or sed -n."""
PARAMETERS = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "The bash command to run."},
        "timeout": {"type": "integer",
                    "description": f"Seconds before the command is killed. Default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}."},
    },
    "required": ["command"],
    "additionalProperties": False,
}


class Bash:
    """在 workdir 里执行命令。output_dir 存完整输出，要放在仓库外面，免得混进 git diff。

    env 是命令能看到的全部环境变量；为 None 时按白名单生成（见 repoaegis.sandbox.env），
    不会继承本进程的全部环境。
    """

    def __init__(self, workdir: str, output_dir: str, env: dict[str, str] | None = None):
        self._workdir = workdir
        self._output_dir = output_dir
        self._env = command_env() if env is None else env
        self._counter = itertools.count(1)

    def tool(self) -> Tool:
        return Tool(NAME, DESCRIPTION, PARAMETERS, self.run)

    def run(self, args: dict) -> ToolResult:
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult("Error: `command` must be a non-empty string.", error=True)
        if "\0" in command:
            # 启动进程时参数里不能有空字符，否则 Python 直接抛异常
            return ToolResult("Error: `command` must not contain NUL characters.", error=True)
        timeout = args.get("timeout", DEFAULT_TIMEOUT)
        # bool 也是 int 的子类，要单独排除
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT:
            return ToolResult(f"Error: `timeout` must be an integer from 1 to {MAX_TIMEOUT}, got {timeout!r}.",
                              error=True)

        os.makedirs(self._output_dir, exist_ok=True)
        path = os.path.join(self._output_dir, f"bash-{next(self._counter)}.txt")
        timed_out = False
        with open(path, "wb") as out:
            # start_new_session：命令自成一个进程组，结束时能把它启动的进程一起杀掉
            process = subprocess.Popen(["bash", "-c", command], cwd=self._workdir, env=self._env,
                                       stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            _kill_group(process.pid)
            process.wait()

        output, truncated = _read_output(path)
        if not truncated:
            os.remove(path)
        if timed_out:
            head = f"Command timed out after {timeout} seconds and was killed. Output before it was killed:"
        else:
            head = f"Exit code: {process.returncode}"
        if truncated:
            head += (f"\n[Output was {os.path.getsize(path)} bytes, cut to the first {HEAD_CHARS} and last"
                     f" {TAIL_CHARS} characters. Full output: {path}]")
        # 退出码不为 0 不算工具出错：跑测试失败、grep 没搜到都是正常结果，交给模型判断
        details = {"exit_code": None if timed_out else process.returncode, "timed_out": timed_out,
                   "truncated": truncated, "output_path": path if truncated else None}
        return ToolResult(f"{head}\n{output}", error=timed_out, details=details)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # 组里已经没有进程了


def _read_output(path: str) -> tuple[str, bool]:
    """读命令输出；超过 OUTPUT_LIMIT 个字符时只取首尾。返回 (文字, 是否截断)。"""
    size = os.path.getsize(path)
    with open(path, "rb") as source:
        if size <= READ_ALL_BYTES:
            text = source.read().decode("utf-8", errors="replace")
            if len(text) <= OUTPUT_LIMIT:
                return text, False
            head, tail = text[:HEAD_CHARS], text[-TAIL_CHARS:]
        else:
            # 一个字符最多 4 个字节，各读这么多一定够；切在字符中间的那一两个字节会变成替换符
            head = source.read(HEAD_CHARS * 4).decode("utf-8", errors="replace")[:HEAD_CHARS]
            source.seek(-TAIL_CHARS * 4, os.SEEK_END)
            tail = source.read().decode("utf-8", errors="replace")[-TAIL_CHARS:]
    return f"{head}\n\n[... middle of output omitted ...]\n\n{tail}", True
