import json
import os
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from repoaegis import precheck
from repoaegis.checkpoint import PauseCheckpoint, pause_name
from repoaegis.content_check import Result, content_hash
from repoaegis.precheck import (Decision, Event, GitHub, Labeling, Repo, Response, artifact_name,
                                parse_artifact_name, write_content, write_outputs, write_snapshot)

REPO = "octo/demo"
LABEL = "agent-fix"
ISSUE = f"/repos/{REPO}/issues/8"
EVENTS = f"/repos/{REPO}/issues/8/events?per_page=100"
RUNS = {status: f"/repos/{REPO}/actions/runs?event=issues&status={status}&per_page=100"
        for status in ("in_progress", "queued")}
TITLE, BODY = "parse_date 遇到空字符串时崩溃", "调用 parse_date('') 抛 ValueError"
CONTENT = {"issue": 8, "title": TITLE, "body": BODY, "version": {}, "references": []}
HASH = content_hash(CONTENT)


class Inspector:
    """代替内容检查：返回事先给定的结论，记下被问过的打标签时间。"""

    def __init__(self, result=None):
        self.result = result or Result("start", "内容检查通过", content=CONTENT)
        self.asked = []

    def __call__(self, labeled_at):
        self.asked.append(labeled_at)
        return self.result


def check(r, ev, label, now=None, inspect=None):
    return precheck.check(r, ev, label, inspect or Inspector(), now=now)


class FakeGitHub:
    """按路径返回事先准备好的 Response，代替真的 GitHub 接口；记录所有 POST 和 DELETE。

    某个路径对应一个列表时，按顺序依次返回，用完后一直返回最后一个（模拟运行从"停止中"到"已结束"）。
    """

    def __init__(self, routes):
        self.routes = routes
        self.posts = []
        self.deletes = []

    def get(self, path_or_url):
        route = self.routes[path_or_url]
        if isinstance(route, list):
            return route.pop(0) if len(route) > 1 else route[0]
        return route

    def post(self, path):
        self.posts.append(path)
        return self.routes.get(("POST", path), Response(202))

    def delete(self, path):
        self.deletes.append(path)
        return self.routes.get(("DELETE", path), Response(204))


def issue(state="open", labels=(LABEL,), **extra):
    return Response(200, {"number": 8, "state": state,
                          "labels": [{"name": name} for name in labels], **extra})


def labeled(login, label=LABEL, created_at="2026-10-25T10:00:00Z", event_id=1, event="labeled"):
    return {"id": event_id, "event": event, "created_at": created_at,
            "actor": {"login": login}, "label": {"name": label}}


def permission(login, base, role):
    return (f"/repos/{REPO}/collaborators/{login}/permission",
            Response(200, {"permission": base, "role_name": role, "user": {"login": login}}))


def event(action="labeled", sender="alice", run_id=900):
    return Event(action=action, sender=sender, issue_number=8, run_id=run_id)


def runs(*active, status="in_progress"):
    """active 是 (run_id, [产物名]) 的列表，生成运行列表和每次运行的产物列表。"""
    table = {RUNS["in_progress"]: Response(200, {"workflow_runs": []}),
             RUNS["queued"]: Response(200, {"workflow_runs": []})}
    table[RUNS[status]] = Response(200, {"workflow_runs": [{"id": run_id} for run_id, _ in active]})
    for run_id, names in active:
        table[f"/repos/{REPO}/actions/runs/{run_id}/artifacts?per_page=100"] = Response(
            200, {"artifacts": [{"name": name, "expired": False} for name in names]})
        # 被停掉后查它的状态：默认马上就结束了
        table[f"/repos/{REPO}/actions/runs/{run_id}"] = Response(200, {"id": run_id, "status": "completed"})
    return table


ARTIFACTS = f"/repos/{REPO}/actions/artifacts?per_page=100"
NOW = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 28, 15, 59, 59, tzinfo=UTC)
EARLIER = datetime(2026, 10, 26, 15, 59, 59, tzinfo=UTC)


def pause(artifact_id, digest=HASH, deadline=LATER, run_id=700, created_at="2026-10-27T01:00:00Z",
          issue_number=8, expired=False):
    """仓库产物列表里的一份暂停存档。"""
    return {"id": artifact_id, "name": pause_name(issue_number, digest, deadline), "expired": expired,
            "created_at": created_at, "workflow_run": {"id": run_id}}


def repo(issue_response=None, events=(), *permissions, active=None, artifacts=()):
    table = {ISSUE: issue_response or issue(), EVENTS: Response(200, list(events)),
             ARTIFACTS: Response(200, {"artifacts": list(artifacts)})}
    table.update(active or runs())
    table.update(dict(permissions))
    github = FakeGitHub(table)
    return Repo(github, REPO, sleep=lambda seconds: None, wait_timeout=30, wait_interval=5), github


ALICE_WRITE = permission("alice", "write", "write")
TOM_TRIAGE = permission("tom", "read", "triage")


class LabeledEventTest(unittest.TestCase):
    def test_start_when_open_and_labeled_by_writer(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE)
        self.assertEqual(check(r, event(), LABEL, now=NOW),
                         Decision("start", "alice（write）打上了 agent-fix", "alice", HASH, content=CONTENT,
                                  labeled_at="2026-10-25T10:00:00Z"))
        self.assertEqual(github.posts, [])
        self.assertEqual(github.deletes, [])

    def test_maintain_and_admin_can_start(self):
        for base, role in (("write", "maintain"), ("admin", "admin")):
            with self.subTest(role=role):
                r, _ = repo(issue(), [labeled("alice")], permission("alice", base, role))
                self.assertEqual(check(r, event(), LABEL).action, "start")

    def test_skip_when_issue_moved_or_deleted(self):
        for status in (301, 404, 410):
            with self.subTest(status=status):
                r, _ = repo(Response(status))
                self.assertEqual(check(r, event(), LABEL).action, "skip")

    def test_gone_issue_cancels_running_job_whatever_the_event(self):
        # 打标签那次运行晚到，发现 issue 已被转移：转移只有有权限的人能做，正在跑的也该停
        r, github = repo(Response(301), active=runs((123, [artifact_name(8, HASH)])))
        decision = check(r, event(), LABEL)
        self.assertEqual((decision.action, decision.cancelled), ("skip", (123,)))
        self.assertEqual(github.posts, [f"/repos/{REPO}/actions/runs/123/cancel"])

    def test_skip_when_pull_request(self):
        r, _ = repo(issue(pull_request={"url": "..."}))
        self.assertEqual(check(r, event(), LABEL).reason, "#8 是 PR，不是 issue")

    def test_skip_when_closed(self):
        r, _ = repo(issue(state="closed"))
        self.assertEqual(check(r, event(), LABEL).reason, "issue #8 已关闭")

    def test_skip_when_label_removed(self):
        r, _ = repo(issue(labels=("bug",)))
        self.assertEqual(check(r, event(), LABEL).reason, "issue #8 上已没有 agent-fix 标签")

    def test_skip_when_labeled_by_triage(self):
        r, github = repo(issue(), [labeled("tom")], TOM_TRIAGE)
        self.assertEqual(check(r, event(sender="tom"), LABEL),
                         Decision("skip", "tom（triage）打的标签没有权限，忽略", "tom"))
        self.assertEqual(github.posts, [])

    def test_skip_when_labeled_by_non_collaborator(self):
        r, _ = repo(issue(), [labeled("someone[bot]")],
                    (f"/repos/{REPO}/collaborators/someone%5Bbot%5D/permission", Response(404)))
        self.assertEqual(check(r, event(sender="someone[bot]"), LABEL).reason,
                         "someone[bot]（不是协作者）打的标签没有权限，忽略")

    def test_skip_when_no_label_record(self):
        r, _ = repo(issue(), [])
        self.assertEqual(check(r, event(), LABEL).action, "skip")

    def test_unexpected_status_raises(self):
        r, _ = repo(Response(403))
        with self.assertRaises(RuntimeError):
            check(r, event(), LABEL)

    def test_uses_last_labeler_not_sender(self):
        # 事件乱序：这次运行的 sender 是 tom（triage），但最后打标签的是 alice
        r, _ = repo(issue(), [labeled("tom", event_id=1), labeled("alice", event_id=2,
                                                                 created_at="2026-10-25T10:01:00Z")],
                    ALICE_WRITE, TOM_TRIAGE)
        self.assertEqual(check(r, event(sender="tom"), LABEL).action, "start")


class ContentCheckStepTest(unittest.TestCase):
    """打标签的人有权限之后做内容检查，比较新旧运行用的是它整理出的内容。"""

    def test_inspects_at_last_labeling_time(self):
        r, _ = repo(issue(), [labeled("tom", event_id=1), labeled("alice", event_id=2,
                                                                 created_at="2026-10-25T10:01:00Z")],
                    ALICE_WRITE, TOM_TRIAGE)
        inspector = Inspector()
        check(r, event(sender="tom"), LABEL, inspect=inspector)
        self.assertEqual(inspector.asked, ["2026-10-25T10:01:00Z"])

    def test_not_inspected_when_labeler_has_no_permission(self):
        r, _ = repo(issue(), [labeled("tom")], TOM_TRIAGE)
        inspector = Inspector()
        check(r, event(sender="tom"), LABEL, inspect=inspector)
        self.assertEqual(inspector.asked, [])

    def test_not_inspected_after_unlabel(self):
        r, _ = repo(issue(labels=("bug",)), [labeled("alice")], ALICE_WRITE)
        inspector = Inspector()
        check(r, event("unlabeled"), LABEL, inspect=inspector)
        self.assertEqual(inspector.asked, [])

    def test_failed_content_check_stops(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE, artifacts=[pause(55)])
        inspector = Inspector(Result("stop", "引用多于一层（#12 引用了 #30）", "nested"))
        self.assertEqual(check(r, event(), LABEL, now=NOW, inspect=inspector),
                         Decision("stop", "alice（write）打上了 agent-fix，但引用多于一层（#12 引用了 #30）",
                                  "alice", kind="nested", labeled_at="2026-10-25T10:00:00Z"))
        self.assertEqual(github.deletes, [])  # 暂停存档留着，到期自然删

    def test_failed_content_check_cancels_running_job(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE, active=runs((123, [artifact_name(8, HASH)])))
        decision = check(r, event(), LABEL, inspect=Inspector(Result("stop", "正文里有图片 1 处", "media")))
        self.assertEqual((decision.action, decision.kind, decision.cancelled), ("stop", "media", (123,)))
        self.assertEqual(github.posts, [f"/repos/{REPO}/actions/runs/123/cancel"])

    def test_reference_change_restarts_running_job(self):
        # 标题正文没变，引用的 #12 被维护者改过：内容不一致，停掉重来
        changed = {**CONTENT, "references": [{"ref": "#12", "read": True, "title": "t", "state": "open", "body": "新"}]}
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE, active=runs((123, [artifact_name(8, HASH)])))
        decision = check(r, event(), LABEL, inspect=Inspector(Result("start", "通过", content=changed)))
        self.assertEqual((decision.action, decision.cancelled, decision.content), ("start", (123,), changed))
        self.assertEqual(decision.content_hash, content_hash(changed))


class ActiveRunTest(unittest.TestCase):
    """已有运行在跑时，打标签要对比内容。"""

    def test_same_content_lets_running_job_continue(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE,
                         active=runs((123, [artifact_name(8, HASH)])))
        self.assertEqual(check(r, event(), LABEL),
                         Decision("skip", "运行 123 正在处理相同内容的 issue #8，让它继续", "alice"))
        self.assertEqual(github.posts, [])

    def test_different_content_cancels_and_restarts(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE,
                         active=runs((123, [artifact_name(8, "0" * 16)])))
        decision = check(r, event(), LABEL)
        self.assertEqual(decision.action, "start")
        self.assertEqual(decision.cancelled, (123,))
        self.assertEqual(github.posts, [f"/repos/{REPO}/actions/runs/123/cancel"])

    def test_own_run_and_other_issues_are_ignored(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE,
                         active=runs((900, [artifact_name(8, "0" * 16)]),      # 自己
                                     (124, [artifact_name(9, "0" * 16)]),      # 别的 issue
                                     (125, ["coverage-report"])))              # 无关产物
        self.assertEqual(check(r, event(run_id=900), LABEL).action, "start")
        self.assertEqual(github.posts, [])

    def test_queued_runs_count_as_active(self):
        r, _ = repo(issue(), [labeled("alice")], ALICE_WRITE,
                    active=runs((126, [artifact_name(8, HASH)]), status="queued"))
        self.assertEqual(check(r, event(), LABEL).action, "skip")

    def test_label_by_triage_is_ignored_while_running(self):
        r, github = repo(issue(), [labeled("tom")], TOM_TRIAGE,
                         active=runs((123, [artifact_name(8, HASH)])))
        decision = check(r, event(sender="tom"), LABEL)
        self.assertEqual(decision.action, "skip")
        self.assertIn("已有运行继续", decision.reason)
        self.assertEqual(github.posts, [])

    def test_already_finished_run_is_not_reported_as_cancelled(self):
        table = runs((123, [artifact_name(8, "0" * 16)]))
        table[("POST", f"/repos/{REPO}/actions/runs/123/cancel")] = Response(409)
        r, _ = repo(issue(), [labeled("alice")], ALICE_WRITE, active=table)
        self.assertEqual(check(r, event(), LABEL).cancelled, ())


class StopOrderTest(unittest.TestCase):
    """撤标签、关闭、删除、转移：有权限的人做的无条件停，其他人做的忽略。"""

    def test_unlabel_by_writer_cancels_running_job(self):
        r, github = repo(issue(labels=("bug",)), [labeled("alice")], ALICE_WRITE,
                         active=runs((123, [artifact_name(8, HASH)])))
        decision = check(r, event("unlabeled"), LABEL)
        self.assertEqual((decision.action, decision.cancelled), ("skip", (123,)))
        self.assertEqual(github.posts, [f"/repos/{REPO}/actions/runs/123/cancel"])

    def test_unlabel_by_triage_is_ignored(self):
        r, github = repo(issue(labels=("bug",)), [labeled("alice")], ALICE_WRITE, TOM_TRIAGE,
                         active=runs((123, [artifact_name(8, HASH)])))
        self.assertEqual(check(r, event("unlabeled", sender="tom"), LABEL),
                         Decision("skip", "tom（triage）撤掉了标签，没有权限，忽略"))
        self.assertEqual(github.posts, [])

    def test_close_by_issue_author_is_ignored(self):
        r, github = repo(issue(state="closed"), [labeled("alice")], ALICE_WRITE,
                         (f"/repos/{REPO}/collaborators/reporter/permission", Response(404)),
                         active=runs((123, [artifact_name(8, HASH)])))
        self.assertEqual(check(r, event("closed", sender="reporter"), LABEL).reason,
                         "reporter（不是协作者）关闭了 issue，没有权限，忽略")
        self.assertEqual(github.posts, [])

    def test_unlabel_then_relabel_restarts_even_with_same_content(self):
        # 撤标签那次运行最后才处理：先无条件停掉旧的，再看当前状态，标签又在了就重新开工
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE,
                         active=runs((123, [artifact_name(8, HASH)])))
        decision = check(r, event("unlabeled"), LABEL)
        self.assertEqual((decision.action, decision.cancelled), ("start", (123,)))

    def test_delete_cancels_running_job(self):
        r, _ = repo(Response(410), [], ALICE_WRITE, active=runs((123, [artifact_name(8, HASH)])))
        decision = check(r, event("deleted"), LABEL)
        self.assertEqual((decision.action, decision.cancelled), ("skip", (123,)))

    def test_stop_order_with_nothing_running(self):
        r, github = repo(issue(labels=("bug",)), [labeled("alice")], ALICE_WRITE)
        self.assertEqual(check(r, event("unlabeled"), LABEL).action, "skip")
        self.assertEqual(github.posts, [])


class WaitStoppedRunTest(unittest.TestCase):
    """停掉的运行要等它完全结束，它被取消后上传的暂停存档才找得到。"""

    def test_waits_until_cancelled_run_completes(self):
        table = runs((123, [artifact_name(8, HASH)]))
        table[f"/repos/{REPO}/actions/runs/123"] = [Response(200, {"status": "in_progress"}),
                                                    Response(200, {"status": "in_progress"}),
                                                    Response(200, {"status": "completed"})]
        slept = []
        r = Repo(FakeGitHub({**table, ISSUE: issue(labels=("bug",)), EVENTS: Response(200, [labeled("alice")]),
                             **dict([ALICE_WRITE])}), REPO, sleep=slept.append, wait_timeout=30, wait_interval=5)
        decision = check(r, event("unlabeled"), LABEL, now=NOW)
        self.assertEqual((decision.cancelled, decision.unfinished), ((123,), ()))
        self.assertEqual(slept, [5, 5])

    def test_reports_run_still_running_after_timeout(self):
        table = runs((123, [artifact_name(8, HASH)]))
        table[f"/repos/{REPO}/actions/runs/123"] = Response(200, {"status": "in_progress"})
        r, _ = repo(issue(labels=("bug",)), [labeled("alice")], ALICE_WRITE, active=table)
        decision = check(r, event("unlabeled"), LABEL, now=NOW)
        self.assertEqual((decision.action, decision.cancelled, decision.unfinished), ("skip", (123,), (123,)))

    def test_already_finished_run_is_not_waited_for(self):
        table = runs((123, [artifact_name(8, "0" * 16)]))
        table[("POST", f"/repos/{REPO}/actions/runs/123/cancel")] = Response(409)
        del table[f"/repos/{REPO}/actions/runs/123"]  # 不会去查它的状态
        r, _ = repo(issue(), [labeled("alice")], ALICE_WRITE, active=table)
        self.assertEqual(check(r, event(), LABEL, now=NOW).unfinished, ())


class PauseCheckpointTest(unittest.TestCase):
    """开工时找暂停存档：内容一致、没过截止时刻的接着做，其余删掉。"""

    def test_resume_from_matching_checkpoint(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE, artifacts=[pause(55)])
        decision = check(r, event(), LABEL, now=NOW)
        self.assertEqual(decision.resume, PauseCheckpoint(55, 700, HASH, LATER, "2026-10-27T01:00:00Z"))
        self.assertEqual(decision.reason, "alice（write）打上了 agent-fix，从运行 700 的暂停存档接着做")
        self.assertEqual(github.deletes, [])

    def test_different_content_deletes_checkpoint_and_starts_fresh(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE, artifacts=[pause(55, digest="0" * 16)])
        decision = check(r, event(), LABEL, now=NOW)
        self.assertEqual((decision.action, decision.resume, decision.deleted), ("start", None, (55,)))
        self.assertEqual(github.deletes, [f"/repos/{REPO}/actions/artifacts/55"])

    def test_checkpoint_past_deadline_is_not_used(self):
        r, _ = repo(issue(), [labeled("alice")], ALICE_WRITE, artifacts=[pause(55, deadline=EARLIER)])
        decision = check(r, event(), LABEL, now=NOW)
        self.assertEqual((decision.resume, decision.deleted), (None, (55,)))

    def test_newest_matching_checkpoint_wins(self):
        r, _ = repo(issue(), [labeled("alice")], ALICE_WRITE,
                    artifacts=[pause(55, created_at="2026-10-27T01:00:00Z", run_id=700),
                               pause(56, created_at="2026-10-27T01:40:00Z", run_id=701)])
        decision = check(r, event(), LABEL, now=NOW)
        self.assertEqual((decision.resume.artifact_id, decision.resume.run_id, decision.deleted), (56, 701, (55,)))

    def test_other_issues_and_expired_artifacts_are_left_alone(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE,
                         artifacts=[pause(57, issue_number=80), pause(58, expired=True),
                                    {"id": 59, "name": artifact_name(8, HASH), "expired": False,
                                     "created_at": "2026-10-27T01:00:00Z", "workflow_run": {"id": 702}}])
        decision = check(r, event(), LABEL, now=NOW)
        self.assertEqual((decision.resume, decision.deleted), (None, ()))
        self.assertEqual(github.deletes, [])

    def test_failed_delete_is_not_reported(self):
        r, github = repo(issue(), [labeled("alice")], ALICE_WRITE, artifacts=[pause(55, digest="0" * 16)])
        github.routes[("DELETE", f"/repos/{REPO}/actions/artifacts/55")] = Response(403)
        self.assertEqual(check(r, event(), LABEL, now=NOW).deleted, ())

    def test_unlabel_paused_then_relabel_resumes(self):
        # 撤标签那次运行最后才处理，标签又在了：停掉旧的、等它把存档传完，再从存档接着做
        r, _ = repo(issue(), [labeled("alice")], ALICE_WRITE,
                    active=runs((123, [artifact_name(8, HASH)])), artifacts=[pause(55, run_id=123)])
        decision = check(r, event("unlabeled"), LABEL, now=NOW)
        self.assertEqual((decision.action, decision.cancelled, decision.resume.run_id), ("start", (123,), 123))

    def test_skip_does_not_touch_checkpoints(self):
        r, github = repo(issue(labels=("bug",)), [labeled("alice")], ALICE_WRITE,
                         artifacts=[pause(55, digest="0" * 16)])
        self.assertEqual(check(r, event("unlabeled"), LABEL, now=NOW).action, "skip")
        self.assertEqual(github.deletes, [])


class HelperTest(unittest.TestCase):
    def test_artifact_name_round_trip(self):
        name = artifact_name(8, HASH)
        self.assertEqual(parse_artifact_name(name, 8), HASH)
        self.assertIsNone(parse_artifact_name(name, 80))
        self.assertIsNone(parse_artifact_name("repoaegis-issue-8-notahash", 8))

    def test_last_labeler_across_pages(self):
        page2 = "https://api.github.com/repositories/1/issues/8/events?per_page=100&page=2"
        github = FakeGitHub({
            EVENTS: Response(200, [
                labeled("alice", event_id=1, created_at="2026-10-25T10:00:00Z"),
                labeled("alice", event_id=2, created_at="2026-10-25T10:05:00Z", event="unlabeled"),
            ], next_url=page2),
            page2: Response(200, [
                labeled("bob", event_id=3, created_at="2026-10-25T10:10:00Z"),
                labeled("carol", label="bug", event_id=4, created_at="2026-10-25T10:20:00Z"),
            ]),
        })
        self.assertEqual(Repo(github, REPO).last_labeling(8, LABEL), Labeling("bob", "2026-10-25T10:10:00Z"))

    def test_write_outputs_snapshot_and_content(self):
        resume = PauseCheckpoint(55, 700, HASH, LATER, "2026-10-27T01:00:00Z")
        decision = Decision("start", "第一行\n第二行", "alice", HASH, cancelled=(123,), content=CONTENT,
                            labeled_at="2026-10-27T01:30:00Z", resume=resume)
        with tempfile.TemporaryDirectory() as tmp:
            out, snap, content = (os.path.join(tmp, name) for name in ("output", "snapshot.json", "content.json"))
            write_outputs(out, decision, event())
            write_snapshot(snap, event(), decision)
            write_content(content, decision)
            with open(out, encoding="utf-8") as f:
                self.assertEqual(f.read(), f"action=start\nreason=第一行 第二行\nkind=\nlabeler=alice\n"
                                           f"content_hash={HASH}\nartifact_name=repoaegis-issue-8-{HASH}\n"
                                           f"cancelled=123\nlabeled_at=2026-10-27T01:30:00Z\n"
                                           f"resume_run_id=700\nresume_artifact_id=55\n")
            with open(snap, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual((data["issue"], data["content_hash"], data["run_id"],
                              data["labeled_at"], data["resume_run_id"], data["resume_artifact_id"]),
                             (8, HASH, 900, "2026-10-27T01:30:00Z", 700, 55))
            with open(content, encoding="utf-8") as f:
                self.assertEqual(json.load(f), CONTENT)

    def test_outputs_without_start_have_no_artifact_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "output")
            write_outputs(out, Decision("stop", "x", kind="media"), event())
            with open(out, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("artifact_name=\n", text)
            self.assertIn("kind=media\n", text)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/moved":
            self.send_response(301)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
        elif self.path == "/gone":
            self.send_response(410)
            self.end_headers()
        else:
            body = json.dumps([{"auth": self.headers["Authorization"]}]).encode()
            self.send_response(200)
            self.send_header("Link", '<http://x/page2>; rel="next", <http://x/page9>; rel="last"')
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        self.send_response(202 if self.path.endswith("/cancel") else 409)
        self.end_headers()

    def log_message(self, *args):
        pass


class GitHubClientTest(unittest.TestCase):
    """用本机上的一个小 HTTP 服务代替 GitHub，检查客户端怎么处理状态码、分页和 POST。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.github = GitHub("t0ken", f"http://127.0.0.1:{cls.server.server_port}", retries=1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_redirect_is_not_followed(self):
        self.assertEqual(self.github.get("/moved").status, 301)

    def test_client_error_is_returned(self):
        self.assertEqual(self.github.get("/gone"), Response(410))

    def test_ok_with_token_and_next_link(self):
        response = self.github.get("/list")
        self.assertEqual(response, Response(200, [{"auth": "Bearer t0ken"}], "http://x/page2"))

    def test_post_cancel(self):
        repo_ = Repo(self.github, REPO)
        self.assertTrue(repo_.cancel(1))
        self.assertEqual(self.github.post("/other").status, 409)


if __name__ == "__main__":
    unittest.main()
