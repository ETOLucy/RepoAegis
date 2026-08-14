from __future__ import annotations

import asyncio
import builtins
from datetime import datetime
from typing import Protocol

from sqlalchemy import DateTime, Engine, Integer, String, Text, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from repo_maintenance_agent.domain.errors import ConcurrentUpdate, ResourceNotFound
from repo_maintenance_agent.evaluation.models import EvaluationRun
from repo_maintenance_agent.storage.sql import Base


class EvaluationRepository(Protocol):
    async def create(self, run: EvaluationRun) -> EvaluationRun: ...

    async def get(self, tenant_id: str, run_id: str) -> EvaluationRun: ...

    async def list(self, tenant_id: str, *, limit: int = 50) -> list[EvaluationRun]: ...

    async def save(
        self,
        run: EvaluationRun,
        *,
        expected_version: int,
    ) -> EvaluationRun: ...


class InMemoryEvaluationRepository:
    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], EvaluationRun] = {}
        self._lock = asyncio.Lock()

    async def create(self, run: EvaluationRun) -> EvaluationRun:
        key = (run.tenant_id, run.run_id)
        async with self._lock:
            if key in self._runs:
                raise ConcurrentUpdate("evaluation run already exists")
            self._runs[key] = run
        return run

    async def get(self, tenant_id: str, run_id: str) -> EvaluationRun:
        async with self._lock:
            run = self._runs.get((tenant_id, run_id))
        if run is None:
            raise ResourceNotFound("evaluation run not found")
        return run

    async def list(self, tenant_id: str, *, limit: int = 50) -> list[EvaluationRun]:
        if not 1 <= limit <= 200:
            raise ValueError("evaluation list limit must be between 1 and 200")
        async with self._lock:
            runs = [run for run in self._runs.values() if run.tenant_id == tenant_id]
        return sorted(
            runs,
            key=lambda run: (run.created_at, run.run_id),
            reverse=True,
        )[:limit]

    async def save(
        self,
        run: EvaluationRun,
        *,
        expected_version: int,
    ) -> EvaluationRun:
        key = (run.tenant_id, run.run_id)
        if run.version <= expected_version:
            raise ConcurrentUpdate("new evaluation version must advance")
        async with self._lock:
            current = self._runs.get(key)
            if current is None:
                raise ResourceNotFound("evaluation run not found")
            if current.version != expected_version:
                raise ConcurrentUpdate("evaluation run version is stale")
            self._runs[key] = run
        return run


class EvaluationRunRow(Base):
    __tablename__ = "repo_agent_evaluation_runs"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    suite_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state_json: Mapped[str] = mapped_column(Text)


class SqlEvaluationRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def create(self, run: EvaluationRun) -> EvaluationRun:
        return await asyncio.to_thread(self._create, run)

    def _create(self, run: EvaluationRun) -> EvaluationRun:
        try:
            with Session(self._engine) as session:
                session.add(_to_row(run))
                session.commit()
        except IntegrityError as error:
            raise ConcurrentUpdate("evaluation run already exists") from error
        return run

    async def get(self, tenant_id: str, run_id: str) -> EvaluationRun:
        return await asyncio.to_thread(self._get, tenant_id, run_id)

    def _get(self, tenant_id: str, run_id: str) -> EvaluationRun:
        with Session(self._engine) as session:
            row = session.scalar(
                select(EvaluationRunRow).where(
                    EvaluationRunRow.tenant_id == tenant_id,
                    EvaluationRunRow.run_id == run_id,
                )
            )
        if row is None:
            raise ResourceNotFound("evaluation run not found")
        return EvaluationRun.model_validate_json(row.state_json)

    async def list(self, tenant_id: str, *, limit: int = 50) -> list[EvaluationRun]:
        return await asyncio.to_thread(self._list, tenant_id, limit)

    def _list(self, tenant_id: str, limit: int) -> builtins.list[EvaluationRun]:
        if not 1 <= limit <= 200:
            raise ValueError("evaluation list limit must be between 1 and 200")
        with Session(self._engine) as session:
            rows = session.scalars(
                select(EvaluationRunRow)
                .where(EvaluationRunRow.tenant_id == tenant_id)
                .order_by(
                    EvaluationRunRow.created_at.desc(),
                    EvaluationRunRow.run_id.desc(),
                )
                .limit(limit)
            ).all()
        return [EvaluationRun.model_validate_json(row.state_json) for row in rows]

    async def save(
        self,
        run: EvaluationRun,
        *,
        expected_version: int,
    ) -> EvaluationRun:
        return await asyncio.to_thread(self._save, run, expected_version)

    def _save(self, run: EvaluationRun, expected_version: int) -> EvaluationRun:
        if run.version <= expected_version:
            raise ConcurrentUpdate("new evaluation version must advance")
        with Session(self._engine) as session:
            result = session.execute(
                update(EvaluationRunRow)
                .where(
                    EvaluationRunRow.tenant_id == run.tenant_id,
                    EvaluationRunRow.run_id == run.run_id,
                    EvaluationRunRow.version == expected_version,
                )
                .values(
                    status=run.status.value,
                    version=run.version,
                    state_json=run.model_dump_json(),
                )
            )
            if not isinstance(result, CursorResult) or result.rowcount != 1:
                session.rollback()
                raise ConcurrentUpdate("evaluation run version is stale")
            session.commit()
        return run


def _to_row(run: EvaluationRun) -> EvaluationRunRow:
    return EvaluationRunRow(
        tenant_id=run.tenant_id,
        run_id=run.run_id,
        suite_id=run.suite.suite_id,
        status=run.status.value,
        version=run.version,
        created_at=run.created_at,
        state_json=run.model_dump_json(),
    )
