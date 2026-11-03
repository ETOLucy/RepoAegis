"""str_replace_editor 工具：查看、新建、替换、插入文件。

规则（2026-10-27 定，依据见 docs/调研/编辑器的细节.md）：

- 四个命令 view、create、str_replace、insert；没有撤销，退回用 bash 里的 git。
- 路径必须是绝对路径；解析掉 .. 和符号链接后必须在仓库目录之内；.git/ 和 .github/ 下只能看不能改。
- 改文件前必须用 view 看过，而且看过之后没被别的东西改过（照 Claude Code）；本工具自己改的不用重看。
- 原文必须完全一致且只出现一次；不动制表符；文件用 \\r\\n 换行时，把模型给的文字也换成 \\r\\n。
- Python 文件改完用 flake8 查几条规则（照 SWE-agent），这次修改新增了错误就撤回，把错误和原文交给模型。
- 只改 UTF-8 文本文件；二进制文件和超过 10 MB 的文件不看也不改；不是 UTF-8 的能看（坏字节换成替换符）不能改。
- 新建时文件已存在、父目录不存在都报错，不覆盖、不自动建目录（照 SWE-agent）。
"""

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass, replace

from repoaegis.tools import Tool, ToolResult

MAX_VIEW_CHARS = 16_000
SNIPPET_LINES = 4  # 改完后返回改动处上下各几行
MAX_FILE_BYTES = 10 * 1024 * 1024
BINARY_PROBE_BYTES = 8000  # 开头这么多字节里有空字节，就当二进制文件
# SWE-agent edit_anthropic 编辑器的默认检查项：语法错误、缩进错误、用了没定义的名字等
LINT_SELECT = "F821,F822,F831,E111,E112,E113,E999,E902"
COMMANDS = ("view", "create", "str_replace", "insert")

NAME = "str_replace_editor"
DESCRIPTION = """View, create and edit files in the repository.
* Paths must be absolute and inside the repository. Files under .git/ and .github/ can be viewed but not changed.
* `view` on a file shows it with line numbers, like `cat -n`. Use `view_range` [start, end] to see part of it; end -1 means to the end of the file. Output longer than 16000 characters is cut, so find line numbers with `grep -n` first. `view` on a directory lists files and directories up to 2 levels deep, excluding hidden items.
* You must `view` a file before using `str_replace` or `insert` on it, and view it again if anything other than this tool (e.g. a bash command) changed it since. Reading a file with bash does not count.
* `str_replace` replaces `old_str` with `new_str`. `old_str` must match exactly one place in the file, including whitespace and indentation; include enough surrounding lines to make it unique. An empty `new_str` deletes `old_str`.
* `insert` inserts `new_str` after line `insert_line` (0 inserts at the beginning of the file).
* `create` creates a new file with `file_text`. It fails if the file already exists or its parent directory does not exist (create the directory with `mkdir -p` first).
* After each change to a Python file, flake8 checks for syntax errors, indentation errors and undefined names. If the change introduces new ones, it is not applied and the errors are shown.
* Only UTF-8 text files up to 10 MB can be changed. To undo changes, use git in bash (e.g. `git diff`, `git checkout -- <file>`)."""
PARAMETERS = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "enum": list(COMMANDS), "description": "The command to run."},
        "path": {"type": "string", "description": "Absolute path to a file or directory in the repository."},
        "view_range": {"type": "array", "items": {"type": "integer"},
                       "description": "For `view` on a file: [start, end] line numbers, 1-based; end -1 means to the end."},
        "file_text": {"type": "string", "description": "For `create`: the content of the new file."},
        "old_str": {"type": "string", "description": "For `str_replace`: the exact text to replace."},
        "new_str": {"type": "string",
                    "description": "For `str_replace`: the replacement text (empty to delete). For `insert`: the text to insert."},
        "insert_line": {"type": "integer", "description": "For `insert`: insert after this line; 0 means at the beginning."},
    },
    "required": ["command", "path"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class LintError:
    line: int
    col: int
    code: str
    text: str

    def __str__(self) -> str:
        return f"line {self.line} col {self.col}: {self.code} {self.text}"


def flake8(path: str) -> list[LintError]:
    """用 RepoAegis 自己虚拟环境里的 flake8 查一个文件。flake8 跑不起来算环境坏了，抛异常。"""
    result = subprocess.run(
        [sys.executable, "-m", "flake8", "--isolated", f"--select={LINT_SELECT}",
         "--format=%(row)d:%(col)d:%(code)s:%(text)s", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    # 有错误时退出码是 1，没有是 0；其他情况（包括没装 flake8）都会在标准错误里留话
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise RuntimeError(f"flake8 运行失败（退出码 {result.returncode}）：{result.stderr.strip()}")
    errors = []
    for line in result.stdout.splitlines():
        row, col, code, text = line.split(":", 3)
        errors.append(LintError(int(row), int(col), code, text))
    return errors


class _Refused(Exception):
    """模型能自己改的错：写成文字交回模型。"""


class Editor:
    """在 workdir（仓库根目录）里看文件、改文件。lint 默认用 flake8，测试时可以换掉。"""

    def __init__(self, workdir: str, lint=flake8):
        self._root = os.path.realpath(workdir)
        self._lint = lint
        # 真实路径 → 最近一次用 view 看过、或本工具改完后的内容哈希
        self._seen: dict[str, str] = {}

    def tool(self) -> Tool:
        return Tool(NAME, DESCRIPTION, PARAMETERS, self.run)

    def run(self, args: dict) -> ToolResult:
        command = args.get("command")
        details = {"command": command, "path": args.get("path")}
        try:
            if command not in COMMANDS:
                raise _Refused(f"`command` must be one of {', '.join(COMMANDS)}, got {command!r}.")
            path = self._resolve(args.get("path"), writing=command != "view")
            details["path"] = path
            if command == "view":
                return ToolResult(self._view(path, args.get("view_range")), details=details)
            if command == "create":
                return self._create(path, args.get("file_text"), details)
            if command == "str_replace":
                return self._str_replace(path, args.get("old_str"), args.get("new_str", ""), details)
            return self._insert(path, args.get("insert_line"), args.get("new_str"), details)
        except _Refused as refused:
            return ToolResult(f"Error: {refused}", error=True, details=details)

    def _resolve(self, path, writing: bool) -> str:
        """检查路径，返回解析掉 .. 和符号链接之后的真实路径。"""
        if not isinstance(path, str) or not path.strip():
            raise _Refused("`path` must be a non-empty string.")
        if "\0" in path:
            raise _Refused("`path` must not contain NUL characters.")
        if not os.path.isabs(path):
            raise _Refused(f"`path` must be absolute. Did you mean {os.path.join(self._root, path)}?")
        real = os.path.realpath(path)
        if os.path.commonpath([real, self._root]) != self._root:
            raise _Refused(f"{path} is outside the repository {self._root}.")
        if writing:
            parts = os.path.relpath(real, self._root).split(os.sep)
            # 符号链接已解析，绕道指进 .git/、.github/ 也拦得住
            if parts[0] == ".github" or ".git" in parts:
                raise _Refused(f"{path} is under .git/ or .github/, which can be viewed but not changed.")
        return real

    # ---- 查看（view）----

    def _view(self, path: str, view_range) -> str:
        if not os.path.exists(path):
            raise _Refused(f"{path} does not exist.")
        if os.path.isdir(path):
            if view_range is not None:
                raise _Refused("`view_range` can only be used on files, not directories.")
            return self._list_dir(path)
        data = self._read_bytes(path)
        self._seen[path] = _digest(data)
        try:
            text, note = data.decode("utf-8"), ""
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            note = "Note: this file is not valid UTF-8; undecodable bytes are shown as � and it cannot be changed with this tool.\n"
        lines = _split_lines(text)
        if not lines:
            if view_range is not None:
                raise _Refused(f"{path} is empty, so `view_range` cannot be used.")
            return f"{note}{path} is empty."
        start, end = 1, len(lines)
        if view_range is not None:
            start, end, clamp_note = _check_view_range(view_range, len(lines))
            note += clamp_note
        numbered = _numbered(lines[start - 1:end], start)
        if len(numbered) > MAX_VIEW_CHARS:
            numbered = (numbered[:MAX_VIEW_CHARS] + "\n[Output cut at 16000 characters. Find the line numbers you need"
                        " with `grep -n` in bash, then view them with `view_range`.]")
        return f"{note}Here is {path}, lines {start}-{end} of {len(lines)}, with line numbers:\n{numbered}"

    def _list_dir(self, path: str) -> str:
        entries = []
        names = sorted(os.listdir(path))
        hidden = sum(name.startswith(".") for name in names)
        for name in names:
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            # 不顺着符号链接往下走，免得走出仓库
            if os.path.isdir(full) and not os.path.islink(full):
                entries.append(full + "/")
                for child in sorted(os.listdir(full)):
                    if not child.startswith("."):
                        child_full = os.path.join(full, child)
                        entries.append(child_full + ("/" if os.path.isdir(child_full) else ""))
            else:
                entries.append(full + ("/" if os.path.isdir(full) else ""))
        listing = "\n".join(entries)
        if len(listing) > MAX_VIEW_CHARS:
            listing = listing[:MAX_VIEW_CHARS] + "\n[Output cut at 16000 characters. Use `ls` in bash to look at large directories.]"
        text = f"Files and directories up to 2 levels deep in {path}, excluding hidden items:\n{listing}"
        if hidden:
            text += f"\n{hidden} hidden item(s) directly in {path} are not shown; use `ls -a` in bash to see them."
        return text

    # ---- 新建、替换、插入（create、str_replace、insert）----

    def _create(self, path: str, file_text, details: dict) -> ToolResult:
        if not isinstance(file_text, str):
            raise _Refused("`file_text` is required for `create` and must be a string.")
        if os.path.lexists(path):
            raise _Refused(f"{path} already exists. Use `str_replace` or `insert` to change it.")
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            raise _Refused(f"The parent directory {parent} does not exist. Create it first with `mkdir -p` in bash.")
        data = file_text.encode("utf-8")
        _write(path, data)
        if path.endswith(".py"):
            errors = self._lint(path)
            if errors:
                os.remove(path)
                details["lint_errors"] = [str(error) for error in errors]
                raise _Refused(f"{path} was not created because it has lint errors:\n{_bullets(errors)}\n"
                               "Fix the content and try again.")
        self._seen[path] = _digest(data)
        return ToolResult(f"Created {path} ({len(_split_lines(file_text))} lines).", details=details)

    def _str_replace(self, path: str, old_str, new_str, details: dict) -> ToolResult:
        if not isinstance(old_str, str) or old_str == "":
            raise _Refused("`old_str` is required for `str_replace` and must be a non-empty string.")
        if not isinstance(new_str, str):
            raise _Refused("`new_str` must be a string.")
        if old_str == new_str:
            raise _Refused("`old_str` and `new_str` are the same, so there is nothing to change.")
        data, text = self._read_for_edit(path)
        positions = _find_all(text, old_str)
        if not positions and "\r\n" in text:
            # 文件用 \r\n 换行，模型给的通常是 \n
            old_str, new_str = _to_crlf(old_str), _to_crlf(new_str)
            positions = _find_all(text, old_str)
        if not positions:
            raise _Refused(f"`old_str` does not appear verbatim in {path}. It must match exactly, including"
                           " whitespace and indentation; view the file to copy the exact text.")
        if len(positions) > 1:
            lines = sorted({text.count("\n", 0, pos) + 1 for pos in positions})
            raise _Refused(f"`old_str` appears {len(positions)} times in {path} (lines {', '.join(map(str, lines))})."
                           " Include more surrounding lines to make it unique.")
        pos = positions[0]
        new_text = text[:pos] + new_str + text[pos + len(old_str):]
        start = text.count("\n", 0, pos) + 1
        return self._apply(path, data, text, new_text, start, _span(old_str), _span(new_str),
                           new_str.count("\n") - old_str.count("\n"), details)

    def _insert(self, path: str, insert_line, new_str, details: dict) -> ToolResult:
        if isinstance(insert_line, bool) or not isinstance(insert_line, int):
            raise _Refused("`insert_line` is required for `insert` and must be an integer.")
        if not isinstance(new_str, str) or new_str == "":
            raise _Refused("`new_str` is required for `insert` and must be a non-empty string.")
        data, text = self._read_for_edit(path)
        n_lines = len(_split_lines(text))
        if not 0 <= insert_line <= n_lines:
            raise _Refused(f"`insert_line` must be from 0 to {n_lines} (the number of lines in {path}), got {insert_line}.")
        eol = "\r\n" if "\r\n" in text else "\n"
        block = new_str.replace("\r\n", "\n").replace("\n", eol)
        if not block.endswith(eol):
            block += eol
        offset = 0
        for _ in range(insert_line):
            offset = text.find("\n", offset) + 1
        if insert_line and offset == 0:
            # 最后一行没有换行符，插在文件末尾：先补一个换行
            offset, block = len(text), eol + block
        new_text = text[:offset] + block + text[offset:]
        return self._apply(path, data, text, new_text, insert_line + 1, 0, _span(new_str), _span(new_str), details)

    def _apply(self, path: str, data: bytes, text: str, new_text: str, start: int, old_span: int, new_span: int,
               shift: int, details: dict) -> ToolResult:
        """写入改动；Python 文件改完检查，新增了错误就撤回。

        start 是改动开始的行号；old_span、new_span 是改动改前、改后占几行；shift 是改动之后的行号挪了多少。
        """
        is_python = path.endswith(".py")
        before = self._lint(path) if is_python else []
        new_data = new_text.encode("utf-8")
        _write(path, new_data)
        if is_python:
            added = _new_errors(before, self._lint(path), start, old_span, shift)
            if added:
                _write(path, data)
                details["lint_errors"] = [str(error) for error in added]
                details["reverted"] = True
                raise _Refused(
                    f"The change was not applied because it introduced new lint errors:\n{_bullets(added)}\n\n"
                    f"How the changed section would have looked:\n{_snippet(new_text, start, new_span)}\n\n"
                    f"The original section, which is still in the file:\n{_snippet(text, start, old_span)}\n\n"
                    "Fix the change and try again.")
        self._seen[path] = _digest(new_data)
        return ToolResult(f"Edited {path}. The changed section:\n{_snippet(new_text, start, new_span)}\n"
                          "Check that it is what you intended; edit again if needed.", details=details)

    def _read_for_edit(self, path: str) -> tuple[bytes, str]:
        if not os.path.exists(path):
            raise _Refused(f"{path} does not exist. Use `create` to make a new file.")
        if os.path.isdir(path):
            raise _Refused(f"{path} is a directory; only `view` works on directories.")
        data = self._read_bytes(path)
        seen = self._seen.get(path)
        if seen is None:
            raise _Refused(f"View {path} with this tool before changing it (reading it with bash does not count).")
        if seen != _digest(data):
            raise _Refused(f"{path} was changed since you last viewed it. View it again before changing it.")
        try:
            return data, data.decode("utf-8")
        except UnicodeDecodeError:
            raise _Refused(f"{path} is not valid UTF-8 and cannot be changed with this tool. Use bash instead.") from None

    @staticmethod
    def _read_bytes(path: str) -> bytes:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            raise _Refused(f"{path} is larger than 10 MB. Use bash (e.g. grep, head, sed -n) to look at it.")
        with open(path, "rb") as source:
            data = source.read()
        if b"\0" in data[:BINARY_PROBE_BYTES]:
            raise _Refused(f"{path} looks like a binary file, which this tool does not handle.")
        return data


def _new_errors(before: list[LintError], after: list[LintError], start: int, old_span: int,
                shift: int) -> list[LintError]:
    """改完后多出来的错误（照 SWE-agent 的 format_flake8_output）。

    改动之前就有的错误，按改动增减的行数挪到新行号，挪完对得上的不算；落在被改那几行里的旧错误不作数；
    改动位置上方的错误也不算是这次改出来的。SWE-agent 用新文本的行数当旧范围，增减的行数总算成 0，这里分开算。
    """
    known = set()
    for error in before:
        if error.line < start:
            known.add(error)
        elif error.line >= start + old_span:
            known.add(replace(error, line=error.line + shift))
    return [error for error in after if error.line >= start and error not in known]


def _span(text: str) -> int:
    """一段文字占几行：最后的换行不另算一行。"""
    return text.count("\n") + (0 if text.endswith("\n") or not text else 1)


def _check_view_range(view_range, n_lines: int) -> tuple[int, int, str]:
    if (not isinstance(view_range, list) or len(view_range) != 2
            or any(isinstance(n, bool) or not isinstance(n, int) for n in view_range)):
        raise _Refused("`view_range` must be a list of two integers, [start, end].")
    start, end = view_range
    if not 1 <= start <= n_lines:
        raise _Refused(f"`view_range` start must be from 1 to {n_lines} (the number of lines), got {start}.")
    if end == -1:
        return start, n_lines, ""
    if end < start:
        raise _Refused(f"`view_range` end must be -1 or at least the start ({start}), got {end}.")
    if end > n_lines:
        return start, n_lines, f"Note: the file has only {n_lines} lines, so showing lines {start}-{n_lines}.\n"
    return start, end, ""


def _split_lines(text: str) -> list[str]:
    """按 \\n 分行（不用 splitlines，它会把 \\x0c 等也当换行），显示时去掉行尾的 \\r。"""
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _numbered(lines: list[str], first: int) -> str:
    return "\n".join(f"{number:6}\t{line}" for number, line in enumerate(lines, first))


def _snippet(text: str, start: int, count: int) -> str:
    """start 行起的 count 行，上下各多带 SNIPPET_LINES 行，带行号。"""
    lines = _split_lines(text)
    first = max(1, start - SNIPPET_LINES)
    last = min(len(lines), start + max(count, 1) - 1 + SNIPPET_LINES)
    return _numbered(lines[first - 1:last], first)


def _find_all(text: str, needle: str) -> list[int]:
    """所有出现的位置，包括互相重叠的。"""
    positions = []
    pos = text.find(needle)
    while pos != -1:
        positions.append(pos)
        pos = text.find(needle, pos + 1)
    return positions


def _to_crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _bullets(errors: list[LintError]) -> str:
    return "\n".join(f"- {error}" for error in errors)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: str, data: bytes) -> None:
    with open(path, "wb") as out:
        out.write(data)
