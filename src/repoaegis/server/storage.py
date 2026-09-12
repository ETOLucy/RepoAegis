"""Persistence behind SQLAlchemy 2.0 async.

One code path serves SQLite (development, tests) and PostgreSQL (production);
the URL decides. The repository exposes the few operations the state machine
needs and nothing else. The compare-and-set in ``transition`` is what keeps two
workers from advancing the same task twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, select, update
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

from repoaegis.server.models import Event, Task, TaskStatus


class Base(DeclarativeBase):
    pass


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    issue_url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def _utc(dt: datetime) -> datetime:
    # SQLite drops tzinfo on the way out; everything in this codebase is UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def _task(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        issue_url=row.issue_url,
        title=row.title,
        status=TaskStatus(row.status),
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def _event(row: EventRow) -> Event:
    return Event(
        id=row.id, task_id=row.task_id, type=row.type, payload=row.payload, ts=_utc(row.ts)
    )


class Database:
    def __init__(self, url: str) -> None:
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            if ":memory:" in url:
                # Every pooled connection would otherwise get its own empty database.
                kwargs["poolclass"] = StaticPool
                kwargs["connect_args"] = {"check_same_thread": False}
            else:
                Path(url.rsplit("///", 1)[-1]).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


class TaskRepo:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, *, issue_url: str, title: str) -> Task:
        now = _now()
        row = TaskRow(
            id=uuid.uuid4().hex,
            issue_url=issue_url,
            title=title,
            status=TaskStatus.QUEUED.value,
            created_at=now,
            updated_at=now,
        )
        async with self._sessions() as s:
            s.add(row)
            await s.commit()
        return _task(row)

    async def get(self, task_id: str) -> Task | None:
        async with self._sessions() as s:
            row = await s.get(TaskRow, task_id)
        return _task(row) if row else None

    async def list_tasks(self, *, limit: int = 100) -> list[Task]:
        stmt = select(TaskRow).order_by(TaskRow.created_at.desc()).limit(limit)
        async with self._sessions() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_task(r) for r in rows]

    async def next_queued(self) -> Task | None:
        stmt = (
            select(TaskRow)
            .where(TaskRow.status == TaskStatus.QUEUED.value)
            .order_by(TaskRow.created_at)
            .limit(1)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
        return _task(row) if row else None

    async def transition(
        self, task_id: str, *, expected: TaskStatus, to: TaskStatus
    ) -> Task | None:
        """Compare-and-set. Returns ``None`` if the task was not in ``expected``."""
        stmt = (
            update(TaskRow)
            .where(TaskRow.id == task_id, TaskRow.status == expected.value)
            .values(status=to.value, updated_at=_now())
            .returning(TaskRow)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
            await s.commit()
        return _task(row) if row else None

    async def append_event(self, task_id: str, type: str, payload: dict[str, Any]) -> Event:
        row = EventRow(task_id=task_id, type=type, payload=payload, ts=_now())
        async with self._sessions() as s:
            s.add(row)
            await s.commit()
        return _event(row)

    async def list_events(
        self, *, task_id: str | None = None, after_id: int = 0, limit: int = 500
    ) -> list[Event]:
        stmt = select(EventRow).where(EventRow.id > after_id).order_by(EventRow.id).limit(limit)
        if task_id is not None:
            stmt = stmt.where(EventRow.task_id == task_id)
        async with self._sessions() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_event(r) for r in rows]
