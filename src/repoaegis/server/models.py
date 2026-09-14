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
