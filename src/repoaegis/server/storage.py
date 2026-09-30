"""Persistence behind SQLAlchemy 2.0 async.

One code path serves SQLite (development, tests) and PostgreSQL (production);
the URL decides. The repository exposes the few operations the state machine
needs and nothing else. The compare-and-set in ``transition`` is what keeps two
workers from advancing the same task twice.

SQLite is always file-backed, including in tests: ``:memory:`` only survives by
pinning the whole application to a single connection, and a single connection
means a single transaction, so the worker closing a read session rolls back
writes the API has not committed yet. Losing rows silently is a worse trade
than a few milliseconds of disk.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    event,
    select,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from repoaegis.server.models import (
    LEASED_STATES,
    RECLAIM_TARGET,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Claim,
    Event,
    Task,
    TaskStatus,
)


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
    repo_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    steps: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # The lease: who is working on the task and until when. Empty while the
    # task is queued, waiting on a human, or finished. ``lease_token`` counts
    # how many times the task has been claimed and never decreases; every write
    # made under a claim carries it, so a worker that lost its lease is refused.
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    lease_token: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # How many times an expired lease has been reclaimed. Capped, so a task
    # that keeps killing its worker ends up failed instead of looping.
    recoveries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(500))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    policy: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(200))
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
        repo_sha=row.repo_sha,
        steps=row.steps or 0,
        cost_usd=row.cost_usd or 0.0,
        lease_owner=row.lease_owner,
        lease_until=_utc(row.lease_until) if row.lease_until else None,
        lease_token=row.lease_token or 0,
        recoveries=row.recoveries or 0,
    )


def _approval(row: ApprovalRow) -> Approval:
    return Approval(
        id=row.id,
        task_id=row.task_id,
        kind=ApprovalKind(row.kind),
        subject=row.subject,
        payload=row.payload,
        payload_hash=row.payload_hash,
        status=ApprovalStatus(row.status),
        policy=row.policy,
        reason=row.reason,
        decided_by=row.decided_by,
        created_at=_utc(row.created_at),
        expires_at=_utc(row.expires_at),
        decided_at=_utc(row.decided_at) if row.decided_at else None,
    )


def _event(row: EventRow) -> Event:
    return Event(
        id=row.id, task_id=row.task_id, type=row.type, payload=row.payload, ts=_utc(row.ts)
    )


def _enable_sqlite_concurrency(engine: AsyncEngine) -> None:
    """WAL lets the API read while the worker writes; busy_timeout waits instead of failing."""

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


class StaleLease(RuntimeError):
    """The writer's claim is not the task's current lease.

    Raised from the write itself, never from a check before it: the lease is
    compared inside the same statement or transaction that writes, so a worker
    that lost its lease between looking and writing is still refused.
    """


@dataclass(frozen=True, slots=True)
class Reclaimed:
    """One task taken back from an expired lease."""

    task: Task
    previous_status: TaskStatus
    previous_owner: str | None
    exhausted: bool


# Entering any of these ends the worker's involvement, so the lease goes too.
_RELEASING = frozenset(TaskStatus) - LEASED_STATES

# The claim of the stage running in this asyncio task, if any. The worker sets
# it around each stage; every lease-checked write below falls back to it, so a
# service deep inside a stage never has to pass the claim by hand. (Lives here,
# not in the state machine, because the writes that check it live here.)
current_claim: ContextVar[Claim | None] = ContextVar("current_claim", default=None)


def _lease_condition(claim: Claim | None) -> Any:
    """What a write must find on the row: my token, or no lease at all."""
    claim = claim or current_claim.get()
    return TaskRow.lease_token == claim.token if claim else TaskRow.lease_owner.is_(None)


class InMemorySQLiteRejected(ValueError):
    """Raised instead of handing back a database that silently drops writes."""

    def __init__(self, url: str) -> None:
        super().__init__(
            f"in-memory SQLite is not supported ({url}): it forces one shared connection, "
            "so a concurrent session's rollback discards another's uncommitted writes. "
            "Point database_url at a file instead (tests: pytest's tmp_path)."
        )


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite"):
            if ":memory:" in url or "mode=memory" in url:
                raise InMemorySQLiteRejected(url)
            Path(url.rsplit("///", 1)[-1]).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url)
        if url.startswith("sqlite"):
            _enable_sqlite_concurrency(self.engine)
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
            steps=0,
            cost_usd=0.0,
            lease_token=0,
            recoveries=0,
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
            .where(TaskRow.status == TaskStatus.QUEUED.value, TaskRow.lease_owner.is_(None))
            .order_by(TaskRow.created_at)
            .limit(1)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
        return _task(row) if row else None

    async def next_in(self, status: TaskStatus) -> Task | None:
        """The oldest task in ``status`` that no worker currently holds."""
        stmt = (
            select(TaskRow)
            .where(TaskRow.status == status.value, TaskRow.lease_owner.is_(None))
            .order_by(TaskRow.created_at)
            .limit(1)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
        return _task(row) if row else None

    async def list_in(self, status: TaskStatus) -> list[Task]:
        """Every task in ``status`` that no worker holds, oldest first."""
        stmt = (
            select(TaskRow)
            .where(TaskRow.status == status.value, TaskRow.lease_owner.is_(None))
            .order_by(TaskRow.created_at)
        )
        async with self._sessions() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_task(r) for r in rows]

    async def transition(
        self, task_id: str, *, expected: TaskStatus, to: TaskStatus, claim: Claim | None = None
    ) -> Task | None:
        """Compare-and-set on status *and* lease.

        Returns ``None`` if the task was not in ``expected``. Raises
        ``StaleLease`` if it was, but the lease is not the writer's: a worker
        writing with an old token, or a lease-less writer (a human answering a
        gate) on a task some worker currently holds. Entering a state that no
        worker works in releases the lease in the same statement.
        """
        values: dict[str, Any] = {"status": to.value, "updated_at": _now()}
        if to in _RELEASING:
            values.update(lease_owner=None, lease_until=None)
        stmt = (
            update(TaskRow)
            .where(TaskRow.id == task_id, TaskRow.status == expected.value, _lease_condition(claim))
            .values(**values)
            .returning(TaskRow)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
            await s.commit()
            if row is None:
                current = await s.get(TaskRow, task_id)
                if current is not None and current.status == expected.value:
                    raise StaleLease(task_id)
        return _task(row) if row else None

    async def record_run(
        self,
        task_id: str,
        *,
        sha: str | None,
        steps: int,
        cost_usd: float,
        claim: Claim | None = None,
    ) -> None:
        """Stamp what one run consumed. Not a transition, but still lease-checked."""
        stmt = (
            update(TaskRow)
            .where(TaskRow.id == task_id, _lease_condition(claim))
            .values(repo_sha=sha, steps=steps, cost_usd=cost_usd, updated_at=_now())
            .returning(TaskRow.id)
        )
        async with self._sessions() as s:
            hit = (await s.execute(stmt)).scalar_one_or_none()
            await s.commit()
            if hit is None and await s.get(TaskRow, task_id) is not None:
                raise StaleLease(task_id)

    async def append_event(
        self, task_id: str, type: str, payload: dict[str, Any], *, claim: Claim | None = None
    ) -> Event:
        """Append one event, in the same transaction as the lease check."""
        claim = claim or current_claim.get()
        row = EventRow(task_id=task_id, type=type, payload=payload, ts=_now())
        async with self._sessions() as s:
            task = await s.get(TaskRow, task_id)
            if task is not None:
                held = task.lease_owner is not None
                if (claim and task.lease_token != claim.token) or (not claim and held):
                    raise StaleLease(task_id)
            s.add(row)
            await s.commit()
        return _event(row)

    # -- leases -----------------------------------------------------------

    async def claim(
        self,
        task_id: str,
        *,
        expected: TaskStatus,
        to: TaskStatus,
        owner: str,
        ttl_seconds: float,
    ) -> Claim | None:
        """Take the task: move it to ``to`` and write the lease, in one statement.

        Only a task nobody holds can be claimed. ``lease_token`` goes up by one,
        and that number is the claim: every write this worker makes for the
        task carries it, and stops working the moment someone else claims.
        """
        now = _now()
        stmt = (
            update(TaskRow)
            .where(
                TaskRow.id == task_id,
                TaskRow.status == expected.value,
                TaskRow.lease_owner.is_(None),
            )
            .values(
                status=to.value,
                lease_owner=owner,
                lease_until=now + timedelta(seconds=ttl_seconds),
                lease_token=TaskRow.lease_token + 1,
                updated_at=now,
            )
            .returning(TaskRow.lease_token)
        )
        async with self._sessions() as s:
            token = (await s.execute(stmt)).scalar_one_or_none()
            await s.commit()
        return Claim(task_id=task_id, token=int(token)) if token is not None else None

    async def renew(self, claim: Claim, *, ttl_seconds: float) -> bool:
        """Push the lease out. ``False`` means the lease is no longer this claim's."""
        stmt = (
            update(TaskRow)
            .where(
                TaskRow.id == claim.task_id,
                TaskRow.lease_token == claim.token,
                TaskRow.lease_owner.is_not(None),
            )
            .values(lease_until=_now() + timedelta(seconds=ttl_seconds))
            .returning(TaskRow.id)
        )
        async with self._sessions() as s:
            hit = (await s.execute(stmt)).scalar_one_or_none()
            await s.commit()
        return hit is not None

    async def release(self, claim: Claim) -> bool:
        """Give the task back at the end of a stage. A stale claim releases nothing."""
        stmt = (
            update(TaskRow)
            .where(TaskRow.id == claim.task_id, TaskRow.lease_token == claim.token)
            .values(lease_owner=None, lease_until=None, updated_at=_now())
            .returning(TaskRow.id)
        )
        async with self._sessions() as s:
            hit = (await s.execute(stmt)).scalar_one_or_none()
            await s.commit()
        return hit is not None

    async def reclaim_expired(
        self, *, max_recoveries: int, now: datetime | None = None
    ) -> list[Reclaimed]:
        """Take back every task whose lease ran out.

        Each row is reset with a statement guarded by its own token, so two
        workers sweeping at once cannot both reclaim the same task. The task
        goes back to its stage's entry state, or to FAILED once it has been
        reclaimed ``max_recoveries`` times: a task that keeps killing its
        worker must not loop forever.
        """
        now = now or _now()
        candidates = (
            select(TaskRow)
            .where(
                TaskRow.lease_owner.is_not(None),
                TaskRow.lease_until < now,
                TaskRow.status.in_([st.value for st in LEASED_STATES]),
            )
            .order_by(TaskRow.lease_until)
        )
        taken: list[Reclaimed] = []
        async with self._sessions() as s:
            rows = (await s.execute(candidates)).scalars().all()
            for row in rows:
                # Read before writing: the RETURNING below refreshes ``row`` in place.
                previous = TaskStatus(row.status)
                previous_owner = row.lease_owner
                previous_token = row.lease_token
                exhausted = (row.recoveries or 0) + 1 > max_recoveries
                to = TaskStatus.FAILED if exhausted else RECLAIM_TARGET[previous]
                stmt = (
                    update(TaskRow)
                    .where(
                        TaskRow.id == row.id,
                        TaskRow.lease_token == previous_token,
                        TaskRow.lease_owner.is_not(None),
                    )
                    .values(
                        status=to.value,
                        lease_owner=None,
                        lease_until=None,
                        recoveries=TaskRow.recoveries + 1,
                        updated_at=now,
                    )
                    .returning(TaskRow)
                )
                updated = (await s.execute(stmt)).scalar_one_or_none()
                if updated is not None:
                    taken.append(
                        Reclaimed(
                            task=_task(updated),
                            previous_status=previous,
                            previous_owner=previous_owner,
                            exhausted=exhausted,
                        )
                    )
            await s.commit()
        return taken

    async def list_events(
        self, *, task_id: str | None = None, after_id: int = 0, limit: int = 500
    ) -> list[Event]:
        stmt = select(EventRow).where(EventRow.id > after_id).order_by(EventRow.id).limit(limit)
        if task_id is not None:
            stmt = stmt.where(EventRow.task_id == task_id)
        async with self._sessions() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_event(r) for r in rows]


class ApprovalRepo:
    """Approval envelopes. Every state change here is a single atomic UPDATE.

    That is the whole concurrency story: two browser tabs answering the same
    gate, or a decision racing the expiry sweep, resolve to one winner because
    only one statement can move a row out of ``pending``.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self,
        *,
        task_id: str,
        kind: ApprovalKind,
        subject: str,
        payload: dict[str, Any],
        payload_hash: str,
        status: ApprovalStatus,
        policy: str,
        reason: str,
        decided_by: str | None,
        ttl_seconds: float,
    ) -> Approval:
        now = _now()
        row = ApprovalRow(
            id=uuid.uuid4().hex,
            task_id=task_id,
            kind=kind.value,
            subject=subject[:500],
            payload=payload,
            payload_hash=payload_hash,
            status=status.value,
            policy=policy,
            reason=reason,
            decided_by=decided_by,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            decided_at=None if status is ApprovalStatus.PENDING else now,
        )
        async with self._sessions() as s:
            s.add(row)
            await s.commit()
        return _approval(row)

    async def get(self, approval_id: str) -> Approval | None:
        async with self._sessions() as s:
            row = await s.get(ApprovalRow, approval_id)
        return _approval(row) if row else None

    async def list_for_task(self, task_id: str) -> list[Approval]:
        stmt = (
            select(ApprovalRow)
            .where(ApprovalRow.task_id == task_id)
            .order_by(ApprovalRow.created_at)
        )
        async with self._sessions() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [_approval(r) for r in rows]

    async def latest_approved(self, task_id: str, kind: ApprovalKind) -> Approval | None:
        """The envelope that authorised this stage, so the next one can read it."""
        stmt = (
            select(ApprovalRow)
            .where(
                ApprovalRow.task_id == task_id,
                ApprovalRow.kind == kind.value,
                ApprovalRow.status == ApprovalStatus.APPROVED.value,
            )
            .order_by(ApprovalRow.decided_at.desc())
            .limit(1)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
        return _approval(row) if row else None

    async def decide(
        self, approval_id: str, *, to: ApprovalStatus, decided_by: str
    ) -> Approval | None:
        """Claim a pending, unexpired envelope. ``None`` means someone else won."""
        now = _now()
        stmt = (
            update(ApprovalRow)
            .where(
                ApprovalRow.id == approval_id,
                ApprovalRow.status == ApprovalStatus.PENDING.value,
                ApprovalRow.expires_at > now,
            )
            .values(status=to.value, decided_by=decided_by, decided_at=now)
            .returning(ApprovalRow)
        )
        async with self._sessions() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
            await s.commit()
        return _approval(row) if row else None

    async def expire_due(self) -> list[Approval]:
        """Fail closed: anything still pending past its deadline becomes expired."""
        now = _now()
        stmt = (
            update(ApprovalRow)
            .where(
                ApprovalRow.status == ApprovalStatus.PENDING.value,
                ApprovalRow.expires_at <= now,
            )
            .values(status=ApprovalStatus.EXPIRED.value, decided_by="system:expiry", decided_at=now)
            .returning(ApprovalRow)
        )
        async with self._sessions() as s:
            rows = (await s.execute(stmt)).scalars().all()
            await s.commit()
        return [_approval(r) for r in rows]
