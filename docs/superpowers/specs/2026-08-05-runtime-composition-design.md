# Runtime Composition Design

## Scope

This increment turns the existing API, SQL queue, worker, and LangGraph contracts into one explicit
runtime assembly. It proves that creating a task makes durable work available to an independently
running worker. Workspace lifecycle, remote Git writes, and Draft PR creation remain subsequent
increments and are recorded as such in the claim-to-evidence matrix.

## Architecture

`RuntimeComponents` is the composition root shared by the API and worker entry points. It owns the
SQL engine, task repository, task queue, evaluation repository, and task executor. Construction is
side-effect free until the factory is called; the factory creates required storage and validates
that model-backed graph execution has the dependencies it needs.

The API receives the task repository. The SQL repository keeps its existing atomic task-plus-queue
insert, while the composition root guarantees that the worker queue uses the same engine. The
worker process receives that repository and queue plus a `TaskExecutor`, so it can claim work and
advance the real graph without sharing an in-process lifecycle with FastAPI.

## Data Flow And Recovery

1. The API asks the SQL repository to create a version-zero task.
2. The repository inserts the task and queue row in one transaction.
3. A worker claims the row using the existing fenced lease contract.
4. The executor invokes the graph and the worker persists the returned state.
5. The current save-then-ack boundary remains explicitly partial until the following transaction
   increment adds atomic completion or replay-safe reconciliation.

Task creation and initial enqueue are atomic. The later recovery increment still owns atomic or
replay-safe worker completion because state save and queue acknowledgement are currently separate.

## Deployment

Compose adds a dedicated `worker` service from the same digestable application image. It receives
the database, model, artifact, and tenant-scope configuration but exposes no port. API and worker
wait for PostgreSQL health. The worker command polls with bounded idle delay and exits on normal
container termination.

## Testing

TDD proceeds from observable boundaries:

- an API/runtime integration test must fail until the API repository and worker queue share storage;
- a runtime-factory test must fail until API and worker share compatible SQL components;
- a worker-service test must fail until one claimed task reaches the executor and persists its next
  graph state;
- Compose configuration validation must show a dedicated worker service with no published ports.

Every code change updates the operational tutorial and claim-to-evidence matrix in the same commit.

## Non-Goals

This increment does not claim the full repository-maintenance loop, atomic remote side effects,
containerized repository verification, live retrieval ingestion, or real evaluation quality. Those
remain required by the umbrella goal and are implemented in later independently verifiable steps.
