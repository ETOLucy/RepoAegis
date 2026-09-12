"""Domain models shared by storage, the state machine, the API and the console."""

from __future__ import annotations

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


class Event(BaseModel):
    """One append-only audit record. ``id`` is monotonic and doubles as the SSE id."""

    id: int
    task_id: str
    type: str
    payload: dict[str, Any]
    ts: datetime
