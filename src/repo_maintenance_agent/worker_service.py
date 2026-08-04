from __future__ import annotations

import asyncio

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.runtime import RuntimeComponents, build_worker_runtime
from repo_maintenance_agent.worker import Worker, WorkerOutcome


async def run_worker_once(
    settings: Settings,
    *,
    runtime: RuntimeComponents,
) -> WorkerOutcome:
    if runtime.executor is None:
        raise RuntimeError("worker runtime requires a task executor")
    tenant_ids = frozenset(settings.worker_tenant_ids)
    if not tenant_ids:
        raise RuntimeError("worker tenant scope is required")
    worker = Worker(
        worker_id=settings.worker_id,
        tenant_ids=tenant_ids,
        queue=runtime.queue,
        repository=runtime.tasks,
        executor=runtime.executor,
        completion=runtime.completion,
    )
    return await worker.run_once()


async def run_worker_forever(
    settings: Settings,
    *,
    runtime: RuntimeComponents,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        outcome = await run_worker_once(settings, runtime=runtime)
        if outcome is not WorkerOutcome.IDLE:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
        except TimeoutError:
            continue


def main() -> None:
    settings = Settings()
    runtime = build_worker_runtime(settings)
    try:
        asyncio.run(
            run_worker_forever(
                settings,
                runtime=runtime,
                stop=asyncio.Event(),
            )
        )
    except KeyboardInterrupt:
        return
