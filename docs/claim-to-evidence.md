# Claim-to-Evidence Matrix

This matrix is the authoritative delivery ledger for RepoAegis. A green unit test proves only the
named contract. It does not prove that the production application assembled that component or that
a repository task completed end to end.

Status meanings:

- **Proven**: implementation, automated test, and current-commit runtime evidence exist.
- **Partial**: useful implementation or tests exist, but production wiring or runtime evidence is
  incomplete.
- **Missing**: the documented behavior has no adequate implementation or evidence.
- **Fixture only**: deterministic reporting evidence exists but is not agent-quality evidence.

The initial audit was performed on 2026-08-05 from RepoAegis commit `852eda6`. Baseline evidence was
109 passing tests, clean Ruff, and clean strict Mypy. These checks establish a healthy starting
point, not product completion.

## Repository Task Lifecycle

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| Authenticated task creation is tenant scoped | `api/app.py`, `api/auth.py`, `storage/sql.py` | `tests/integration/api/test_tasks.py`, `tests/integration/storage/test_sql.py` | API integration tests | **Proven** for API/storage contract |
| Task creation atomically creates queue work | `SqlTaskRepository._create` writes `TaskRow` and `QueueRow` in one transaction | SQL integration tests | Database test evidence | **Proven** for create transaction |
| API and worker storage share one explicit composition root | `runtime.build_runtime` binds SQL task, queue, and evaluation adapters to one engine | `tests/integration/test_runtime.py`, `tests/unit/test_main.py` | API submission is claimed through the runtime queue | **Proven** composition boundary; worker graph assembly remains missing |
| Worker service claims, executes, and persists through shared SQL adapters | `worker_service.run_worker_once`, `Worker` | `tests/unit/test_worker_service.py`, `tests/unit/test_worker.py` | deterministic executor over real SQLite queue/repository | **Partial**: service boundary proven; production graph and polling entry point require per-task workspace assembly |
| A submitted task leaves `pending` and runs through the graph | `Worker`, `LangGraphExecutor`, `build_graph` exist separately | worker and graph integration tests | none through `build_application` or Compose | **Missing** production assembly; API creates work but no worker service consumes it |
| Runtime clones the declared immutable commit into an isolated workspace | no workspace manager or clone/materialize adapter is assembled | none | none | **Missing** |
| Runtime creates a branch, commits, and pushes once | current `GitToolAdapter` is read-only | read-only Git adapter tests | none | **Missing** |
| PR node creates a real Draft PR | `GitHubCliAdapter.create_draft_pr` exists | adapter unit tests | none | **Missing** graph wiring; PR node stores generated title/body only |
| Credential-free fake-remote end-to-end demo | no dedicated fixture remote workflow | none | none | **Missing** |

## Safety, Approval, and Recovery

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| Every side effect crosses one ToolGateway | `ToolGateway` and permission policy exist | gateway/policy tests | none | **Missing** in AgentRuntime: coding calls patch applier directly and verification calls verifier directly |
| Idempotency survives process restart | `InMemoryOperationLog` caches successful writes | gateway replay tests | none | **Missing** durable operation log |
| Queue leases fence stale workers | SQL queue rotates lease IDs and fences ack/nack | queue and SQL tests | database integration evidence | **Proven** at queue boundary |
| Crash between repository save and queue ack recovers safely | worker saves, then separately acks | worker tests cover retry basics | none | **Missing** atomic completion/outbox or resumable terminal-state semantics |
| Duplicate delivery cannot repeat patch, push, or PR | adapter-level idempotency key support is in-memory only | limited gateway replay tests | none | **Missing** cross-process and remote-write evidence |
| Approval binds a reviewable plan and permissions | approval binds `plan_hash` | API and route tests | API test evidence | **Partial**: response omits plan, risk reasons, evidence, verification plan, commit and tool scope |
| Deterministic rules raise risk for sensitive changes | planning prompt asks the model to flag risks | agent-node tests | none | **Missing** deterministic risk engine |
| Review sees actual diff and changed source | review receives changed file names and verification summary | node tests | none | **Missing** actual diff/source evidence |
| Agent correction loop is bounded | graph routes and `max_iterations` model field | route/workflow tests | graph test evidence | **Partial**: coding is one-shot patch generation and has no controlled read/tool loop |

## Retrieval, Sandbox, and Evaluation

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| Lexical retrieval is real and workspace contained | `LocalLexicalSearch` | local search unit/integration tests | local adapter tests | **Proven** adapter contract |
| BM25, vector, symbol, and history routes are real | router declares all routes; one generic OpenSearch match adapter exists | routing/query tests | none | **Missing** dedicated indexes, ingestion, embeddings, symbols, history, and quality evidence |
| Search enforces tenant/repository/commit/path scope | OpenSearch query injects filters; local adapter constrains paths | adapter tests | query-construction evidence | **Partial**: no production ingestion/service assembly |
| Untrusted verification runs in a hardened container | `DockerSandbox`, `SandboxVerifier`, Python profile | sandbox command/profile tests | no current Docker execution evidence | **Partial**: only Python image configured; setup network policy and production wiring need verification |
| Compose starts the complete runtime | Compose starts API, PostgreSQL, and OpenSearch | compose syntax not yet gated | none | **Missing** worker and runtime services |
| Artifact metadata survives restart | file bytes persist; metadata is an in-memory dictionary | storage tests | none | **Missing** durable artifact metadata |
| Evaluation executes the real Agent Graph against hidden tests | `EvaluationHarness` accepts a `CaseExecutor` | harness tests | deterministic example report | **Missing** live graph executor and hidden oracle integration |
| Checked-in evaluation observations prove agent quality | `ObservationExecutor` and example observations | deterministic evaluation tests | example JSON/Markdown | **Fixture only**; never valid as model-quality evidence |

## RepoAegis to AegisEvo Boundary

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| RepoAegis exports an immutable versioned target pack | none | none | none | **Missing** `repoaegis-target-pack/v2` contract and export |
| Target pack binds runtime, baseline, images, evaluator, benchmark, tools, and policy digests | none | none | none | **Missing** |
| AegisEvo invokes the pinned RepoAegis runtime rather than a second coding agent | current AegisEvo Harness has its own repository action loop | Harness tests | no joint demo | **Missing** architectural replacement |
| One joint demo completes task, exports pack, searches, reports, and promotes | none | none | none | **Missing** |

## Release and Repository Identity

| Claim | Implementation | Automated test | Runtime evidence | Status / gap |
|---|---|---|---|---|
| README accurately separates implemented, experimental, and roadmap features | README currently presents several partial items as end-to-end guarantees | documentation tests are limited | none | **Partial**; implementation must be completed before final wording is accepted |
| README badges use real data sources | CI, Python, and License badges have real targets | link checks not yet present | public CI source exists | **Partial**; release badge waits for an actual release |
| Version, changelog, tag, release notes, and target-pack compatibility are prepared locally | package version is `0.1.0`; no final changelog/compatibility matrix | none | none | **Missing** release preparation |
| Remote metadata and release are updated | intentionally not authorized in unattended work | none | none | **Blocked by approval after local proposal is complete** |

## Completion Rule

RepoAegis is not interview-ready until every reasonable **Partial** or **Missing** item above has
implementation, tests, and runtime evidence. Claims are not closed by removing them from the README.
Unsafe, unverifiable, or logically incorrect claims must instead be documented with the reason for
changing them.
