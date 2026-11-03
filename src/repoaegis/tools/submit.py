"""submit 工具：agent 交卷。要调用两次才算交上。

规则（2026-10-30 定，依据见 docs/调研/交卷工具.md 第五节）：

- 第一次调用：程序把仓库里的全部改动（和开工时的提交比，含新文件）生成补丁，
  连同复查清单发回模型，不结束。被 .gitignore 忽略的文件一般不进补丁，但 agent 用 git add -f 强行加进暂存区、
  或删掉 .gitignore 里那一行，照样会进来（2026-10-30 实测），要靠产出检查拦下。顺带用产出检查的规则先查一遍，有问题一并提醒，让它趁早改。
- 第二次调用：补丁和第一次时完全一样才算交卷；不一样就当作新的第一次，重新发补丁和清单。
- 汇报五个字段，每次调用都要带，以交卷那一次的为准。字段不合格就报错交回模型改。
- 没有改动也能交卷，同样两次；外层程序看到补丁为空，就在 issue 里留言说明，不开 PR。
- 交卷后把补丁和汇报写进仓库外的输出目录，details["submitted"] 为 True，主循环看到就结束。

生成补丁用一份临时的 git 暂存区（GIT_INDEX_FILE），不动 agent 自己的暂存区，
否则 agent 之后再跑 git diff 会看不到这些改动。
仓库里的 git 配置 agent 能改，所以关掉 fsmonitor、外部 diff 程序和 textconv，环境变量用 agent 命令的那份白名单；
补丁是否可信不靠这里，产出检查作业会在不信任 agent 的机器上再查一遍。
"""

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable

from repoaegis.sandbox.env import command_env
from repoaegis.tools import Tool, ToolResult

MAX_TITLE_CHARS = 100
MAX_FIELD_CHARS = 3000
MAX_SHOWN_PATCH_CHARS = 16_000  # 复查时发给模型的补丁最多这么长，和编辑器 view 的上限一致
# 字段名 → 能不能为空
FIELDS = {"title": False, "summary": False, "testing": False, "assumptions": True, "skipped_requests": True}
PATCH_FILE = "patch.diff"
REPORT_FILE = "report.json"

NAME = "submit"
DESCRIPTION = """Submit your work and final report. This ends the task, so call it only when you are done, or when you have found that the issue cannot or should not be fixed.
* Submitting takes two calls. The first call shows you the patch that will be submitted (every change in the repository compared with the original commit, including new files; files ignored by .gitignore are not included) and a review checklist. Call submit again to confirm. If any file changed between the two calls, the second call counts as a new first call.
* The report from the confirming call is the one used. A pull request is opened with your title and report; if there are no changes, no pull request is opened and your report is posted on the issue instead.
* Write the report in the language of the issue."""
PARAMETERS = {
    "type": "object",
    "properties": {
        "title": {"type": "string",
                  "description": f"Pull request title, one line, at most {MAX_TITLE_CHARS} characters."},
        "summary": {"type": "string",
                    "description": "What you changed and why. If you made no changes, why not."},
        "testing": {"type": "string",
                    "description": "How you verified the change: what you ran and the results. If you could not verify it, why."},
        "assumptions": {"type": "string",
                        "description": "Details the issue left open that you decided yourself, and what you decided. Empty if none."},
        "skipped_requests": {"type": "string",
                             "description": "Requests in the issue you did not carry out, and why. Empty if none."},
    },
    "required": list(FIELDS),
    "additionalProperties": False,
}

CHECKLIST = """Before confirming, check:
1. After your last code change, did you re-run the relevant tests or reproduction steps?
2. Did you remove temporary scripts, debug output and other files that are not part of the fix?
3. Are there changes unrelated to the issue (reformatting, edits to other files)? Revert them.
4. Do `assumptions` and `skipped_requests` list everything you decided yourself or did not do?
If everything is right, call submit again with your final report. If you change any file first, the review starts over."""
NO_CHANGES = """You have made no changes. If you confirm, no pull request is opened; your report is posted on the issue instead.
Make sure `summary` explains why no change is made. If you meant to change files, do that first.
If this is right, call submit again with your final report."""


class Submit:
    """workdir 是仓库根目录，base_commit 是开工时的提交，output_dir 放交上来的补丁和汇报（要在仓库外面）。

    check 是产出检查的规则：收到补丁和汇报，返回发给模型的提醒（英文），没问题返回空列表。
    """

    def __init__(self, workdir: str, base_commit: str, output_dir: str,
                 check: Callable[[bytes, dict], list[str]], env: dict[str, str] | None = None):
        self._workdir = workdir
        self._base = base_commit
        self._output_dir = output_dir
        self._check = check
        self._env = command_env() if env is None else env
        self._pending: bytes | None = None  # 上一次复查时发给模型的补丁
        self.submitted = False

    def tool(self) -> Tool:
        return Tool(NAME, DESCRIPTION, PARAMETERS, self.run)

    def run(self, args: dict) -> ToolResult:
        if self.submitted:
            return ToolResult("Error: the work has already been submitted.", error=True)
        problems = _report_problems(args)
        if problems:
            # 汇报不合格不算一次调用：已经复查过的补丁，改好汇报再交就行
            return ToolResult("Error: the report is not valid:\n" + "\n".join(f"- {p}" for p in problems),
                              error=True)
        report = {name: args[name] for name in FIELDS}
        patch, stat = self._diff()
        details = {"has_changes": bool(patch), "patch_bytes": len(patch)}

        if patch == self._pending:
            self.submitted = True
            os.makedirs(self._output_dir, exist_ok=True)
            patch_path = os.path.join(self._output_dir, PATCH_FILE)
            report_path = os.path.join(self._output_dir, REPORT_FILE)
            with open(patch_path, "wb") as out:
                out.write(patch)
            with open(report_path, "w", encoding="utf-8") as out:
                json.dump(report, out, ensure_ascii=False, indent=2)
            return ToolResult("Submitted. The task is finished; no further tool calls will be run.",
                              details={**details, "submitted": True, "patch_path": patch_path,
                                       "report_path": report_path})

        restarted = self._pending is not None
        self._pending = patch
        warnings = self._check(patch, report)
        parts = []
        if restarted:
            parts.append("The files changed since your last submit call, so this is a new review.")
        if patch:
            parts += [f"This is the patch that will be submitted:\n{stat}", _shown(patch)]
        if warnings:
            parts.append("Problems found; fix them before confirming:\n" + "\n".join(f"- {w}" for w in warnings))
        parts.append(CHECKLIST if patch else NO_CHANGES)
        return ToolResult("\n\n".join(parts),
                          details={**details, "submitted": False, "restarted": restarted, "warnings": warnings})

    # ---- 生成补丁 ----

    def _git(self, *args: str, index: str | None = None) -> bytes:
        env = dict(self._env)
        if index is not None:
            env["GIT_INDEX_FILE"] = index
        result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false", *args],
                                cwd=self._workdir, env=env, capture_output=True)
        if result.returncode != 0:
            # git 失败是环境问题（仓库坏了、开工提交不存在），模型改不了
            raise RuntimeError(f"git {args[0]} 失败（退出码 {result.returncode}）："
                               f"{result.stderr.decode('utf-8', 'replace').strip()}")
        return result.stdout

    def _diff(self) -> tuple[bytes, str]:
        """仓库现在的样子和开工提交之间的全部差别，含新文件和二进制文件。返回 (补丁, 改了哪些文件的摘要)。"""
        with tempfile.TemporaryDirectory() as tmp:
            index = os.path.join(tmp, "index")
            # 从 agent 的暂存区复制一份，git 能沿用里面记下的文件状态，不用把每个文件重新算一遍
            real_index = self._git("rev-parse", "--path-format=absolute", "--git-path", "index").decode().strip()
            if os.path.exists(real_index):
                shutil.copyfile(real_index, index)
            else:
                self._git("read-tree", self._base, index=index)
            self._git("add", "--all", index=index)
            diff = ("diff", "--cached", "--no-ext-diff", "--no-textconv")
            patch = self._git(*diff, "--binary", self._base, index=index)
            stat = self._git(*diff, "--stat=200", self._base, index=index)
        return patch, stat.decode("utf-8", "replace").rstrip()


def _report_problems(args: dict) -> list[str]:
    problems = [f"unknown field `{name}`" for name in args if name not in FIELDS]
    for name, may_be_empty in FIELDS.items():
        value = args.get(name)
        if value is None:
            problems.append(f"`{name}` is missing")
        elif not isinstance(value, str):
            problems.append(f"`{name}` must be a string")
        elif not may_be_empty and not value.strip():
            problems.append(f"`{name}` must not be empty")
        else:
            limit = MAX_TITLE_CHARS if name == "title" else MAX_FIELD_CHARS
            if len(value) > limit:
                problems.append(f"`{name}` is {len(value)} characters; the limit is {limit}")
            if name == "title" and ("\n" in value or "\r" in value):
                problems.append("`title` must be one line")
    return problems


def _shown(patch: bytes) -> str:
    text = patch.decode("utf-8", "replace")
    if len(text) <= MAX_SHOWN_PATCH_CHARS:
        return text
    rest = len(text) - MAX_SHOWN_PATCH_CHARS
    return (f"{text[:MAX_SHOWN_PATCH_CHARS]}\n[... {rest} more characters not shown. "
            f"Use `git status` and `git diff` in bash to see the rest.]")
