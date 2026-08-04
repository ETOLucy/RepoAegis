from __future__ import annotations

import asyncio
import builtins
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Engine,
    Integer,
    String,
    Text,
    delete,
    or_,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from repo_maintenance_agent.domain.errors import (
    ConcurrentUpdate,
    LeaseConflict,
    ResourceNotFound,
)
from repo_maintenance_agent.domain.models import RepoTaskState, TaskStatus, ToolResult
from repo_maintenance_agent.storage.queue import QueueLease


class Base(DeclarativeBase):
    pass


class TaskRow(Base):
    __tablename__ = "repo_agent_tasks"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo_id: Mapped[str] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    state_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QueueRow(Base):
    __tablename__ = "repo_agent_queue"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    dead_lettered: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class OperationRow(Base):
    __tablename__ = "repo_agent_operations"

    operation_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ArtifactRow(Base):
    __tablename__ = "repo_agent_artifacts"

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    relative_path: Mapped[str] = mapped_column(String(512))
    media_type: Mapped[str] = mapped_column(String(255))
    content_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SqlTaskRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def create(self, state: RepoTaskState) -> RepoTaskState:
        return await asyncio.to_thread(self._create, state)

    def _create(self, state: RepoTaskState) -> RepoTaskState:
        row = self._to_row(state)
        try:
            with Session(self._engine) as session:
                session.add(row)
                session.add(
                    QueueRow(
                        tenant_id=state.tenant_id,
                        task_id=state.task_id,
                        available_at=state.created_at,
                        attempts=0,
                        dead_lettered=False,
                    )
                )
                session.commit()
        except IntegrityError as error:
            raise ConcurrentUpdate("task already exists") from error
        return state

    async def get(self, tenant_id: str, task_id: str) -> RepoTaskState:
        return await asyncio.to_thread(self._get, tenant_id, task_id)

    def _get(self, tenant_id: str, task_id: str) -> RepoTaskState:
        with Session(self._engine) as session:
            row = session.scalar(
                select(TaskRow).where(
                    TaskRow.tenant_id == tenant_id,
                    TaskRow.task_id == task_id,
                )
            )
        if row is None:
            raise ResourceNotFound("task not found")
        return RepoTaskState.model_validate_json(row.state_json)

    async def list(self, tenant_id: str, *, limit: int = 50) -> list[RepoTaskState]:
        return await asyncio.to_thread(self._list, tenant_id, limit)

    def _list(self, tenant_id: str, limit: int) -> builtins.list[RepoTaskState]:
        if not 1 <= limit <= 200:
            raise ValueError("task list limit must be between 1 and 200")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(TaskRow)
                .where(TaskRow.tenant_id == tenant_id)
                .order_by(TaskRow.updated_at.desc(), TaskRow.task_id.desc())
                .limit(limit)
            ).all()
        return [RepoTaskState.model_validate_json(row.state_json) for row in rows]

    async def save(self, state: RepoTaskState, expected_version: int) -> RepoTaskState:
        return await asyncio.to_thread(self._save, state, expected_version)

    def _save(self, state: RepoTaskState, expected_version: int) -> RepoTaskState:
        if state.version <= expected_version:
            raise ConcurrentUpdate("new state version must advance")
        values = {
            "repo_id": state.repo_id,
            "status": state.status.value,
            "version": state.version,
            "state_json": state.model_dump_json(),
            "updated_at": datetime.now(UTC),
        }
        with Session(self._engine) as session:
            result = session.execute(
                update(TaskRow)
                .where(
                    TaskRow.tenant_id == state.tenant_id,
                    TaskRow.task_id == state.task_id,
                    TaskRow.version == expected_version,
                )
                .values(**values)
            )
            if not isinstance(result, CursorResult) or result.rowcount != 1:
                session.rollback()
                raise ConcurrentUpdate("task version conflict")
            if (
                state.status is TaskStatus.CODING
                and state.approval is not None
                and state.approval.approved
            ):
                queue_row = session.get(QueueRow, (state.tenant_id, state.task_id))
                if queue_row is None:
                    session.add(
                        QueueRow(
                            tenant_id=state.tenant_id,
                            task_id=state.task_id,
                            available_at=state.updated_at,
                            attempts=0,
                            dead_lettered=False,
                        )
                    )
                else:
                    queue_row.available_at = state.updated_at
                    queue_row.attempts = 0
                    queue_row.lease_id = None
                    queue_row.worker_id = None
                    queue_row.lease_expires_at = None
                    queue_row.dead_lettered = False
            session.commit()
        return state

    @staticmethod
    def _to_row(state: RepoTaskState) -> TaskRow:
        return TaskRow(
            tenant_id=state.tenant_id,
            task_id=state.task_id,
            repo_id=state.repo_id,
            status=state.status.value,
            version=state.version,
            state_json=json.dumps(state.model_dump(mode="json")),
            updated_at=state.updated_at,
        )


class SqlTaskQueue:
    def __init__(
        self,
        engine: Engine,
        *,
        lease_duration: timedelta = timedelta(minutes=5),
        max_attempts: int = 3,
    ) -> None:
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._engine = engine
        self._lease_duration = lease_duration
        self._max_attempts = max_attempts

    async def enqueue(self, tenant_id: str, task_id: str, *, now: datetime) -> bool:
        return await asyncio.to_thread(self._enqueue, tenant_id, task_id, now)

    def _enqueue(self, tenant_id: str, task_id: str, now: datetime) -> bool:
        try:
            with Session(self._engine) as session:
                session.add(
                    QueueRow(
                        tenant_id=tenant_id,
                        task_id=task_id,
                        available_at=_aware(now),
                        attempts=0,
                        dead_lettered=False,
                    )
                )
                session.commit()
        except IntegrityError:
            return False
        return True

    async def claim(
        self,
        worker_id: str,
        tenant_ids: frozenset[str],
        *,
        now: datetime,
    ) -> QueueLease | None:
        return await asyncio.to_thread(self._claim, worker_id, tenant_ids, now)

    def _claim(
        self,
        worker_id: str,
        tenant_ids: frozenset[str],
        now: datetime,
    ) -> QueueLease | None:
        if not tenant_ids:
            return None
        current_time = _aware(now)
        with Session(self._engine) as session:
            row = session.scalar(
                select(QueueRow)
                .where(
                    QueueRow.tenant_id.in_(tenant_ids),
                    QueueRow.dead_lettered.is_(False),
                    QueueRow.attempts < self._max_attempts,
                    QueueRow.available_at <= current_time,
                    or_(
                        QueueRow.lease_id.is_(None),
                        QueueRow.lease_expires_at <= current_time,
                    ),
                )
                .order_by(QueueRow.available_at, QueueRow.task_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            row.attempts += 1
            row.lease_id = str(uuid4())
            row.worker_id = worker_id
            row.lease_expires_at = current_time + self._lease_duration
            session.commit()
            return _row_lease(row)

    async def heartbeat(self, lease: QueueLease, *, now: datetime) -> QueueLease:
        return await asyncio.to_thread(self._heartbeat, lease, now)

    def _heartbeat(self, lease: QueueLease, now: datetime) -> QueueLease:
        current_time = _aware(now)
        if lease.lease_expires_at <= current_time:
            raise LeaseConflict("queue lease expired")
        new_lease_id = str(uuid4())
        new_expiry = current_time + self._lease_duration
        with Session(self._engine) as session:
            result = session.execute(
                update(QueueRow)
                .where(
                    QueueRow.tenant_id == lease.tenant_id,
                    QueueRow.task_id == lease.task_id,
                    QueueRow.lease_id == lease.lease_id,
                )
                .values(lease_id=new_lease_id, lease_expires_at=new_expiry)
            )
            if not isinstance(result, CursorResult) or result.rowcount != 1:
                session.rollback()
                raise LeaseConflict("queue lease is stale")
            session.commit()
        return lease.model_copy(
            update={"lease_id": new_lease_id, "lease_expires_at": new_expiry}
        )

    async def ack(self, lease: QueueLease) -> None:
        await asyncio.to_thread(self._ack, lease)

    def _ack(self, lease: QueueLease) -> None:
        with Session(self._engine) as session:
            result = session.execute(
                delete(QueueRow).where(
                    QueueRow.tenant_id == lease.tenant_id,
                    QueueRow.task_id == lease.task_id,
                    QueueRow.lease_id == lease.lease_id,
                )
            )
            if not isinstance(result, CursorResult) or result.rowcount != 1:
                session.rollback()
                raise LeaseConflict("queue lease is stale")
            session.commit()

    async def nack(self, lease: QueueLease, *, retry_at: datetime) -> bool:
        return await asyncio.to_thread(self._nack, lease, retry_at)

    def _nack(self, lease: QueueLease, retry_at: datetime) -> bool:
        should_retry = lease.attempt < self._max_attempts
        with Session(self._engine) as session:
            result = session.execute(
                update(QueueRow)
                .where(
                    QueueRow.tenant_id == lease.tenant_id,
                    QueueRow.task_id == lease.task_id,
                    QueueRow.lease_id == lease.lease_id,
                )
                .values(
                    available_at=_aware(retry_at),
                    lease_id=None,
                    worker_id=None,
                    lease_expires_at=None,
                    dead_lettered=not should_retry,
                )
            )
            if not isinstance(result, CursorResult) or result.rowcount != 1:
                session.rollback()
                raise LeaseConflict("queue lease is stale")
            session.commit()
        return should_retry


class SqlOperationLog:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def get(self, key: str) -> ToolResult | None:
        return await asyncio.to_thread(self._get, key)

    def _get(self, key: str) -> ToolResult | None:
        with Session(self._engine) as session:
            row = session.get(OperationRow, key)
            if row is None:
                return None
            return ToolResult.model_validate_json(row.result_json).model_copy(
                update={"replayed": True}
            )

    async def put(self, key: str, result: ToolResult) -> None:
        await asyncio.to_thread(self._put, key, result)

    def _put(self, key: str, result: ToolResult) -> None:
        try:
            with Session(self._engine) as session:
                session.add(
                    OperationRow(
                        operation_key=key,
                        result_json=result.model_dump_json(),
                        created_at=datetime.now(UTC),
                    )
                )
                session.commit()
        except IntegrityError:
            return


class SqlTaskCompletion:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def complete(
        self,
        lease: QueueLease,
        state: RepoTaskState,
        *,
        expected_version: int,
    ) -> None:
        await asyncio.to_thread(self._complete, lease, state, expected_version)

    def _complete(
        self,
        lease: QueueLease,
        state: RepoTaskState,
        expected_version: int,
    ) -> None:
        if state.version <= expected_version:
            raise ConcurrentUpdate("new state version must advance")
        if state.tenant_id != lease.tenant_id or state.task_id != lease.task_id:
            raise LeaseConflict("completion scope does not match queue lease")
        completion_time = datetime.now(UTC)
        with Session(self._engine) as session:
            updated = session.execute(
                update(TaskRow)
                .where(
                    TaskRow.tenant_id == state.tenant_id,
                    TaskRow.task_id == state.task_id,
                    TaskRow.version == expected_version,
                )
                .values(
                    repo_id=state.repo_id,
                    status=state.status.value,
                    version=state.version,
                    state_json=state.model_dump_json(),
                    updated_at=datetime.now(UTC),
                )
            )
            if not isinstance(updated, CursorResult) or updated.rowcount != 1:
                session.rollback()
                raise ConcurrentUpdate("task version conflict")
            consumed = session.execute(
                delete(QueueRow).where(
                    QueueRow.tenant_id == lease.tenant_id,
                    QueueRow.task_id == lease.task_id,
                    QueueRow.lease_id == lease.lease_id,
                    QueueRow.lease_expires_at > completion_time,
                )
            )
            if not isinstance(consumed, CursorResult) or consumed.rowcount != 1:
                session.rollback()
                raise LeaseConflict("queue lease is stale")
            session.commit()


def _row_lease(row: QueueRow) -> QueueLease:
    if row.lease_id is None or row.worker_id is None or row.lease_expires_at is None:
        raise LeaseConflict("queue row does not contain an active lease")
    return QueueLease(
        tenant_id=row.tenant_id,
        task_id=row.task_id,
        worker_id=row.worker_id,
        lease_id=row.lease_id,
        attempt=row.attempts,
        lease_expires_at=_restore_timezone(row.lease_expires_at),
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("queue timestamps must include a timezone")
    return value.astimezone(UTC)


def _restore_timezone(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
