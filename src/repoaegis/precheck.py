"""开工前检查：看这条事件是谁做的，再看 issue 现在的状态，决定停掉谁、要不要开工。

在工作流里用 `python -m repoaegis.precheck` 运行，只用标准库。规则（2026-10-26 定）：

- 只有 Write、Maintain、Admin（接口里的 permission 为 admin 或 write）的动作才算数。
- 他们撤标签、关闭、删除、转移 issue：无条件停掉这个 issue 正在跑的运行。
- 他们打标签时，如果已有运行在跑：内容一致就让它继续，不一致就停掉重来。
  内容是内容检查（content_check.py）按打标签的时间整理出来的：标题、正文，加上引用的那一层（2026-10-30 定）。
  内容检查没通过就不开工（结论 stop，由收尾步骤留言、撤标签），正在跑的处理的是旧版本，一并停掉。
- 其他人做这些动作：一律不理会，这次运行安静结束。
- 停掉该停的之后，再按 issue 现在的状态决定要不要开工：开着、挂着标签、最后打标签的人有权限。

暂停与存档（2026-10-27 定，见 docs/暂停、存档与失败重来.md）：

- 停掉一次运行后，等它完全结束再往下判断，因为它被取消后还要上传暂停存档。
- 开工时找这个 issue 的暂停存档：内容一致、没过截止时刻的，从最新一份接着做；其余的删掉。
- 运行的身份 = issue 编号 + 最后一次打上标签的时间。

结论写进 GITHUB_OUTPUT；要开工时写两个文件：快照（这次运行的身份和结论）和整理好的内容，
由后面的步骤传成运行产物，产物名里带内容指纹，供之后的运行对比。
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from repoaegis.checkpoint import PauseCheckpoint, parse_pause_name

# 接口返回的基础权限里，Maintain 记作 write，Triage 记作 read
ALLOWED_PERMISSIONS = {"admin", "write"}
STOP_ACTIONS = {"unlabeled", "closed", "deleted", "transferred"}
ACTIVE_RUN_STATUSES = ("in_progress", "queued")
# 停掉一次运行后，等它完全结束（包括被取消后仍会执行的存档、收尾步骤）最多多久
WAIT_TIMEOUT_SECONDS = 420
WAIT_INTERVAL_SECONDS = 5


@dataclass(frozen=True)
class Response:
    status: int
    data: object = None
    next_url: str | None = None


@dataclass(frozen=True)
class ActiveRun:
    run_id: int
    content_hash: str


@dataclass(frozen=True)
class Labeling:
    """最后一次打上标签的那条事件：谁、什么时候。运行的身份 = issue 编号 + 这个时间。"""

    actor: str
    created_at: str


@dataclass(frozen=True)
class Decision:
    action: str  # start：开工；skip：不开工，安静结束；stop：内容检查没通过，不开工，要留言、撤标签
    reason: str
    labeler: str = ""
    content_hash: str = ""
    cancelled: tuple[int, ...] = ()
    labeled_at: str = ""
    resume: PauseCheckpoint | None = None  # 开工时从这份暂停存档接着做
    deleted: tuple[int, ...] = ()  # 删掉的过时暂停存档（产物编号）
    unfinished: tuple[int, ...] = ()  # 停掉后等到超时仍没结束的运行
    kind: str = ""  # stop 时内容检查给的类别：editor、media、nested
    content: dict | None = None  # start 时交给 agent 的内容


@dataclass(frozen=True)
class Event:
    """触发这次运行的那条事件里，我们用到的几项。"""

    action: str
    sender: str
    issue_number: int
    run_id: int


def artifact_name(issue_number: int, digest: str) -> str:
    return f"repoaegis-issue-{issue_number}-{digest}"


def parse_artifact_name(name: str, issue_number: int) -> str | None:
    prefix = f"repoaegis-issue-{issue_number}-"
    if name.startswith(prefix) and re.fullmatch(r"[0-9a-f]{16}", name[len(prefix):]):
        return name[len(prefix):]
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # issue 被转移时接口返回 301，不跟过去，交给 check 判断
    def redirect_request(self, *args, **kwargs):
        return None


class GitHub:
    """GitHub 接口客户端。服务器出错或网络出错时重试，其他状态码原样返回。"""

    def __init__(self, token: str, api_url: str = "https://api.github.com", retries: int = 3,
                 graphql_url: str | None = None):
        self._token = token
        self._api_url = api_url.rstrip("/")
        # 工作流里有 GITHUB_GRAPHQL_URL；自建的 GitHub 服务器上它不是 api_url + /graphql
        self._graphql_url = graphql_url or self._api_url + "/graphql"
        self._retries = retries
        self._opener = urllib.request.build_opener(_NoRedirect)

    def get(self, path_or_url: str) -> Response:
        return self._request("GET", path_or_url)

    def post(self, path: str) -> Response:
        return self._request("POST", path)

    def delete(self, path: str) -> Response:
        return self._request("DELETE", path)

    def graphql(self, query: str, variables: dict | None = None) -> Response:
        """调 GraphQL 接口（只用来查询）。查询本身出错时 HTTP 状态仍是 200，错误在返回内容的 errors 里。"""
        return self._request("POST", self._graphql_url, {"query": query, "variables": variables or {}})

    def _request(self, method: str, path_or_url: str, payload: dict | None = None) -> Response:
        url = path_or_url if path_or_url.startswith("http") else self._api_url + path_or_url
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "RepoAegis",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
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


def _pages(github: GitHub, url: str, what: str):
    while url:
        page = github.get(url)
        _expect_ok(page, what)
        yield page.data
        url = page.next_url


class Repo:
    """一个仓库上我们要做的几件事，每件事一个方法。"""

    def __init__(self, github: GitHub, repo: str, sleep=time.sleep,
                 wait_timeout: int = WAIT_TIMEOUT_SECONDS, wait_interval: int = WAIT_INTERVAL_SECONDS):
        self._github = github
        self._repo = repo
        self._sleep = sleep
        self._wait_timeout = wait_timeout
        self._wait_interval = wait_interval

    def issue(self, number: int) -> Response:
        return self._github.get(f"/repos/{self._repo}/issues/{number}")

    def is_allowed(self, login: str) -> tuple[bool, str]:
        """这个人在仓库里有没有 Write 以上的权限；返回 (是否允许, 角色名)。"""
        response = self._github.get(f"/repos/{self._repo}/collaborators/{urllib.parse.quote(login)}/permission")
        if response.status == 404:
            return False, "不是协作者"
        _expect_ok(response, "读取协作者权限")
        return response.data["permission"] in ALLOWED_PERMISSIONS, response.data["role_name"]

    def last_labeling(self, number: int, label: str) -> Labeling | None:
        """最后一次给 issue 打上 label 的事件；找不到时返回 None。"""
        latest = None
        url = f"/repos/{self._repo}/issues/{number}/events?per_page=100"
        for events in _pages(self._github, url, "读取 issue 事件"):
            for event in events:
                if event["event"] != "labeled" or event["label"]["name"] != label:
                    continue
                if latest is None or (event["created_at"], event["id"]) > (latest["created_at"], latest["id"]):
                    latest = event
        if latest is None:
            return None
        return Labeling((latest.get("actor") or {}).get("login", ""), latest["created_at"])

    def active_runs(self, number: int, exclude_run_id: int) -> list[ActiveRun]:
        """这个 issue 正在跑的运行：产物名是 repoaegis-issue-编号-哈希 的那些。"""
        found = []
        for status in ACTIVE_RUN_STATUSES:
            url = f"/repos/{self._repo}/actions/runs?event=issues&status={status}&per_page=100"
            for page in _pages(self._github, url, "读取运行列表"):
                for run in page["workflow_runs"]:
                    if run["id"] == exclude_run_id:
                        continue
                    for digest in self._run_snapshots(run["id"], number):
                        found.append(ActiveRun(run["id"], digest))
        return found

    def _run_snapshots(self, run_id: int, number: int) -> list[str]:
        url = f"/repos/{self._repo}/actions/runs/{run_id}/artifacts?per_page=100"
        digests = []
        for page in _pages(self._github, url, "读取运行产物"):
            for artifact in page["artifacts"]:
                digest = parse_artifact_name(artifact["name"], number)
                if digest and not artifact.get("expired"):
                    digests.append(digest)
        return digests

    def cancel(self, run_id: int) -> bool:
        """停掉一次运行；已经结束的返回 False。"""
        response = self._github.post(f"/repos/{self._repo}/actions/runs/{run_id}/cancel")
        if response.status == 202:
            return True
        if response.status == 409:
            return False
        raise RuntimeError(f"取消运行 {run_id} 失败：HTTP {response.status}")

    def wait_finished(self, run_ids: list[int]) -> list[int]:
        """等这些运行完全结束；返回等到超时仍没结束的。

        取消只是"通知收到了"：运行还要跑完被取消后仍会执行的步骤（上传暂停存档、收尾），
        状态才变成 completed。等它结束再往下判断，才能看到它留下的存档。
        """
        waiting = list(run_ids)
        for _ in range(self._wait_timeout // self._wait_interval):
            waiting = [run_id for run_id in waiting if not self._finished(run_id)]
            if not waiting:
                break
            self._sleep(self._wait_interval)
        return waiting

    def _finished(self, run_id: int) -> bool:
        response = self._github.get(f"/repos/{self._repo}/actions/runs/{run_id}")
        _expect_ok(response, f"读取运行 {run_id}")
        return response.data["status"] == "completed"

    def pause_checkpoints(self, number: int) -> list[PauseCheckpoint]:
        """这个 issue 还没被 GitHub 删掉的暂停存档。"""
        found = []
        url = f"/repos/{self._repo}/actions/artifacts?per_page=100"
        for page in _pages(self._github, url, "读取仓库产物"):
            for artifact in page["artifacts"]:
                parsed = parse_pause_name(artifact["name"], number)
                if parsed and not artifact.get("expired"):
                    digest, deadline = parsed
                    found.append(PauseCheckpoint(artifact["id"], artifact["workflow_run"]["id"],
                                                 digest, deadline, artifact["created_at"]))
        return found

    def delete_artifact(self, artifact_id: int) -> bool:
        """删除一个产物；已经不在的也算删掉。"""
        return self._github.delete(f"/repos/{self._repo}/actions/artifacts/{artifact_id}").status in (204, 404)


def check(repo: Repo, event: Event, label: str, inspect, now: datetime | None = None) -> Decision:
    """inspect(打标签的时间) 做内容检查，返回 content_check.Result（有 action、reason、kind、content、digest）。"""
    number = event.issue_number
    active = repo.active_runs(number, exclude_run_id=event.run_id)

    # 第一步：这条事件是谁做的。撤标签、关闭、删除、转移，只有有权限的人做的才算叫停
    stop_order = False
    if event.action in STOP_ACTIONS:
        allowed, role = repo.is_allowed(event.sender)
        if not allowed:
            return Decision("skip", f"{event.sender}（{role}）{_describe(event.action)}，没有权限，忽略")
        stop_order = True

    stopped = _stop_all(repo, active) if stop_order else Stopped()

    # 第二步：issue 现在的状态
    issue = repo.issue(number)
    if issue.status in (301, 404, 410):
        # 转移和删除只有有权限的人能做，所以不管这次是什么事件，正在跑的都该停
        if not stop_order:
            stopped = _stop_all(repo, active)
        return stopped.apply(Decision("skip", f"查不到 issue #{number}（已被转移、删除，或不存在）"))
    _expect_ok(issue, "读取 issue")
    if "pull_request" in issue.data:
        return stopped.apply(Decision("skip", f"#{number} 是 PR，不是 issue"))
    if issue.data["state"] != "open":
        return stopped.apply(Decision("skip", f"issue #{number} 已关闭"))
    labels = {item if isinstance(item, str) else item["name"] for item in issue.data["labels"]}
    if label not in labels:
        return stopped.apply(Decision("skip", f"issue #{number} 上已没有 {label} 标签"))

    # 第三步：标签是谁、什么时候打的。不用这条事件的 sender：事件可能乱序到达，那个人不一定是打标签的人
    labeling = repo.last_labeling(number, label)
    labeler = labeling.actor if labeling else ""
    allowed, role = repo.is_allowed(labeler) if labeler else (False, "")
    if not allowed:
        # 没权限的人打的标签不算数：有运行在跑就让它继续，没有就什么都不做
        if not labeler:
            reason = f"找不到给 issue #{number} 打上 {label} 的记录，无法确认触发者"
        else:
            reason = f"{labeler}（{role}）打的标签没有权限，忽略"
        if active and not stop_order:
            reason += "，已有运行继续"
        return stopped.apply(Decision("skip", reason, labeler))

    # 第四步：按打标签的时间做内容检查，整理出要交给 agent 的内容。
    # 先检查再比较：引用的那一层也算内容，不重新读一遍就不知道它变没变
    reason = f"{labeler}（{role}）打上了 {label}"
    inspected = inspect(labeling.created_at)
    if inspected.action != "start":
        # 最新一次打标签的那一版过不了检查，正在跑的处理的是旧版本，一并停掉
        if active and not stop_order:
            stopped = _stop_all(repo, active)
        return stopped.apply(Decision("stop", f"{reason}，但{inspected.reason}", labeler, kind=inspected.kind,
                                      labeled_at=labeling.created_at))
    digest = inspected.digest

    # 第五步：已有运行在跑：内容一致就让它继续，不一致就停掉重来
    if active and not stop_order:
        same = [run for run in active if run.content_hash == digest]
        if same:
            ids = "、".join(str(run.run_id) for run in same)
            return Decision("skip", f"运行 {ids} 正在处理相同内容的 issue #{number}，让它继续", labeler)
        stopped = _stop_all(repo, active)

    # 第六步：开工。有内容一致、没过截止时刻的暂停存档就接着做；内容不一致或过期的删掉
    resume, stale = _pick_pause_checkpoint(repo.pause_checkpoints(number), digest, now or datetime.now(UTC))
    deleted = tuple(cp.artifact_id for cp in stale if repo.delete_artifact(cp.artifact_id))
    if resume:
        reason += f"，从运行 {resume.run_id} 的暂停存档接着做"
    return stopped.apply(Decision("start", reason, labeler, digest, content=inspected.content,
                                  labeled_at=labeling.created_at, resume=resume, deleted=deleted))


@dataclass(frozen=True)
class Stopped:
    cancelled: tuple[int, ...] = ()
    unfinished: tuple[int, ...] = ()

    def apply(self, decision: Decision) -> Decision:
        return replace(decision, cancelled=self.cancelled, unfinished=self.unfinished)


def _stop_all(repo: Repo, active: list[ActiveRun]) -> Stopped:
    """停掉这些运行，并等它们完全结束（被取消的运行还要上传暂停存档、跑收尾）。"""
    cancelled = [run_id for run_id in sorted({run.run_id for run in active}) if repo.cancel(run_id)]
    unfinished = repo.wait_finished(cancelled) if cancelled else []
    return Stopped(tuple(cancelled), tuple(unfinished))


def _pick_pause_checkpoint(checkpoints: list[PauseCheckpoint], digest: str,
                           now: datetime) -> tuple[PauseCheckpoint | None, list[PauseCheckpoint]]:
    """挑出要接着做的那份（内容一致、没过截止时刻、最新的一份），其余都算过时。"""
    usable = sorted((cp for cp in checkpoints if cp.content_hash == digest and cp.deadline > now),
                    key=lambda cp: (cp.created_at, cp.artifact_id))
    resume = usable[-1] if usable else None
    return resume, [cp for cp in checkpoints if cp is not resume]


def _describe(action: str) -> str:
    return {"unlabeled": "撤掉了标签", "closed": "关闭了 issue",
            "deleted": "删除了 issue", "transferred": "转移了 issue"}[action]


def write_snapshot(path: str, event: Event, decision: Decision) -> None:
    snapshot = {
        "issue": event.issue_number,
        "content_hash": decision.content_hash,
        "labeler": decision.labeler,
        "labeled_at": decision.labeled_at,
        "resume_run_id": decision.resume.run_id if decision.resume else None,
        "resume_artifact_id": decision.resume.artifact_id if decision.resume else None,
        "event_action": event.action,
        "run_id": event.run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8") as out:
        json.dump(snapshot, out, ensure_ascii=False, indent=2)


def write_content(path: str, decision: Decision) -> None:
    with open(path, "w", encoding="utf-8") as out:
        json.dump(decision.content, out, ensure_ascii=False, indent=2)


def write_outputs(path: str, decision: Decision, event: Event) -> None:
    # GITHUB_OUTPUT 每行一个 名字=值，值里不能有换行
    values = (
        ("action", decision.action),
        ("reason", decision.reason),
        ("kind", decision.kind),
        ("labeler", decision.labeler),
        ("content_hash", decision.content_hash),
        ("artifact_name", artifact_name(event.issue_number, decision.content_hash) if decision.action == "start" else ""),
        ("cancelled", " ".join(str(run_id) for run_id in decision.cancelled)),
        ("labeled_at", decision.labeled_at),
        ("resume_run_id", str(decision.resume.run_id) if decision.resume else ""),
        ("resume_artifact_id", str(decision.resume.artifact_id) if decision.resume else ""),
    )
    with open(path, "a", encoding="utf-8") as out:
        for name, value in values:
            out.write(f"{name}={value.replace(chr(10), ' ')}\n")


def event_from_env(env=os.environ) -> Event:
    return Event(
        action=env["EVENT_ACTION"],
        sender=env["SENDER"],
        issue_number=int(env["ISSUE_NUMBER"]),
        run_id=int(env["GITHUB_RUN_ID"]),
    )


def main() -> None:
    # 内容检查要用这里的接口客户端，放在函数里引用，两个模块才不会在加载时互相引用
    from repoaegis.content_check import Source, check_content

    github = GitHub(os.environ["GITHUB_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                    graphql_url=os.environ.get("GITHUB_GRAPHQL_URL"))
    repo = Repo(github, os.environ["GITHUB_REPOSITORY"])
    source = Source(github, os.environ["GITHUB_REPOSITORY"])
    event = event_from_env()
    decision = check(repo, event, os.environ["LABEL"],
                     lambda labeled_at: check_content(source, event.issue_number, labeled_at))
    if decision.cancelled:
        print("已停掉运行：" + "、".join(str(run_id) for run_id in decision.cancelled))
    if decision.unfinished:
        # 等到超时仍没结束：它的暂停存档可能还没传上来，这次就找不到，只能从头做
        print("::warning::这些运行停掉后等到超时仍没结束：" + "、".join(str(run_id) for run_id in decision.unfinished))
    if decision.deleted:
        print("已删掉过时的暂停存档：" + "、".join(str(artifact_id) for artifact_id in decision.deleted))
    print(f"{decision.action}: {decision.reason}")
    if decision.action == "start":
        write_snapshot(os.environ["SNAPSHOT_PATH"], event, decision)
        write_content(os.environ["CONTENT_PATH"], decision)
    write_outputs(os.environ["GITHUB_OUTPUT"], decision, event)


if __name__ == "__main__":
    main()
