"""开工前的内容检查：打标签那一刻的版本是谁改的、正文里有没有图片和附件、引用了哪些 issue。

开工前检查确认打标签的人有权限之后调用 check_content，只用标准库。整理出的内容算一个指纹，
开工前检查拿它和正在跑的运行、暂停存档比：引用的那一层也算在内容里（2026-10-30 定）。规则见 docs/什么样的issue适合交给agent.md 第六节（2026-10-26、2026-10-28 定）：

- 打标签之前最后一次改标题、最后一次改正文的人，有一个没有写权限（Write、Maintain、Admin），就不开工。
  只发过、没改过不算修改：issue 是谁发的不管，打标签的人已经替这一版担保。
  程序提醒过之后维护者重新打标签，表示认可这一版：提醒留言在那次修改之后、本次打标签之前，就开工。
- 正文里有图片、视频或上传的文件，就不开工。
- 正文直接引用的本仓库 issue 读一层，读打标签那一刻的版本。那一版的标题或正文由没有写权限的人
  写或改的，只列编号和原因，不读，也不往下查。读了的 issue 里又引用了本仓库别的 issue
  （指回原 issue 的、原 issue 也直接引用了的不算），就不开工。PR、讨论区帖子、其他仓库的不读，也不算层数。
- 被引用的最多读 10 篇、合计约 4,000 token，其余只列编号和标题；里面的图片和附件换成一句说明。

图片和引用都只认代码以外的文字（代码块、行内代码里的不算），而且是在清洗过的正文上找（HTML 注释已删）。
被打标签的 issue 也用修改记录倒推出的那一版，不用触发事件里带的：事件可能早于最后一次打标签，
内容和时间就会对不上。通过时把清洗后的标题、正文和读到的引用交给后面的步骤；
不通过时写明原因，由收尾步骤留言、撤标签。
"""

import hashlib
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime

from repoaegis.precheck import GitHub, Repo, _expect_ok
from repoaegis.sanitize import apply_to_non_code_regions, sanitize_body, sanitize_title

MAX_REFERENCES = 10
# 合计长度按 UTF-8 字节数算：英文大约 4 个字母一个 token，16,000 字节约 4,000 token
MAX_REFERENCE_BYTES = 16_000
# 程序发"修改人没有权限"提醒时用的账号和留言里的标记。只认这个账号发的，别人把标记抄进评论不算。
# 账号取决于收尾步骤用什么身份发留言（GITHUB_TOKEN 是这个账号；改用 GitHub App 时要跟着改）
NOTICE_AUTHOR = "github-actions[bot]"
NOTICE_MARKER = "<!-- repoaegis:editor-notice -->"
# 一次 GraphQL 查询里最多查几个编号
BATCH = 20


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text)


# ---------------------------------------------------------------------------
# 某一刻的版本
#
# 正文的修改记录（userContentEdits）每条存着那一版正文的全文；最早的一条是发出时的原文，
# 时间等于创建时间，不算修改。改标题不在这里，在改标题事件（RenamedTitleEvent）里，带改前和改后的标题。
# 2026-10-28 在 astral-sh/uv 的 issue 上实测过这两点。
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Change:
    """一次修改：谁、什么时候。login 为空表示账号已注销。"""

    login: str
    at: str


@dataclass(frozen=True)
class Issue:
    """查到的一个 issue，带全部修改记录。"""

    number: int
    state: str
    title: str
    body: str
    author: str
    created_at: str
    edits: tuple[tuple[str, str, str | None], ...] = ()  # (修改时间, 修改人, 这一版正文)；正文被删掉时为 None
    renames: tuple[tuple[str, str, str, str], ...] = ()  # (时间, 修改人, 改前标题, 改后标题)


@dataclass(frozen=True)
class Version:
    """一个 issue 在某一刻的样子。"""

    title: str
    body: str | None  # None：这一版的正文从修改记录里删掉了，读不到
    title_change: Change | None  # 这一刻之前最后一次改标题；没改过为 None
    body_change: Change | None


def version_at(issue: Issue, moment: str) -> Version | None:
    """issue 在 moment 那一刻的版本；那时还没发出来就返回 None。

    时间只精确到秒，和 moment 同一秒的修改算在之前：分不清先后时按"改过"处理，最多多拦一次。
    """
    t = _time(moment)
    if _time(issue.created_at) > t:
        return None

    edits = sorted(issue.edits, key=lambda edit: _time(edit[0]))
    before = [edit for edit in edits if _time(edit[0]) <= t]
    if not edits:
        body, body_change = issue.body, None
    elif not before:
        # 有修改记录，却连发出时的原文都在 moment 之后：记录不完整，这一版读不到
        body, body_change = None, None
    else:
        at, login, text = before[-1]
        original = len(before) == 1 and _time(at) == _time(issue.created_at)
        body, body_change = text, None if original else Change(login, at)

    renames = sorted(issue.renames, key=lambda rename: _time(rename[0]))
    done = [rename for rename in renames if _time(rename[0]) <= t]
    if done:
        at, login, _, current = done[-1]
        title, title_change = current, Change(login, at)
    elif renames:
        title, title_change = renames[0][2], None
    else:
        title, title_change = issue.title, None
    return Version(title, body, title_change, body_change)


# ---------------------------------------------------------------------------
# 图片和附件
#
# 识别规则沿用 2026-10-24 统计图片频率时的那套（docs/调研/issue附件与图片频率.md 第二节），
# 在 348 条样本上没有漏检。不读渲染后的网页：上传的视频在原文里只是一条裸链接，和图片分不清，
# 统一记作"图片或视频"，反正两种都不开工。另加了 ![说明][名字] 这种引用式图片写法。
# ---------------------------------------------------------------------------

_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)")
_MD_IMAGE_REF = re.compile(r"!\[[^\]]*\]\[[^\]]*\]")
# 链接定义行 [名字]: 网址。它本身不显示，网址由用到它的 ![说明][名字] 算一次，不再当裸链接算
_LINK_DEFINITION = re.compile(r"^ {0,3}\[[^\]]+\]:[ \t]*\S.*$", re.M)
_HTML_IMG = re.compile(r"<img\b[^>]*>", re.I)
_HTML_VIDEO = re.compile(r"<video\b[^>]*>(?:.*?</video\s*>)?|<source\b[^>]*\bsrc\s*=[^>]*>", re.I | re.S)
_SRC = re.compile(r"\bsrc\s*=\s*[\"']?([^\"'\s>]+)", re.I)
_URL = re.compile(r"https?://[^\s<>\")\]]+")
_UPLOADED_ASSET = re.compile(r"https://github\.com/user-attachments/assets/[0-9a-fA-F-]{20,}")
_OLD_ASSET = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/assets/\d+/[0-9a-fA-F-]{20,}")
_UPLOADED_FILE = re.compile(r"https://github\.com/(?:user-attachments|[\w.-]+/[\w.-]+)/files/\d+/([^\s)\]\"'<>]+)")
_OLD_IMAGE_HOST = re.compile(r"https://(?:private-)?user-images\.githubusercontent\.com/")

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".heic", ".tiff"}
# 徽章、状态图标、头像、各种工具自动插的按钮图，不是人贴的图
_NOISE = re.compile(r"shields\.io|/badge|badge\.|badgen|codecov|vercel\.com|vercel\.sh|avatars\.githubusercontent|"
                    r"deepwiki|coderabbit|gitpod\.io/button|camo\.githubusercontent|github\.com/[^/]+\.png|"
                    r"readthedocs|pkg\.pr\.new|netlify|/actions/workflows/.*\.svg|emoji|stackblitz|"
                    r"cursor\.com/assets/images/open-in|cubic\.dev/buttons|static\.devin\.ai/assets|"
                    r"codesandbox\.io/static/img|^data:|^\\", re.I)

# 被引用的 issue 里的图片和附件换成这几句（给模型看，用英文）
_PLACEHOLDER = {
    "image": "[image omitted]",
    "video": "[video omitted]",
    "image_or_video": "[image or video omitted]",
    "file": "[attached file omitted: {name}]",
}
_KIND_NAME = {"image": "图片", "video": "视频", "image_or_video": "图片或视频", "file": "上传的文件"}


@dataclass(frozen=True)
class Media:
    kind: str  # image、video、image_or_video（裸的上传链接）、file（上传的文件）
    text: str  # 原文里的那一段
    name: str = ""  # 上传文件的文件名


def _ext(url: str) -> str:
    return os.path.splitext(urllib.parse.unquote(urllib.parse.urlparse(url).path))[1].lower()


def _classify_url(url: str) -> tuple[str, str]:
    """一条裸链接是不是图片、视频或上传的文件；返回 (种类, 文件名)，都不是时种类为空。"""
    uploaded = _UPLOADED_FILE.match(url)
    if uploaded:
        return "file", urllib.parse.unquote(uploaded.group(1))
    if _UPLOADED_ASSET.match(url) or _OLD_ASSET.match(url):
        return "image_or_video", ""
    ext = _ext(url)
    if _OLD_IMAGE_HOST.match(url):
        return ("video" if ext in VIDEO_EXT else "image"), ""
    if ext in VIDEO_EXT:
        return "video", ""
    if ext in IMAGE_EXT and not _NOISE.search(url) and "github.com" not in urllib.parse.urlparse(url).netloc:
        return "image", ""
    return "", ""


def _scan(text: str) -> list[tuple[int, int, Media]]:
    """在一段普通文字（不含代码）里找图片和附件，返回 [(起点, 终点, 找到的), ...]，按位置排好、互不重叠。"""
    found = []
    taken = []  # 已经认过的段落，包括被当作徽章跳过的，免得里面的链接再被当成裸链接

    def free(start, end):
        return all(end <= s or e <= start for s, e in taken)

    taken.extend(match.span() for match in _LINK_DEFINITION.finditer(text))
    for pattern in (_HTML_VIDEO, _HTML_IMG, _MD_IMAGE, _MD_IMAGE_REF):
        for match in pattern.finditer(text):
            if not free(*match.span()):
                continue
            taken.append(match.span())
            if pattern is _HTML_VIDEO:
                kind = "video"
            elif pattern is _MD_IMAGE_REF:
                kind = "image"
            else:
                src = match.group(1) if pattern is _MD_IMAGE else (_SRC.search(match.group(0)) or [None, ""])[1]
                if src and _NOISE.search(src):
                    continue
                kind = "video" if _ext(src) in VIDEO_EXT else "image"
            found.append((match.start(), match.end(), Media(kind, match.group(0))))

    for match in _URL.finditer(text):
        url = match.group(0).rstrip(".,;:")
        start, end = match.start(), match.start() + len(url)
        if not free(start, end):
            continue
        kind, name = _classify_url(url)
        if kind:
            found.append((start, end, Media(kind, url, name)))
    return sorted(found, key=lambda item: item[0])


def find_media(body: str) -> list[Media]:
    """正文里代码以外的图片、视频和上传的文件。"""
    found = []

    def collect(segment):
        found.extend(media for _, _, media in _scan(segment))
        return segment

    apply_to_non_code_regions(body, collect)
    return found


def omit_media(body: str) -> str:
    """把代码以外的图片、视频和上传的文件换成一句说明。"""
    def replace(segment):
        parts, position = [], 0
        for start, end, media in _scan(segment):
            parts.append(segment[position:start])
            parts.append(_PLACEHOLDER[media.kind].format(name=media.name))
            position = end
        parts.append(segment[position:])
        return "".join(parts)

    return apply_to_non_code_regions(body, replace)


def _describe_media(media: list[Media]) -> str:
    parts = []
    for kind in ("image", "image_or_video", "video", "file"):
        items = [item for item in media if item.kind == kind]
        if not items:
            continue
        text = f"{_KIND_NAME[kind]} {len(items)} 处"
        if kind == "file":
            text += "（" + "、".join(item.name for item in items) + "）"
        parts.append(text)
    return "、".join(parts)


# ---------------------------------------------------------------------------
# 引用
#
# 按 GitHub 自动转成链接的四种写法认（github/docs 的 autolinked-references-and-urls.md
# "Issues and pull requests" 一节）：完整网址、#编号、GH-编号、用户名/仓库#编号。只用规则，不用模型。
# ---------------------------------------------------------------------------

_REFERENCE = re.compile(
    r"(?i:https?://github\.com/(?P<url_owner>[\w.-]+)/(?P<url_repo>[\w.-]+)"
    r"/(?P<url_kind>issues|pull|discussions)/(?P<url_number>\d+))"
    r"|(?<![\w/.-])(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#(?P<number>\d+)\b"
    r"|(?<![\w/#&])(?:#|GH-)(?P<local>\d+)\b"
)


@dataclass(frozen=True)
class Reference:
    number: int
    kind: str  # number：#12 这类，查过才知道是 issue 还是 PR；pull：网址写明是 PR；discussion：讨论区帖子
    repo: str = ""  # 其他仓库的写成 owner/repo；本仓库的为空

    @property
    def name(self) -> str:
        return f"{self.repo}#{self.number}"


def find_references(body: str, repo: str) -> list[Reference]:
    """正文里代码以外的引用，按第一次出现的顺序，同一个编号只留一次。repo 是本仓库 owner/repo。"""
    found = {}

    def collect(segment):
        for match in _REFERENCE.finditer(segment):
            if match.group("local"):
                ref = Reference(int(match.group("local")), "number")
            else:
                if match.group("url_number"):
                    owner, name = match.group("url_owner"), match.group("url_repo")
                    kind = {"issues": "number", "pull": "pull", "discussions": "discussion"}[match.group("url_kind").lower()]
                    number = match.group("url_number")
                else:
                    owner, name, kind, number = match.group("owner"), match.group("repo"), "number", match.group("number")
                other = f"{owner}/{name}"
                ref = Reference(int(number), kind, "" if other.lower() == repo.lower() else other)
            found.setdefault((ref.repo.lower(), ref.number), ref)
        return segment

    apply_to_non_code_regions(body, collect)
    return list(found.values())


# ---------------------------------------------------------------------------
# 向 GitHub 查
# ---------------------------------------------------------------------------

_EDITS = "pageInfo { hasNextPage endCursor } nodes { editedAt editor { login } diff }"
_RENAMES = ("pageInfo { hasNextPage endCursor } "
            "nodes { ... on RenamedTitleEvent { createdAt actor { login } previousTitle currentTitle } }")
_ISSUE_FIELDS = (f"number state title body createdAt author {{ login }} "
                 f"userContentEdits(first: 100) {{ {_EDITS} }} "
                 f"timelineItems(itemTypes: [RENAMED_TITLE_EVENT], first: 100) {{ {_RENAMES} }}")
_MORE_EDITS = (f"query($owner: String!, $name: String!, $number: Int!, $after: String!) "
               f"{{ repository(owner: $owner, name: $name) {{ issue(number: $number) "
               f"{{ userContentEdits(first: 100, after: $after) {{ {_EDITS} }} }} }} }}")
_MORE_RENAMES = (f"query($owner: String!, $name: String!, $number: Int!, $after: String!) "
                 f"{{ repository(owner: $owner, name: $name) {{ issue(number: $number) "
                 f"{{ timelineItems(itemTypes: [RENAMED_TITLE_EVENT], first: 100, after: $after) {{ {_RENAMES} }} }} }} }}")


def _login(account: dict | None) -> str:
    return (account or {}).get("login", "")


class Source:
    """内容检查要向 GitHub 查的几件事，每件事一个方法。"""

    def __init__(self, github: GitHub, repo: str):
        self.repo = repo
        self._github = github
        self._owner, self._name = repo.split("/", 1)
        self._repo = Repo(github, repo)
        self._allowed = {}

    def issues(self, numbers: list[int]) -> dict[int, Issue | str | None]:
        """按编号查本仓库：issue 返回带全部修改记录的 Issue，PR 返回 "PullRequest"，查不到返回 None。"""
        return self._batch(numbers, f"__typename ... on Issue {{ {_ISSUE_FIELDS} }}")

    def kinds(self, numbers: list[int]) -> dict[int, str | None]:
        """按编号查本仓库：返回 "Issue"、"PullRequest"，查不到返回 None。"""
        found = self._batch(numbers, "__typename")
        return {number: "Issue" if isinstance(item, Issue) else item for number, item in found.items()}

    def is_allowed(self, login: str) -> bool:
        """有没有 Write 以上的权限；已注销的账号算没有。同一个人只查一次。"""
        if not login:
            return False
        if login not in self._allowed:
            self._allowed[login] = self._repo.is_allowed(login)[0]
        return self._allowed[login]

    def notified(self, number: int, after: str, until: str) -> bool:
        """after 之后、until 之前（含）有没有程序发过"修改人没有权限"的提醒。"""
        url = f"/repos/{self.repo}/issues/{number}/comments?since={urllib.parse.quote(after)}&per_page=100"
        while url:
            page = self._github.get(url)
            _expect_ok(page, "读取 issue 评论")
            for comment in page.data:
                if (_login(comment.get("user")) == NOTICE_AUTHOR and NOTICE_MARKER in (comment.get("body") or "")
                        and _time(after) < _time(comment["created_at"]) <= _time(until)):
                    return True
            url = page.next_url
        return False

    def _batch(self, numbers: list[int], fields: str) -> dict[int, Issue | str | None]:
        found = {}
        numbers = list(dict.fromkeys(numbers))
        for i in range(0, len(numbers), BATCH):
            chunk = numbers[i:i + BATCH]
            parts = " ".join(f"n{number}: issueOrPullRequest(number: {number}) {{ {fields} }}" for number in chunk)
            data = self._query(f"query($owner: String!, $name: String!) "
                               f"{{ repository(owner: $owner, name: $name) {{ {parts} }} }}")
            for number in chunk:
                node = data["repository"].get(f"n{number}")
                if node is None:
                    found[number] = None
                elif node["__typename"] == "Issue":
                    found[number] = self._complete(node) if "userContentEdits" in node else Issue(number, "", "", "", "", "")
                else:
                    found[number] = node["__typename"]
        return found

    def _complete(self, node: dict) -> Issue:
        """修改记录超过一页时接着往后翻，拿全。"""
        number = node["number"]
        edits, page = list(node["userContentEdits"]["nodes"]), node["userContentEdits"]["pageInfo"]
        while page["hasNextPage"]:
            more = self._query(_MORE_EDITS, number=number, after=page["endCursor"])["repository"]["issue"]["userContentEdits"]
            edits, page = edits + more["nodes"], more["pageInfo"]
        renames, page = list(node["timelineItems"]["nodes"]), node["timelineItems"]["pageInfo"]
        while page["hasNextPage"]:
            more = self._query(_MORE_RENAMES, number=number, after=page["endCursor"])["repository"]["issue"]["timelineItems"]
            renames, page = renames + more["nodes"], more["pageInfo"]
        return Issue(
            number=number, state=node["state"].lower(), title=node["title"], body=node["body"] or "",
            author=_login(node["author"]), created_at=node["createdAt"],
            edits=tuple((edit["editedAt"], _login(edit["editor"]), edit["diff"]) for edit in edits),
            renames=tuple((item["createdAt"], _login(item["actor"]), item["previousTitle"], item["currentTitle"])
                          for item in renames if item),
        )

    def _query(self, query: str, **variables) -> dict:
        response = self._github.graphql(query, {"owner": self._owner, "name": self._name, **variables})
        _expect_ok(response, "GraphQL 查询")
        # 编号不存在（或是讨论区帖子）时，那一项为 null，另报一条 NOT_FOUND，不算出错
        errors = [error for error in response.data.get("errors") or [] if error.get("type") != "NOT_FOUND"]
        if errors or not response.data.get("data"):
            raise RuntimeError(f"GraphQL 查询出错：{errors or response.data}")
        return response.data["data"]


# ---------------------------------------------------------------------------
# 检查
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    action: str  # start：开工；stop：不开工，由收尾步骤留言、撤标签
    reason: str
    kind: str = ""  # 不开工时是哪一类：editor（修改人没有权限）、media（图片或附件）、nested（引用多于一层）
    content: dict | None = None  # 开工时交给后面步骤的内容

    @property
    def digest(self) -> str:
        return content_hash(self.content) if self.content is not None else ""


def content_hash(content: dict) -> str:
    """内容的指纹：标题、正文、每个引用（读了的连同全文，没读的编号和原因）。

    version 里是打标签的时间这类记录，不算：不然每次重新打标签，指纹都不一样。
    """
    seen = {key: value for key, value in content.items() if key != "version"}
    text = json.dumps(seen, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _who(login: str) -> str:
    return login or "已注销的账号"


def _change(change: Change | None) -> dict | None:
    return {"by": change.login, "at": change.at} if change else None


def check_content(source: Source, number: int, labeled_at: str) -> Result:
    """number 是被打标签的 issue，labeled_at 是最后一次打标签的时间。"""
    # 第一步：打标签那一刻的版本，最后是谁改的
    root = source.issues([number])[number]
    if not isinstance(root, Issue):
        raise RuntimeError(f"查不到 issue #{number}")
    version = version_at(root, labeled_at)
    if version is None:
        raise RuntimeError(f"issue #{number} 的创建时间晚于打标签时间 {labeled_at}")
    if version.body is None:
        raise RuntimeError(f"issue #{number} 打标签那一刻的正文已从修改记录里删掉，读不到")
    changes = [("改标题", version.title_change), ("改正文", version.body_change)]
    denied = [(what, change) for what, change in changes if change and not source.is_allowed(change.login)]
    accepted = False
    if denied:
        latest = max((change for _, change in denied), key=lambda change: _time(change.at))
        if not source.notified(number, latest.at, labeled_at):
            who = "；".join(f"{what}：{_who(change.login)}，{change.at}" for what, change in denied)
            return Result("stop", f"打标签之前最后一次修改是没有写权限的人做的（{who}），"
                                  f"请维护者看过最新版后重新打标签", "editor")
        accepted = True

    # 第二步：图片和附件
    clean_title, clean_body = sanitize_title(version.title), sanitize_body(version.body)
    media = find_media(clean_body)
    if media:
        return Result("stop", f"正文里有{_describe_media(media)}，暂不处理带图片或附件的 issue", "media")

    # 第三步：读直接引用的 issue
    references = [ref for ref in find_references(clean_body, source.repo) if ref.name != f"#{number}"]
    lookup = [ref.number for ref in references if not ref.repo and ref.kind == "number"]
    found = source.issues(lookup) if lookup else {}
    entries, read, used = [], [], 0
    for ref in references:
        entry = {"ref": ref.name, "read": False}
        entries.append(entry)
        if ref.repo:
            entry["skipped"] = "other_repo"
            continue
        if ref.kind != "number":
            entry["skipped"] = {"pull": "pull_request", "discussion": "discussion"}[ref.kind]
            continue
        item = found[ref.number]
        if item is None:
            entry["skipped"] = "not_found"  # 已删除、已转移，或是讨论区帖子
            continue
        if not isinstance(item, Issue):
            entry["skipped"] = "pull_request"
            continue
        ref_version = version_at(item, labeled_at)
        if ref_version is None:
            entry["skipped"] = "created_after_label"
            continue
        # 被引用的没人替它担保，所以发出的人也要查：标题、正文没改过的，看作者
        writers = [change.login if change else item.author
                   for change in (ref_version.title_change, ref_version.body_change)]
        untrusted = sorted({_who(login) for login in writers if not source.is_allowed(login)})
        if untrusted:
            entry.update(skipped="untrusted_writer", writers=untrusted)
            continue
        if ref_version.body is None:
            entry["skipped"] = "body_deleted"
            continue
        ref_title = sanitize_title(ref_version.title)
        ref_body = omit_media(sanitize_body(ref_version.body))
        size = len((ref_title + ref_body).encode("utf-8"))
        if len(read) >= MAX_REFERENCES or used + size > MAX_REFERENCE_BYTES:
            entry.update(skipped="over_limit", title=ref_title)
            continue
        used += size
        # state 是现在的状态（open、closed），不是打标签那一刻的
        entry.update(read=True, title=ref_title, state=item.state, body=ref_body)
        read.append((ref.number, ref_body))

    # 第四步：读了的里面有没有再引用别的 issue
    known = {number, *lookup}
    deeper = {}
    for ref_number, ref_body in read:
        for inner in find_references(ref_body, source.repo):
            if not inner.repo and inner.kind == "number" and inner.number not in known:
                deeper.setdefault(inner.number, ref_number)
    if deeper:
        kinds = source.kinds(list(deeper))
        nested = [(deeper[inner], inner) for inner in deeper if kinds.get(inner) == "Issue"]
        if nested:
            pairs = "、".join(f"#{outer} 引用了 #{inner}" for outer, inner in nested)
            return Result("stop", f"引用多于一层（{pairs}），只处理凭本 issue 和它直接引用的 issue 就能看懂的任务",
                          "nested")

    content = {
        "issue": number,
        "title": clean_title,
        "body": clean_body,
        "version": {
            "labeled_at": labeled_at,
            "title_changed": _change(version.title_change),
            "body_changed": _change(version.body_change),
            # 修改人没有写权限，但程序提醒之后维护者重新打了标签
            "accepted_after_notice": accepted,
        },
        "references": entries,
    }
    reason = f"内容检查通过：引用 {len(entries)} 处，读了 {len(read)} 篇"
    if accepted:
        reason += "；修改人没有写权限，提醒后维护者重新打了标签"
    return Result("start", reason, content=content)
