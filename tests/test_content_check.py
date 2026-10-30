import re
import unittest

from repoaegis import content_check
from repoaegis.content_check import (NOTICE_AUTHOR, NOTICE_MARKER, Change, Issue, Media, Reference, Source,
                                     check_content, content_hash, find_media, find_references, omit_media,
                                     version_at)
from repoaegis.precheck import Response

REPO = "octo/demo"
LABELED_AT = "2026-10-25T10:00:00Z"
TITLE, BODY = "parse_date 遇到空字符串时崩溃", "调用 parse_date('') 抛 ValueError"
MAINTAINER, OUTSIDER = "maint", "stranger"


def issue(number, body="", title="标题", author=MAINTAINER, created_at="2026-10-20T08:00:00Z",
          edits=(), renames=(), state="open"):
    return Issue(number, state, title, body, author, created_at, tuple(edits), tuple(renames))


class FakeSource:
    """代替 Source：编号对应事先准备好的 Issue 或 "PullRequest"；提醒留言只给出时间。"""

    repo = REPO

    def __init__(self, issues, allowed=(MAINTAINER,), notices=()):
        self._issues = issues
        self._allowed = set(allowed)
        self._notices = notices
        self.kind_queries = []

    def issues(self, numbers):
        return {number: self._issues.get(number) for number in numbers}

    def kinds(self, numbers):
        self.kind_queries.append(list(numbers))
        return {number: "Issue" if isinstance(self._issues.get(number), Issue) else self._issues.get(number)
                for number in numbers}

    def is_allowed(self, login):
        return login in self._allowed

    def notified(self, number, after, until):
        return any(content_check._time(after) < content_check._time(at) <= content_check._time(until)
                   for at in self._notices)


class VersionAtTest(unittest.TestCase):
    def test_never_edited(self):
        version = version_at(issue(8, "原文", author=OUTSIDER), LABELED_AT)
        self.assertEqual(version.body, "原文")
        self.assertIsNone(version.body_change)
        self.assertIsNone(version.title_change)

    def test_original_entry_is_not_a_change(self):
        # 改过一次之后，修改记录里有两条：最早一条是发出时的原文，时间等于创建时间
        item = issue(8, "第二版", created_at="2026-10-20T08:00:00Z", edits=[
            ("2026-10-26T09:00:00Z", OUTSIDER, "第二版"),
            ("2026-10-20T08:00:00Z", OUTSIDER, "原文"),
        ])
        version = version_at(item, LABELED_AT)
        self.assertEqual(version.body, "原文")
        self.assertIsNone(version.body_change)

    def test_last_edit_before_label(self):
        item = issue(8, "第三版", edits=[
            ("2026-10-26T09:00:00Z", MAINTAINER, "第三版"),
            ("2026-10-24T09:00:00Z", OUTSIDER, "第二版"),
            ("2026-10-20T08:00:00Z", MAINTAINER, "原文"),
        ])
        version = version_at(item, LABELED_AT)
        self.assertEqual(version.body, "第二版")
        self.assertEqual(version.body_change, Change(OUTSIDER, "2026-10-24T09:00:00Z"))

    def test_same_second_counts_as_before(self):
        item = issue(8, edits=[(LABELED_AT, OUTSIDER, "第二版"), ("2026-10-20T08:00:00Z", MAINTAINER, "原文")])
        self.assertEqual(version_at(item, LABELED_AT).body_change, Change(OUTSIDER, LABELED_AT))

    def test_title_renames(self):
        renames = [("2026-10-24T09:00:00Z", OUTSIDER, "旧标题", "中间标题"),
                   ("2026-10-26T09:00:00Z", MAINTAINER, "中间标题", "新标题")]
        version = version_at(issue(8, title="新标题", renames=renames), LABELED_AT)
        self.assertEqual(version.title, "中间标题")
        self.assertEqual(version.title_change, Change(OUTSIDER, "2026-10-24T09:00:00Z"))

    def test_title_renamed_only_after_label(self):
        renames = [("2026-10-26T09:00:00Z", OUTSIDER, "旧标题", "新标题")]
        version = version_at(issue(8, title="新标题", renames=renames), LABELED_AT)
        self.assertEqual(version.title, "旧标题")
        self.assertIsNone(version.title_change)

    def test_created_after_label(self):
        self.assertIsNone(version_at(issue(9, created_at="2026-10-26T00:00:00Z"), LABELED_AT))

    def test_deleted_revision(self):
        item = issue(8, edits=[("2026-10-24T09:00:00Z", MAINTAINER, None), ("2026-10-20T08:00:00Z", MAINTAINER, "原文")])
        self.assertIsNone(version_at(item, LABELED_AT).body)


class FindMediaTest(unittest.TestCase):
    def kinds(self, body):
        return [media.kind for media in find_media(body)]

    def test_markdown_and_html_images(self):
        body = "看图 ![截图](https://example.com/a.png) 和 <img src=\"https://example.com/b.jpg\" width=300>"
        self.assertEqual(self.kinds(body), ["image", "image"])

    def test_reference_style_image(self):
        self.assertEqual(self.kinds("![截图][1]\n\n[1]: https://example.com/a.png"), ["image"])

    def test_bare_upload_link_is_image_or_video(self):
        body = "录屏：\nhttps://github.com/user-attachments/assets/0f3c2a1b-1234-4c5d-9e8f-abcdef012345\n"
        self.assertEqual(self.kinds(body), ["image_or_video"])

    def test_uploaded_file(self):
        found = find_media("日志见 [crash.log](https://github.com/user-attachments/files/123/crash.log)")
        self.assertEqual(found, [Media("file", "https://github.com/user-attachments/files/123/crash.log", "crash.log")])

    def test_video_tag(self):
        self.assertEqual(self.kinds('<video src="https://example.com/a.mp4" controls></video>'), ["video"])

    def test_badges_are_ignored(self):
        body = "![CI](https://github.com/o/r/actions/workflows/ci.yml/badge.svg) ![](https://img.shields.io/pypi/v/x)"
        self.assertEqual(self.kinds(body), [])

    def test_code_is_ignored(self):
        body = "```markdown\n![例子](https://example.com/a.png)\n```\n行内 `![x](https://example.com/b.png)`"
        self.assertEqual(self.kinds(body), [])

    def test_paste_sites_and_plain_links_are_not_attachments(self):
        body = "完整输出 https://gist.github.com/someone/abc123 ，文档 https://docs.python.org/3/library/re.html"
        self.assertEqual(self.kinds(body), [])

    def test_omit_media(self):
        body = "前 ![x](https://example.com/a.png) 中 https://github.com/user-attachments/files/1/out.log 后\n`![留](u.png)`"
        self.assertEqual(omit_media(body),
                         "前 [image omitted] 中 [attached file omitted: out.log] 后\n`![留](u.png)`")


class FindReferencesTest(unittest.TestCase):
    def names(self, body):
        return [(ref.name, ref.kind) for ref in find_references(body, REPO)]

    def test_four_forms_in_order(self):
        body = ("见 #12，另见 GH-13、octo/demo#14、"
                "https://github.com/octo/demo/issues/15 和 https://github.com/other/lib/issues/3")
        self.assertEqual(self.names(body), [("#12", "number"), ("#13", "number"), ("#14", "number"),
                                            ("#15", "number"), ("other/lib#3", "number")])

    def test_pull_and_discussion_urls(self):
        body = "https://github.com/octo/demo/pull/20 https://github.com/Octo/Demo/discussions/21"
        self.assertEqual(self.names(body), [("#20", "pull"), ("#21", "discussion")])

    def test_not_references(self):
        body = "abc#12 &#13; 颜色 #fff 版本 v1#2\n```\n#14\n```\n`#15`"
        self.assertEqual(self.names(body), [])

    def test_duplicates_kept_once(self):
        self.assertEqual(self.names("#12 和 https://github.com/octo/demo/issues/12"), [("#12", "number")])

    def test_reference_fields(self):
        self.assertEqual(find_references("other/lib#3", REPO), [Reference(3, "number", "other/lib")])


class CheckContentTest(unittest.TestCase):
    def check(self, source):
        return check_content(source, 8, LABELED_AT)

    def test_clean_issue_starts(self):
        result = self.check(FakeSource({8: issue(8, BODY, author=OUTSIDER)}))
        self.assertEqual(result.action, "start")
        self.assertEqual(result.content["body"], BODY)
        self.assertEqual(result.content["references"], [])
        self.assertIsNone(result.content["version"]["body_changed"])

    def test_edit_by_outsider_stops(self):
        edits = [("2026-10-24T09:00:00Z", OUTSIDER, BODY), ("2026-10-20T08:00:00Z", MAINTAINER, "原文")]
        result = self.check(FakeSource({8: issue(8, BODY, edits=edits)}))
        self.assertEqual((result.action, result.kind), ("stop", "editor"))
        self.assertIn(OUTSIDER, result.reason)

    def test_title_change_by_outsider_stops(self):
        renames = [("2026-10-24T09:00:00Z", OUTSIDER, "旧", TITLE)]
        result = self.check(FakeSource({8: issue(8, BODY, title=TITLE, renames=renames)}))
        self.assertEqual(result.kind, "editor")
        self.assertIn("改标题", result.reason)

    def test_edit_by_maintainer_starts(self):
        edits = [("2026-10-24T09:00:00Z", MAINTAINER, BODY), ("2026-10-20T08:00:00Z", OUTSIDER, "原文")]
        result = self.check(FakeSource({8: issue(8, BODY, author=OUTSIDER, edits=edits)}))
        self.assertEqual(result.action, "start")
        self.assertEqual(result.content["version"]["body_changed"], {"by": MAINTAINER, "at": "2026-10-24T09:00:00Z"})

    def test_edit_after_label_is_ignored(self):
        edits = [("2026-10-26T09:00:00Z", OUTSIDER, "改过"), ("2026-10-20T08:00:00Z", MAINTAINER, BODY)]
        result = self.check(FakeSource({8: issue(8, "改过", edits=edits)}))
        self.assertEqual(result.action, "start")
        # 交出去的是打标签那一刻的那一版，不是现在的
        self.assertEqual(result.content["body"], BODY)

    def test_title_at_label_time(self):
        renames = [("2026-10-26T09:00:00Z", MAINTAINER, TITLE, "打标签后改的标题")]
        result = self.check(FakeSource({8: issue(8, BODY, title="打标签后改的标题", renames=renames)}))
        self.assertEqual(result.content["title"], TITLE)

    def test_deleted_root_revision_raises(self):
        edits = [("2026-10-24T09:00:00Z", MAINTAINER, None), ("2026-10-20T08:00:00Z", MAINTAINER, BODY)]
        with self.assertRaises(RuntimeError):
            self.check(FakeSource({8: issue(8, BODY, edits=edits)}))

    def test_relabel_after_notice_starts(self):
        edits = [("2026-10-24T09:00:00Z", OUTSIDER, BODY), ("2026-10-20T08:00:00Z", MAINTAINER, "原文")]
        source = FakeSource({8: issue(8, BODY, edits=edits)}, notices=["2026-10-24T09:30:00Z"])
        result = self.check(source)
        self.assertEqual(result.action, "start")
        self.assertTrue(result.content["version"]["accepted_after_notice"])

    def test_notice_before_the_edit_does_not_count(self):
        edits = [("2026-10-24T09:00:00Z", OUTSIDER, BODY), ("2026-10-20T08:00:00Z", MAINTAINER, "原文")]
        source = FakeSource({8: issue(8, BODY, edits=edits)}, notices=["2026-10-23T09:30:00Z"])
        self.assertEqual(self.check(source).kind, "editor")

    def test_deleted_account_counts_as_no_permission(self):
        edits = [("2026-10-24T09:00:00Z", "", BODY), ("2026-10-20T08:00:00Z", MAINTAINER, "原文")]
        result = self.check(FakeSource({8: issue(8, BODY, edits=edits)}))
        self.assertIn("已注销的账号", result.reason)

    def test_image_stops(self):
        body = BODY + "\n![截图](https://github.com/user-attachments/assets/0f3c2a1b-1234-4c5d-9e8f-abcdef012345)"
        result = self.check(FakeSource({8: issue(8, body)}))
        self.assertEqual((result.action, result.kind), ("stop", "media"))
        self.assertIn("图片 1 处", result.reason)

    def test_image_in_html_comment_is_ignored(self):
        body = BODY + "\n<!-- 在这里贴截图 ![](https://example.com/a.png) -->"
        result = self.check(FakeSource({8: issue(8, body)}))
        self.assertEqual(result.action, "start")
        self.assertEqual(result.content["body"], BODY + "\n")

    def test_references_read_and_skipped(self):
        body = "见 #12 #13 #14 #15 #8 other/lib#3 https://github.com/octo/demo/pull/16"
        source = FakeSource({
            8: issue(8, body),
            12: issue(12, "背景说明", title="相关问题", state="closed"),
            13: "PullRequest",
            14: issue(14, "外人写的", author=OUTSIDER),
            15: issue(15, "打标签后才发", created_at="2026-10-26T00:00:00Z"),
        })
        result = self.check(source)
        self.assertEqual(result.action, "start")
        self.assertEqual(result.content["references"], [
            {"ref": "#12", "read": True, "title": "相关问题", "state": "closed", "body": "背景说明"},
            {"ref": "#13", "read": False, "skipped": "pull_request"},
            {"ref": "#14", "read": False, "skipped": "untrusted_writer", "writers": [OUTSIDER]},
            {"ref": "#15", "read": False, "skipped": "created_after_label"},
            {"ref": "other/lib#3", "read": False, "skipped": "other_repo"},
            {"ref": "#16", "read": False, "skipped": "pull_request"},
        ])

    def test_reference_edited_by_maintainer_is_trusted(self):
        edits = [("2026-10-24T09:00:00Z", MAINTAINER, "整理过"), ("2026-10-20T08:00:00Z", OUTSIDER, "原文")]
        source = FakeSource({8: issue(8, "见 #12"), 12: issue(12, "整理过", author=OUTSIDER, edits=edits)})
        entry = self.check(source).content["references"][0]
        # 正文是维护者改的，但标题没改过，仍是外人写的
        self.assertEqual(entry["skipped"], "untrusted_writer")

    def test_reference_uses_version_at_label_time(self):
        edits = [("2026-10-26T09:00:00Z", MAINTAINER, "打标签后改的"), ("2026-10-20T08:00:00Z", MAINTAINER, "当时的")]
        source = FakeSource({8: issue(8, "见 #12"), 12: issue(12, "打标签后改的", edits=edits)})
        self.assertEqual(self.check(source).content["references"][0]["body"], "当时的")

    def test_reference_images_are_omitted(self):
        source = FakeSource({8: issue(8, "见 #12"), 12: issue(12, "如图 ![](https://example.com/a.png)")})
        self.assertEqual(self.check(source).content["references"][0]["body"], "如图 [image omitted]")

    def test_reference_limits(self):
        numbers = range(20, 32)
        body = " ".join(f"#{n}" for n in numbers)
        source = FakeSource({8: issue(8, body), **{n: issue(n, "短") for n in numbers}})
        entries = self.check(source).content["references"]
        self.assertEqual(sum(entry["read"] for entry in entries), 10)
        self.assertEqual(entries[10], {"ref": "#30", "read": False, "skipped": "over_limit", "title": "标题"})

    def test_reference_byte_limit(self):
        source = FakeSource({8: issue(8, "#12 #13"), 12: issue(12, "长" * 6000), 13: issue(13, "短")})
        entries = self.check(source).content["references"]
        self.assertEqual([entry["read"] for entry in entries], [False, True])

    def test_nested_issue_stops(self):
        source = FakeSource({8: issue(8, "见 #12"), 12: issue(12, "起因是 #30"), 30: issue(30)})
        result = self.check(source)
        self.assertEqual((result.action, result.kind), ("stop", "nested"))
        self.assertIn("#12 引用了 #30", result.reason)

    def test_nested_pr_back_reference_and_sibling_do_not_count(self):
        source = FakeSource({8: issue(8, "见 #12 #13"), 12: issue(12, "修复在 #30，回到 #8，另见 #13"),
                             13: issue(13), 30: "PullRequest"})
        result = self.check(source)
        self.assertEqual(result.action, "start")
        self.assertEqual(source.kind_queries, [[30]])

    def test_untrusted_reference_is_not_checked_for_nesting(self):
        source = FakeSource({8: issue(8, "见 #12"), 12: issue(12, "起因是 #30", author=OUTSIDER), 30: issue(30)})
        self.assertEqual(self.check(source).action, "start")
        self.assertEqual(source.kind_queries, [])

    def test_nested_in_other_repo_does_not_count(self):
        source = FakeSource({8: issue(8, "见 #12"), 12: issue(12, "上游 other/lib#3")})
        self.assertEqual(self.check(source).action, "start")


class ContentHashTest(unittest.TestCase):
    """内容指纹：开工前检查拿它和正在跑的运行、暂停存档比。"""

    def content(self, ref_body="背景说明", labeled_at=LABELED_AT, title=TITLE):
        source = FakeSource({8: issue(8, "见 #12", title=title), 12: issue(12, ref_body)})
        return check_content(source, 8, labeled_at)

    def test_relabel_with_nothing_changed_has_same_hash(self):
        # 重新打标签只改了时间，内容没变
        self.assertEqual(self.content().digest, self.content(labeled_at="2026-10-25T11:00:00Z").digest)

    def test_reference_change_changes_hash(self):
        self.assertNotEqual(self.content().digest, self.content(ref_body="背景说明，补充一句").digest)

    def test_title_change_changes_hash(self):
        self.assertNotEqual(self.content().digest, self.content(title="另一个标题").digest)

    def test_stop_has_no_hash(self):
        source = FakeSource({8: issue(8, "![](https://example.com/a.png)")})
        self.assertEqual(check_content(source, 8, LABELED_AT).digest, "")

    def test_hash_format(self):
        self.assertRegex(content_hash({"issue": 8, "title": "", "body": "", "references": []}), r"^[0-9a-f]{16}$")


class FakeGitHub:
    """代替 GitHub：GraphQL 按查询里出现的关键字返回准备好的数据，GET 按路径返回。"""

    def __init__(self, graphql_routes, get_routes=None):
        self.graphql_routes = graphql_routes
        self.get_routes = get_routes or {}
        self.queries = []

    def graphql(self, query, variables=None):
        self.queries.append((query, variables))
        for key, response in self.graphql_routes:
            if key in query:
                return response
        raise AssertionError(f"没有准备这条查询：{query[:80]}")

    def get(self, path_or_url):
        return self.get_routes[path_or_url]


def page(nodes, has_next=False, cursor=None):
    return {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor}, "nodes": nodes}


def issue_node(number, edits=(), renames=(), edits_next=False):
    return {"__typename": "Issue", "number": number, "state": "OPEN", "title": "标题", "body": "正文",
            "createdAt": "2026-10-20T08:00:00Z", "author": {"login": OUTSIDER},
            "userContentEdits": page(list(edits), edits_next, "c1"), "timelineItems": page(list(renames))}


class SourceTest(unittest.TestCase):
    def test_issues_pr_and_missing(self):
        data = {"data": {"repository": {"n8": issue_node(8), "n9": {"__typename": "PullRequest"}, "n10": None}},
                "errors": [{"type": "NOT_FOUND", "path": ["repository", "n10"]}]}
        github = FakeGitHub([("issueOrPullRequest", Response(200, data))])
        found = Source(github, REPO).issues([8, 9, 10])
        self.assertEqual(found[8], Issue(8, "open", "标题", "正文", OUTSIDER, "2026-10-20T08:00:00Z"))
        self.assertEqual((found[9], found[10]), ("PullRequest", None))
        self.assertEqual(github.queries[0][1], {"owner": "octo", "name": "demo"})

    def test_other_errors_raise(self):
        data = {"data": {"repository": {"n8": None}}, "errors": [{"type": "RATE_LIMITED"}]}
        with self.assertRaises(RuntimeError):
            Source(FakeGitHub([("issueOrPullRequest", Response(200, data))]), REPO).issues([8])

    def test_edits_are_paged(self):
        first = issue_node(8, [{"editedAt": "2026-10-24T09:00:00Z", "editor": None, "diff": "第二版"}], edits_next=True)
        more = {"data": {"repository": {"issue": {"userContentEdits": page(
            [{"editedAt": "2026-10-20T08:00:00Z", "editor": {"login": OUTSIDER}, "diff": "原文"}])}}}}
        github = FakeGitHub([("after: $after", Response(200, more)),
                             ("issueOrPullRequest", Response(200, {"data": {"repository": {"n8": first}}}))])
        found = Source(github, REPO).issues([8])[8]
        self.assertEqual(found.edits, (("2026-10-24T09:00:00Z", "", "第二版"),
                                       ("2026-10-20T08:00:00Z", OUTSIDER, "原文")))
        self.assertEqual(github.queries[1][1]["after"], "c1")

    def test_batches(self):
        def answer(query, variables=None):
            numbers = [int(n) for n in re.findall(r"n(\d+): issueOrPullRequest", query)]
            return Response(200, {"data": {"repository": {f"n{n}": {"__typename": "Issue"} for n in numbers}}})

        github = FakeGitHub([])
        github.graphql = answer
        kinds = Source(github, REPO).kinds(list(range(1, 46)))
        self.assertEqual(set(kinds.values()), {"Issue"})
        self.assertEqual(len(kinds), 45)

    def test_notified(self):
        url = f"/repos/{REPO}/issues/8/comments?since=2026-10-24T09%3A00%3A00Z&per_page=100"
        comments = [
            {"user": {"login": OUTSIDER}, "body": NOTICE_MARKER, "created_at": "2026-10-24T10:00:00Z"},
            {"user": {"login": NOTICE_AUTHOR}, "body": "别的留言", "created_at": "2026-10-24T10:00:00Z"},
            {"user": {"login": NOTICE_AUTHOR}, "body": "提醒" + NOTICE_MARKER, "created_at": "2026-10-26T10:00:00Z"},
        ]
        source = Source(FakeGitHub([], {url: Response(200, comments)}), REPO)
        self.assertFalse(source.notified(8, "2026-10-24T09:00:00Z", LABELED_AT))
        comments.append({"user": {"login": NOTICE_AUTHOR}, "body": "提醒\n" + NOTICE_MARKER,
                         "created_at": "2026-10-24T11:00:00Z"})
        self.assertTrue(source.notified(8, "2026-10-24T09:00:00Z", LABELED_AT))

    def test_is_allowed_is_cached(self):
        path = f"/repos/{REPO}/collaborators/{MAINTAINER}/permission"
        github = FakeGitHub([], {path: Response(200, {"permission": "write", "role_name": "maintain"})})
        source = Source(github, REPO)
        self.assertTrue(source.is_allowed(MAINTAINER))
        github.get_routes.clear()
        self.assertTrue(source.is_allowed(MAINTAINER))
        self.assertFalse(source.is_allowed(""))


if __name__ == "__main__":
    unittest.main()
