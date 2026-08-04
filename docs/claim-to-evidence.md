# Claim-to-Evidence Matrix

This matrix is the authoritative delivery ledger for RepoAegis. A green unit test proves only the
named contract. It does not prove that the production application assembled that component or that
a repository task completed end to end.

Status meanings:

- **Verified**: implementation, automated test, and current-commit runtime evidence exist.
- **Under validation**: useful implementation or tests exist, but production wiring or runtime evidence is
  incomplete.
- **Target invariant**: the documented behavior has no adequate implementation or evidence.
- **Under validation** (fixture only): deterministic reporting evidence exists but is not agent-quality evidence.

The initial audit was performed on 2026-08-05 from RepoAegis commit `852eda6`. Baseline evidence was
109 passing tests, clean Ruff, and clean strict Mypy. These checks establish a healthy starting
point, not product completion.

## Repository Task Lifecycle

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| Authenticated task creation is tenant scoped | `api/app.py`, `api/auth.py`, `storage/sql.py` | `tests/integration/api/test_tasks.py`, `tests/integration/storage/test_sql.py` | API integration tests | **Verified** for API/storage contract |
| Task creation atomically creates queue work | `SqlTaskRepository._create` writes `TaskRow` and `QueueRow` in one transaction | SQL integration tests | Database test evidence | **Verified** for create transaction |
| API and worker storage share one explicit composition root | `runtime.build_runtime` binds SQL task, queue, and evaluation adapters to one engine | `tests/integration/test_runtime.py`, `tests/unit/test_main.py` | API submission is claimed through the runtime queue | **Verified** storage composition boundary |
| Worker service claims, executes, and persists through shared SQL adapters | `worker_service.run_worker_once`, `run_worker_forever`, `Worker` | worker service and worker tests | deterministic executor over real SQLite queue/repository | **Verified** service and polling boundary |
| Worker execution materializes before invoking a task-scoped graph | `WorkspaceGraphExecutor`, `ProductionGraphFactory`, `WorkspaceAdapter`, `LangGraphExecutor` | runtime executor, workspace, node, and adapter tests | local bare remote plus real LangGraph state machine | **Under validation**: production components are assembled; live model/container task evidence remains |
| A submitted task leaves `pending` and runs through the graph | `build_worker_runtime` and `repo-agent-worker` assemble queue-to-graph execution | component and integration tests | no Compose or live task run yet | **Under validation** |
| Runtime clones the declared immutable commit into an isolated workspace | `WorkspaceAdapter` runs as a registry-scoped control tool through `ToolGateway` | `tests/integration/tools/test_workspace.py` | credential-free local bare-remote clone, HEAD verification, isolated path, branch and replay evidence | **Verified** materialization boundary |
| Runtime creates a branch, commits allowlisted files, and pushes without force | `WorkspaceAdapter`, approval-gated `GitToolAdapter`, PR node gateway sequence | workspace, Git bare-remote, node, policy, and production factory tests | local bare remote contains the returned commit on the task branch | **Verified** local Git delivery boundary |
| PR node invokes a Draft PR adapter after commit and push | PR node calls `create_draft_pr`; production selects `GitHubCliAdapter` with credentials or a persistent local record adapter without them | node, GitHub adapter, local record, and production factory tests | local record evidence only | **Under validation**: real Draft PR smoke test is not authorized/configured |
| Credential-free fake-remote end-to-end demo | no dedicated fixture remote workflow | none | none | **Target invariant** |

## Safety, Approval, and Recovery

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| Search, patch application, and verification cross ToolGateway | `AgentRuntime` holds one gateway; `SearchAdapter`, `PatchArtifactAdapter`, and `VerificationAdapter` implement scoped tools | `tests/unit/agents/test_nodes.py`, `tests/unit/tools/test_agent_actions.py`, gateway/policy tests | focused adapter and node evidence | **Verified** node and adapter boundary |
| Successful tool-result idempotency survives process reconstruction | `SqlOperationLog` stores append-once typed results; runtime composition injects it | SQL operation-log and gateway replay tests | a reconstructed log instance replays the first stored result | **Verified** for successful tool results |
| Duplicate delivery cannot repeat commit, push, or PR | SQL receipts and stable keys are assembled; commit reconciles its operation trailer and push is idempotent | Git and operation-log tests | commit/push local evidence | **Under validation**: real GitHub create-after-effect/before-receipt reconciliation remains |
| Queue leases fence stale workers | SQL queue rotates lease IDs and fences ack/nack | queue and SQL tests | database integration evidence | **Verified** at queue boundary |
| Task state save and queue consumption are atomic and fenced | `SqlTaskCompletion` updates by expected version and deletes by live lease ID/expiry in one transaction | SQL completion, stale lease, worker delegation, and queue tests | successful completion consumes work; expired completion rolls back state | **Verified** SQL completion boundary |
| Approval binds a reviewable plan and permissions | approval binds `plan_hash` | API and route tests | API test evidence | **Under validation**: response omits plan, risk reasons, evidence, verification plan, commit and tool scope |
| Deterministic rules raise risk for sensitive changes | planning prompt asks the model to flag risks | agent-node tests | none | **Target invariant** deterministic risk engine |
| Review sees actual diff and changed source | review receives changed file names and verification summary | node tests | none | **Target invariant** actual diff/source evidence |
| Agent correction loop is bounded | graph routes and `max_iterations` model field | route/workflow tests | graph test evidence | **Under validation**: coding is one-shot patch generation and has no controlled read/tool loop |

## Retrieval, Sandbox, and Evaluation

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| Lexical retrieval is real and workspace contained | `LocalLexicalSearch` | local search unit/integration tests | local adapter tests | **Verified** adapter contract |
| BM25, vector, symbol, and history routes are real | router declares all routes; one generic OpenSearch match adapter exists | routing/query tests | none | **Target invariant** dedicated indexes, ingestion, embeddings, symbols, history, and quality evidence |
| Search enforces tenant/repository/commit/path scope | OpenSearch query injects filters; local adapter constrains paths | adapter tests | query-construction evidence | **Under validation**: no production ingestion/service assembly |
| Untrusted verification runs in a hardened container | `DockerSandbox`, `SandboxVerifier`, Python profile | sandbox command/profile tests | no current Docker execution evidence | **Under validation**: only Python image configured; setup network policy and production wiring need verification |
| Compose starts the complete runtime | Compose currently starts API, PostgreSQL, and OpenSearch | compose syntax not yet gated | none | **Target invariant**: worker and restricted sandbox-runner services remain |
| Artifact bytes and metadata survive store reconstruction | `SqlFileArtifactStore` uses content-addressed files and tenant-scoped SQL metadata | `tests/integration/storage/test_artifacts_sql.py`, artifact adapter tests | reconstructed store retrieves and hash-checks original bytes | **Verified** storage boundary |
| Evaluation executes the real Agent Graph against hidden tests | `EvaluationHarness` accepts a `CaseExecutor` | harness tests | deterministic example report | **Target invariant** live graph executor and hidden oracle integration |
| Checked-in evaluation observations prove agent quality | `ObservationExecutor` and example observations | deterministic evaluation tests | example JSON/Markdown | **Under validation** (fixture only); never valid as model-quality evidence |

## RepoAegis to AegisEvo Boundary

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| RepoAegis exports an immutable versioned target pack | none | none | none | **Target invariant** `repoaegis-target-pack/v2` contract and export |
| Target pack binds runtime, baseline, images, evaluator, benchmark, tools, and policy digests | none | none | none | **Target invariant** |
| AegisEvo invokes the pinned RepoAegis runtime rather than a second coding agent | current AegisEvo Harness has its own repository action loop | Harness tests | no joint demo | **Target invariant** architectural replacement |
| One joint demo completes task, exports pack, searches, reports, and promotes | none | none | none | **Target invariant** |

## Release and Repository Identity

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| README accurately separates implemented, experimental, and roadmap features | README currently presents several partial items as end-to-end guarantees | documentation tests are limited | none | **Under validation**; implementation must be completed before final wording is accepted |
| README badges use real data sources | CI, Python, and License badges have real targets | link checks not yet present | public CI source exists | **Under validation**; release badge waits for an actual release |
| Version, changelog, tag, release notes, and target-pack compatibility are prepared locally | package version is `0.1.0`; no final changelog/compatibility matrix | none | none | **Target invariant** release preparation |
| Remote metadata and release are updated | intentionally not authorized in unattended work | none | none | **Under validation**: local proposal is required before user approval |

## Completion Rule

RepoAegis is not interview-ready until every reasonable **Under validation** or **Target invariant** item above has
implementation, tests, and runtime evidence. Claims are not closed by removing them from the README.
Unsafe, unverifiable, or logically incorrect claims must instead be documented with the reason for
changing them.
