from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from repo_maintenance_agent.domain.errors import LeaseConflict
from repo_maintenance_agent.storage.sql import Base, QueueRow, SqlTaskQueue


async def run_load_scenario(
    *,
    job_count: int,
    seed: int,
    database_path: Path,
) -> dict[str, object]:
    if job_count not in {500, 1_000}:
        raise ValueError("load profile must contain exactly 500 or 1000 jobs")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(engine)
    queue = SqlTaskQueue(
        engine,
        lease_duration=timedelta(seconds=30),
        max_attempts=3,
    )
    tenant_id = "synthetic-load"
    now = datetime(2026, 8, 6, tzinfo=UTC)
    task_ids = [f"job-{index:04d}" for index in range(job_count)]
    random.Random(seed).shuffle(task_ids)  # noqa: S311 - deterministic load schedule
    for task_id in task_ids:
        inserted = await queue.enqueue(tenant_id, task_id, now=now)
        if not inserted:
            raise RuntimeError("synthetic task enqueue was not unique")

    counters = {
        "success_jobs": 0,
        "retried_jobs": 0,
        "stale_lease_jobs": 0,
        "worker_restart_jobs": 0,
        "stale_write_rejections": 0,
    }
    started = monotonic()
    terminal = 0
    while terminal < job_count:
        lease = await queue.claim(
            "worker-primary",
            frozenset({tenant_id}),
            now=now,
        )
        if lease is None:
            raise RuntimeError("queue lost a non-terminal synthetic task")
        index = int(lease.task_id.removeprefix("job-"))
        scenario = _scenario(index)
        if scenario == "retry" and lease.attempt == 1:
            counters["retried_jobs"] += 1
            await queue.nack(lease, retry_at=now)
            continue
        if scenario in {"stale", "restart"} and lease.attempt == 1:
            if scenario == "stale":
                counters["stale_lease_jobs"] += 1
            else:
                counters["worker_restart_jobs"] += 1
                queue = SqlTaskQueue(
                    engine,
                    lease_duration=timedelta(seconds=30),
                    max_attempts=3,
                )
            now += timedelta(seconds=31)
            replacement = await queue.claim(
                "worker-replacement",
                frozenset({tenant_id}),
                now=now,
            )
            if replacement is None or replacement.task_id != lease.task_id:
                raise RuntimeError("expired synthetic lease was not reclaimed")
            try:
                await queue.ack(lease)
            except LeaseConflict:
                counters["stale_write_rejections"] += 1
            else:
                raise RuntimeError("stale synthetic lease unexpectedly wrote terminal state")
            await queue.ack(replacement)
            terminal += 1
            continue

        await queue.ack(lease)
        if scenario == "success":
            counters["success_jobs"] += 1
        terminal += 1

    duration_ms = int((monotonic() - started) * 1_000)
    with Session(engine) as session:
        pending = session.scalar(select(func.count()).select_from(QueueRow)) or 0
        dead_letter = (
            session.scalar(
                select(func.count())
                .select_from(QueueRow)
                .where(QueueRow.dead_lettered.is_(True))
            )
            or 0
        )
    engine.dispose()
    return {
        "schema_version": "repoaegis-synthetic-load/v1",
        "evidence_kind": "synthetic-infrastructure-load-not-model-quality",
        "seed": seed,
        "job_count": job_count,
        **counters,
        "terminal_jobs": terminal,
        "pending_jobs": int(pending),
        "dead_letter_jobs": int(dead_letter),
        "lost_terminal_jobs": job_count - terminal,
        "duration_ms": duration_ms,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "logical_cpu_count": os.cpu_count(),
            "storage": "sqlite",
        },
    }


def _scenario(index: int) -> str:
    if index % 100 == 0:
        return "restart"
    if index % 50 == 1:
        return "stale"
    if index % 20 == 2:
        return "retry"
    return "success"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic SQL queue load without model calls"
    )
    parser.add_argument("--jobs", type=int, choices=(500, 1_000), required=True)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    report = asyncio.run(
        run_load_scenario(
            job_count=args.jobs,
            seed=args.seed,
            database_path=args.database,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    print(
        f"jobs={report['job_count']} terminal={report['terminal_jobs']} "
        f"pending={report['pending_jobs']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
