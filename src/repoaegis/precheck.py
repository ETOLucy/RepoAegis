"""开工前检查：按 issue 的当前状态和最后打上标签的人的权限，决定这次运行是否开工。

在工作流里用 `python -m repoaegis.precheck` 运行。只用标准库，不需要安装任何东西；
结论写进 GITHUB_OUTPUT，供后面的步骤读取。
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

# 接口返回的基础权限里，Maintain 记作 write，Triage 记作 read
ALLOWED_PERMISSIONS = {"admin", "write"}


@dataclass(frozen=True)
class Response:
    status: int
    data: object = None
    next_url: str | None = None


@dataclass(frozen=True)
class Decision:
    action: str  # start：开工；skip：安静结束；deny：没有触发权限
    reason: str
    labeler: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # issue 被转移时接口返回 301，不跟过去，交给 check 判断
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    """只读的 GitHub 接口客户端。服务器出错或网络出错时重试，其他状态码原样返回。"""

    def __init__(self, token: str, api_url: str = "https://api.github.com", retries: int = 3):
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._retries = retries
        self._opener = urllib.request.build_opener(_NoRedirect)

    def get(self, path_or_url: str) -> Response:
        url = path_or_url if path_or_url.startswith("http") else self._api_url + path_or_url
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "RepoAegis",
        })
        for attempt in range(1, self._retries + 1):
            try:
                with self._opener.open(request, timeout=30) as resp:
                    body = resp.read()
                    return Response(resp.status, json.loads(body) if body else None,
                                    _next_link(resp.headers.get("Link")))
            except urllib.error.HTTPError as error:
                error.close()
                if error.code < 500:
                    return Response(error.code)
                if attempt == self._retries:
                    raise
            except urllib.error.URLError:
                if attempt == self._retries:
                    raise
            time.sleep(2 ** attempt)
        raise AssertionError("unreachable")


def _next_link(header: str | None) -> str | None:
    # 分页信息在 Link 头里，形如 <https://...&page=2>; rel="next", <...>; rel="last"
    for part in (header or "").split(","):
        match = re.search(r'<([^>]+)>;\s*rel="next"', part)
        if match:
            return match.group(1)
    return None


def _expect_ok(response: Response, what: str) -> None:
    if response.status != 200:
        raise RuntimeError(f"{what}失败：HTTP {response.status}")


def last_labeler(github: GitHub, repo: str, number: int, label: str) -> str:
    """返回最后一次给 issue 打上 label 的人；找不到时返回空字符串。"""
    latest = None
    url = f"/repos/{repo}/issues/{number}/events?per_page=100"
    while url:
        page = github.get(url)
        _expect_ok(page, "读取 issue 事件")
        for event in page.data:
            if event["event"] != "labeled" or event["label"]["name"] != label:
                continue
            if latest is None or (event["created_at"], event["id"]) > (latest["created_at"], latest["id"]):
                latest = event
        url = page.next_url
    return (latest.get("actor") or {}).get("login", "") if latest else ""


def check(github: GitHub, repo: str, number: int, label: str) -> Decision:
    issue = github.get(f"/repos/{repo}/issues/{number}")
    if issue.status in (301, 404, 410):
        return Decision("skip", f"查不到 issue #{number}（已被转移、删除，或不存在）")
    _expect_ok(issue, "读取 issue")
    if "pull_request" in issue.data:
        return Decision("skip", f"#{number} 是 PR，不是 issue")
    if issue.data["state"] != "open":
        return Decision("skip", f"issue #{number} 已关闭")
    labels = {item if isinstance(item, str) else item["name"] for item in issue.data["labels"]}
    if label not in labels:
        return Decision("skip", f"issue #{number} 上已没有 {label} 标签")

    # 不用触发事件里的 sender：事件可能乱序到达，那个人不一定是打标签的人
    labeler = last_labeler(github, repo, number, label)
    if not labeler:
        return Decision("deny", f"找不到给 issue #{number} 打上 {label} 的记录，无法确认触发者")

    permission = github.get(f"/repos/{repo}/collaborators/{urllib.parse.quote(labeler)}/permission")
    if permission.status == 404:
        return Decision("deny", f"{labeler} 不是仓库协作者", labeler)
    _expect_ok(permission, "读取协作者权限")
    role = permission.data["role_name"]
    if permission.data["permission"] not in ALLOWED_PERMISSIONS:
        return Decision("deny", f"{labeler} 在仓库里的角色是 {role}，需要 Write、Maintain 或 Admin", labeler)
    return Decision("start", f"{labeler}（{role}）打上了 {label}", labeler)


def write_outputs(path: str, decision: Decision) -> None:
    # GITHUB_OUTPUT 每行一个 名字=值，值里不能有换行
    with open(path, "a", encoding="utf-8") as out:
        for name, value in (("action", decision.action), ("reason", decision.reason),
                            ("labeler", decision.labeler)):
            out.write(f"{name}={value.replace(chr(10), ' ')}\n")


def main() -> None:
    github = GitHub(os.environ["GITHUB_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    decision = check(github, os.environ["GITHUB_REPOSITORY"], int(os.environ["ISSUE_NUMBER"]),
                     os.environ["LABEL"])
    print(f"{decision.action}: {decision.reason}")
    write_outputs(os.environ["GITHUB_OUTPUT"], decision)


if __name__ == "__main__":
    main()
