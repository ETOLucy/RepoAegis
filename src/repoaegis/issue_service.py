"""读 issue 服务：替 agent 当场去 GitHub 读本仓库的一篇 issue，把关、清洗后交回。

agent 跑在 AWF 容器里，没有令牌，也连不上 GitHub。这个服务跑在容器外的运行机上，拿着 agent 作业的
只读令牌；容器里经 AWF 的代理访问 http://host.docker.internal:端口/issue/编号（2026-11-02 实测走得通，
容器里找不到令牌，见 docs/调研/agent运行中读issue.md 第五之二节）。在工作流里用
`python -m repoaegis.issue_service` 在后台启动，只用标准库。

规则（2026-11-01、11-02 定，见 docs/agent读到的内容.md 第五节）：不限篇数、不限层数，也不看剩余接口额度，
GitHub 拒绝时把原因交回；每一篇单独把关，比开工前检查对被打标签 issue 的还严：被打标签的 issue
有打标签的人担保，没改过的不看作者，被提醒过、重新打标签也能放行；这里读的没人担保，没改过的也要看作者，
最后写或改的人里有一个没有写权限就不给看。
容器里任何命令都能访问这个端口，不只是 read_issue_ref，所以规则全在这里执行，不靠工具那边。

一次请求走的路（和 docs/agent读到的内容.md 第五节的流程图一致，第 1、2、8 步在 read_issue_ref 里）：

  第 3 步  收请求，只认 /issue/编号；读过的直接给。            Handler、IssueService.answer
  第 4 步  问 GitHub：这个编号在本仓库是 issue、PR，还是没有。   ask_github
  第 5 步  还原打标签那一刻的版本，查最后修改人有没有写权限。    check_version
  第 6 步  清洗、去图片。                                    clean
  第 7 步  记住结果，日志记一行。                             IssueService.answer
"""

import json
import os
import re
import sys
import traceback
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from repoaegis.content_check import Issue, Source, Version, omit_media, version_at
from repoaegis.precheck import GitHub
from repoaegis.sanitize import sanitize_body, sanitize_title

DEFAULT_PORT = 18080
# GraphQL 的编号是 32 位整数，更大的编号不可能存在，不用去问
MAX_NUMBER = 2**31 - 1
_PATH = re.compile(r"/issue/([0-9]{1,10})/?")


# ---------------------------------------------------------------------------
# 第 3 步：收请求。只认 GET /issue/编号，其余路径一概回 404。
# ---------------------------------------------------------------------------

def make_handler(service: "IssueService") -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            match = _PATH.fullmatch(path)
            try:
                if path == "/health":
                    # 给工作流确认服务起来了
                    status, body = 200, {"ok": True}
                elif match and int(match.group(1)) > 0:
                    status, body = service.answer(int(match.group(1)))
                else:
                    status, body = 404, {"error": "unknown path; use /issue/<number>"}
            except Exception as exc:
                # 程序本身的错：照样回一句话，免得 agent 那边只看到连接断开
                traceback.print_exc()
                status, body = 500, {"error": f"internal error in the issue service: {exc}"}
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            # 每次请求由 IssueService 记一行，不再打默认的访问日志
            pass

    return Handler


class IssueService:
    """按编号回答。读过的记住（分段读时会反复问同一篇）；GitHub 出错的不记，下次重查。"""

    def __init__(self, source: Source, labeled_at: str, log=sys.stdout):
        self._source = source
        self._labeled_at = labeled_at
        self._log = log
        self._cache = {}

    def answer(self, number: int) -> tuple[int, dict]:
        """返回 (HTTP 状态码, 交回的内容)。GitHub 出错时是 502 和 {"error": 原因}。"""
        # 第 3 步：读过的直接给
        if number in self._cache:
            self._record(number, self._cache[number], cached=True)
            return 200, self._cache[number]
        try:
            found = read_issue(self._source, number, self._labeled_at)
        except (RuntimeError, OSError) as exc:
            # 接口额度用完、GitHub 出错、网络断了：原因交回 agent，不记住
            self._record(number, {"error": str(exc)})
            return 502, {"error": str(exc)}
        # 第 7 步：记住结果，日志记一行
        self._cache[number] = found
        self._record(number, found)
        return 200, found

    def _record(self, number: int, found: dict, cached: bool = False) -> None:
        # 按规则，不给看时是谁写的（writers）只该进日志、不交回 agent。但现在 found 里带着 writers，
        # 会随回答交回容器（工具本身不显示，容器里的命令直接访问端口就能看到），要改，见 docs/agent读到的内容.md 第八节
        line = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "number": number, "cached": cached,
                **{key: found[key] for key in ("read", "skipped", "writers", "error") if key in found}}
        print(json.dumps(line, ensure_ascii=False), file=self._log, flush=True)


def read_issue(source: Source, number: int, labeled_at: str) -> dict:
    """第 4 到 6 步。能看：{"read": True, "title", "state", "body"}；
    不能看：{"read": False, "skipped": 原因}，原因是 untrusted_writer 时另带 writers。"""
    item = ask_github(source, number)
    checked = check_version(source, item, labeled_at)
    if isinstance(checked, dict):
        return checked
    return clean(checked, item)


# ---------------------------------------------------------------------------
# 第 4 步：问 GitHub。只问本仓库（Source 建的时候就绑定了仓库），一次只问这一个编号。
# ---------------------------------------------------------------------------

def ask_github(source: Source, number: int) -> Issue | str | None:
    """带全部修改记录的 Issue；PR 返回 "PullRequest"；不存在、已删除、已转移、讨论区帖子返回 None。"""
    if number > MAX_NUMBER:
        return None
    return source.issues([number])[number]


# ---------------------------------------------------------------------------
# 第 5 步：还原打标签那一刻的版本，查最后修改人。
#
# 被打标签的 issue 有打标签的人替那一版担保，只看修改人；这里读的没人担保，
# 所以标题、正文没改过的，看作者。有一个没有写权限就不给看。
# ---------------------------------------------------------------------------

def check_version(source: Source, item: Issue | str | None, labeled_at: str) -> Version | dict:
    """过关返回那一版；不过关返回 {"read": False, "skipped": 原因, ...}。"""
    if item is None:
        return {"read": False, "skipped": "not_found"}
    if not isinstance(item, Issue):
        return {"read": False, "skipped": "pull_request"}
    version = version_at(item, labeled_at)
    if version is None:
        return {"read": False, "skipped": "created_after_label"}
    writers = [change.login if change else item.author for change in (version.title_change, version.body_change)]
    # 已注销的账号 login 为空，算没有权限；日志里记成 GitHub 的叫法 ghost
    untrusted = sorted({login or "ghost" for login in writers if not source.is_allowed(login)})
    if untrusted:
        return {"read": False, "skipped": "untrusted_writer", "writers": untrusted}
    if version.body is None:
        return {"read": False, "skipped": "body_deleted"}
    return version


# ---------------------------------------------------------------------------
# 第 6 步：清洗、去图片。和被打标签的 issue 走同一套清洗；图片和附件换成一句说明。
# ---------------------------------------------------------------------------

def clean(version: Version, item: Issue) -> dict:
    # state 是现在的状态（open、closed），不是打标签那一刻的
    return {"read": True, "title": sanitize_title(version.title), "state": item.state,
            "body": omit_media(sanitize_body(version.body))}


def main() -> None:
    # 令牌只在这个进程里用，取出后从环境变量里删掉，免得以后这里开的子进程继承
    token = os.environ.pop("GITHUB_TOKEN")
    repo = os.environ["GITHUB_REPOSITORY"]
    github = GitHub(token, os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                    graphql_url=os.environ.get("GITHUB_GRAPHQL_URL"))
    service = IssueService(Source(github, repo), os.environ["LABELED_AT"])
    port = int(os.environ.get("ISSUE_SERVICE_PORT", DEFAULT_PORT))
    # 容器经 host.docker.internal 访问运行机，那个地址属于 AWF 启动时才建的网络，所以监听所有网卡。
    # 一次只处理一个请求：agent 的工具调用本来就是一个接一个的
    server = HTTPServer(("0.0.0.0", port), make_handler(service))
    print(f"issue service for {repo} at labeled_at={os.environ['LABELED_AT']} listening on port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
