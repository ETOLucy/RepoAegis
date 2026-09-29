"""Domain models shared by storage, the state machine, the API and the console."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class TaskStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    SOLVING = "solving"
    AWAITING_PATCH_APPROVAL = "awaiting_patch_approval"
    VERIFYING = "verifying"
    DELIVERING = "delivering"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"


_GITHUB_ISSUE = re.compile(r"github\.com/([^/]+)/([^/]+)/(?:issues|pull)/(\d+)")


def default_title(issue_url: str) -> str:
    """``owner/repo#123`` for GitHub issue URLs, the URL itself otherwise."""
    m = _GITHUB_ISSUE.search(issue_url)
    if m:
        return f"{m.group(1)}/{m.group(2)}#{m.group(3)}"
    return issue_url


# States in which a worker holds the task, so a lease exists and can expire.
# The value is where a task goes when its lease is reclaimed: planning starts
# over from the queue; a solve round or a delivery attempt is re-entered as is.
RECLAIM_TARGET: dict[TaskStatus, TaskStatus] = {
    TaskStatus.PLANNING: TaskStatus.QUEUED,
    TaskStatus.SOLVING: TaskStatus.SOLVING,
    TaskStatus.VERIFYING: TaskStatus.SOLVING,
    TaskStatus.DELIVERING: TaskStatus.DELIVERING,
}
LEASED_STATES = frozenset(RECLAIM_TARGET)


class TaskCreate(BaseModel):
    issue_url: HttpUrl
    title: str | None = Field(default=None, max_length=200)


class Task(BaseModel):
    id: str
    issue_url: str
    title: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    # Set once planning pins a commit; the console and the evaluation both need
    # to know which revision a run actually looked at.
    repo_sha: str | None = None
    steps: int = 0
    cost_usd: float = 0.0
    # Who holds the task right now and until when (see ``Claim``). Both are
    # None whenever no worker is on it.
    lease_owner: str | None = None
    lease_until: datetime | None = None
    lease_token: int = 0
    recoveries: int = 0


class Claim(BaseModel):
    """Proof that one worker holds one task, for one particular claiming.

    ``task_id`` says which task; ``token`` says which claiming of it -- the
    value ``lease_token`` had after this worker took the lease. A task can be
    claimed again after its lease expires, and then the old token is refused
    on every write, so a worker that was only paused, not dead, cannot come
    back and write over the new holder's work (a fencing token).
    """

    task_id: str
    token: int


class Event(BaseModel):
    """One append-only audit record. ``id`` is monotonic and doubles as the SSE id."""

    id: int
    task_id: str
    type: str
    payload: dict[str, Any]
    ts: datetime


class ApprovalKind(StrEnum):
    """What is being approved. ``PLAN`` gates the task; the rest gate one tool call."""

    PLAN = "plan"
    PATCH = "patch"
    SHELL = "shell"
    PUSH = "push"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


def payload_digest(payload: dict[str, Any]) -> str:
    """Content address of an approval payload.

    Canonical JSON (sorted keys, no incidental whitespace) so that the same
    action always hashes the same way, and a swapped argument never does. This
    is what the executor re-checks before it runs anything: approval is bound to
    this exact content, not to a boolean somewhere.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Approval(BaseModel):
    """One approval envelope: subject, content hash, verdict, expiry."""

    id: str
    task_id: str
    kind: ApprovalKind
    subject: str
    payload: dict[str, Any]
    payload_hash: str
    status: ApprovalStatus
    policy: str
    reason: str
    decided_by: str | None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status is ApprovalStatus.PENDING


class DecisionRequest(BaseModel):
    """A human's answer. ``payload_hash`` is optional but verified when sent."""

    decision: Decision
    payload_hash: str | None = Field(default=None, min_length=64, max_length=64)
    note: str | None = Field(default=None, max_length=500)
