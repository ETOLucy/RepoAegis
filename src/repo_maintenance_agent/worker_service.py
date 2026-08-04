from __future__ import annotations

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.runtime import RuntimeComponents
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
    )
    return await worker.run_once()
