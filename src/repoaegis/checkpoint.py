"""暂停存档的产物名：repoaegis-pause-issue-编号-内容哈希-截止时刻。

截止时刻用 UTC、精确到秒，写进名字里，开工前检查只看名字就能判断"内容一致吗、过期了吗"，
不用下载存档。存档本身由 agent 作业被取消时上传（尚未实现）。只用标准库。
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

_DEADLINE_FORMAT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True)
class PauseCheckpoint:
    artifact_id: int
    run_id: int
    content_hash: str
    deadline: datetime
    created_at: str


def pause_name(issue_number: int, content_hash: str, deadline: datetime) -> str:
    return f"repoaegis-pause-issue-{issue_number}-{content_hash}-{deadline.astimezone(UTC).strftime(_DEADLINE_FORMAT)}"


def parse_pause_name(name: str, issue_number: int) -> tuple[str, datetime] | None:
    """返回 (内容哈希, 截止时刻)；不是这个 issue 的暂停存档时返回 None。"""
    match = re.fullmatch(rf"repoaegis-pause-issue-{issue_number}-([0-9a-f]{{16}})-(\d{{8}}T\d{{6}}Z)", name)
    if not match:
        return None
    deadline = datetime.strptime(match.group(2), _DEADLINE_FORMAT).replace(tzinfo=UTC)
    return match.group(1), deadline
