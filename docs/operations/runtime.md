# Runtime Operations

This operations reference tracks the production assembly as it is implemented. RepoAegis has a
shared SQL composition root, atomic task submission, a separately deployable worker, production
graph factory, and a restricted remote sandbox contract. Live Compose execution is still under
validation, so static topology evidence is not presented as end-to-end evidence.

## Build The Shared Runtime

`repo_maintenance_agent.runtime.build_runtime(Settings(...))` creates one SQL engine and binds the
task repository, task queue, and evaluation repository to it. For file-backed SQLite it creates the
database parent directory. It also creates the artifact directory and SQL schema.

The application factory calls this composition root. `SqlTaskRepository.create()` writes the task
row and its initial queue row in one transaction, so a successful `POST /v1/tasks` is immediately
claimable from the queue built by the same runtime.

## Verify The Submission Boundary

From the isolated worktree, use its source tree with the repository virtual environment:

```powershell
$env:PYTHONPATH = "$PWD/src"
& 'D:\Repos\Agents\RepoAegis\.venv\Scripts\python.exe' -m pytest tests/integration/test_runtime.py -q
```

The test creates a temporary SQLite database, submits through the real FastAPI route, then claims
the returned task identity through `SqlTaskQueue`. It uses no model credential or network request.

`worker_service.run_worker_once()` now composes the existing fenced `Worker` with these shared SQL
adapters and an explicitly injected `TaskExecutor`. Its focused test proves a claimed task is
executed and its advanced state is persisted. The service refuses an empty tenant scope or a runtime
without an executor. A long-running CLI is intentionally not exposed until the production graph
factory can allocate an isolated workspace for each task.

The workspace control adapter is verified against a credential-free local bare remote. It accepts a
repository only when its `repo_id` is in the operator registry, materializes the exact task commit
under a hashed tenant/task directory, and creates a deterministic task branch. The integration test
executes these Git operations through `ToolGateway`; it does not contact an external service.

`WorkspaceGraphExecutor` connects this control tool to the existing LangGraph executor. Current
runtime evidence uses deterministic graph nodes to prove materialize-before-execute ordering and
state completion. It is not model-quality evidence and does not yet establish production node
assembly.

Within agent nodes, research, patch application, and verification are expressed as scoped gateway
calls. Patch content moves by artifact identifier rather than as a tool argument. Successful tool
results now use an append-once SQL operation log and can replay after process reconstruction. File
artifact metadata and remote delivery are still incomplete, so exactly-once end-to-end recovery is
not yet claimed.

The production worker entry point is `repo-agent-worker`. It requires an explicit tenant scope and
operator-owned repository locator registry. Model and graph components initialize only after a task
workspace exists. Compose connects it to `sandbox-runner`, not to Docker. The authenticated runner
accepts only strict resource-bounded requests with a relative workspace below the shared root. A
separate project-owned rootless daemon runs the nested task containers. Worker and daemon networks
are disjoint, neither a daemon port nor a host socket is published, and topology tests enforce these
properties. The current machine's Docker engine did not become ready during verification, so image
build, service health, and a submitted live task remain open evidence items.

## Current Failure Boundary

Initial creation and queue insertion are atomic. Worker state persistence and lease consumption are
also one SQL transaction, fenced by task version, lease ID, and lease expiry. This removes the
save-before-ack crash window. Artifact bytes and metadata survive store reconstruction and are
content-hash checked. RepoAegis still makes no end-to-end exactly-once claim because real Draft PR
create-after-effect/before-receipt reconciliation is incomplete. The injected executor test is
composition evidence, not proof of model execution.

The delivery adapters can commit declared changed files and push the current task branch to `origin`
without force. A credential-free run writes a tenant-scoped local Draft PR record artifact; it must
be reported as a local record, not a real pull request. Real Draft PR creation is selected only when
the dedicated credential is configured and requires a separately authorized smoke test.
