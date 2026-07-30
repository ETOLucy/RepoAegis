# System Architecture

The authoritative product design is [Repo_Maintenance_Agent_Design.md](../Repo_Maintenance_Agent_Design.md). This document maps that design to executable code.

## Control and Execution Planes

```text
CLI / GitHub event
        |
        v
FastAPI API -----> PostgreSQL task state + queue rows
                        |
                        v
                  leased worker pool
                        |
                        v
               LangGraph state machine
                        |
          +-------------+-------------+
          |             |             |
      model gateway  hybrid search  tool gateway
                                      |
                         +------------+------------+
                         |                         |
                    Git/GitHub                Docker sandbox
```

The control plane owns identity, state, policy, orchestration, evidence, and evaluation. Repository processes run only in assigned workspaces or hardened sandbox containers. The model never receives a raw shell.

## Agent Graph

```text
pending -> intake -> research -> planning
                                  |
                         high/critical risk
                                  v
                           needs_approval
                                  |
                     plan-hash-bound decision
                                  v
coding -> verifying -> reviewing -> delivering -> completed
   ^          |            |
   +----------+------------+
      bounded correction loop
```

Each node consumes and returns `RepoTaskState`; it does not pass an unbounded chat transcript. Routing is deterministic and iteration-limited. A task approved through the API re-enters the graph at `coding`, which fixes the human-in-the-loop resume boundary without replaying intake or planning.

## Persistence and Queue Semantics

`SqlTaskRepository` uses `(tenant_id, task_id)` as the primary identity and optimistic versions for compare-and-swap updates. Creation writes the task and queue row in one transaction. Approval writes the decision and restores a runnable queue row in one transaction.

Workers claim rows with `FOR UPDATE SKIP LOCKED`. Every claim and heartbeat rotates a random lease ID. Ack and nack require the current lease ID, so an expired worker cannot commit after a replacement worker has taken ownership. Retries use capped exponential backoff; exhausted work is dead-lettered.

These rules provide at-least-once execution with fenced side effects. Tool writes add idempotency keys for exactly-once observable outcomes where supported.

## Search

`SearchRouter` selects lexical, semantic, symbol, or history retrieval based on query shape. `HybridSearchService` runs selected retrievers concurrently and applies deterministic reciprocal-rank fusion.

Every OpenSearch query injects server-owned filters:

```text
tenant_id AND repo_id AND commit_sha AND allowed_paths
```

Allowed paths match both an exact file and its directory prefix. Local lexical retrieval resolves every path under the assigned workspace. Result IDs include the immutable commit.

## Tool and Sandbox Boundary

The Tool Gateway checks:

1. task, tenant, repository, and commit identity
2. agent role and requested permission
3. plan-bound approval for GitHub writes
4. workspace containment for all path-like arguments
5. idempotency replay before a write
6. recursive output redaction before persistence

`ProcessRunner` uses `create_subprocess_exec`, an executable allowlist, bounded output, timeouts, and a sanitized environment. `GitPatchApplier` reads `git apply --numstat`, rejects undeclared paths, runs `--check`, then applies.

`DockerSandbox` requires digest-pinned images and enforces non-root UID, read-only root filesystem, dropped capabilities, `no-new-privileges`, PID/CPU/memory limits, a bounded tmpfs, and no network by default. A trusted custom seccomp profile can be supplied explicitly; otherwise Docker's default seccomp policy remains active. `SandboxVerifier` derives setup/test/lint commands from repository profiles. Dependency setup is isolated and auditable; test and lint containers are offline. Failures are classified as code, environment, or infrastructure.

## Model Boundary

Agent outputs are strict Pydantic schemas. The OpenAI gateway uses structured Responses parsing and sets `store=False`. API keys remain `SecretStr` values and enter only the provider client. Repository content and issue text are explicitly framed as untrusted data in system instructions.

## API Boundary

FastAPI provides create, read, cancel, and approval operations. Bearer authentication maps tokens to tenants, and repository lookup always includes the authenticated tenant. Cross-tenant IDs produce the same 404 as unknown IDs. Write schemas reject extra fields. Production disables docs and OpenAPI, validates Host headers, and returns generic conflict errors.

## Observability and Evaluation

Structured traces include model, prompt, tool-schema, and policy versions. Recursive redaction removes credentials before events enter a sink. Metrics use normalized labels.

Evaluation keeps hard and soft signals separate. Tests, patch application, regressions, and unauthorized calls are hard gates. Retrieval, latency, cost, and model-based review are optimization metrics.

### Evaluation Operations

The implemented Harness is separate from the LangGraph runtime:

```text
versioned suite + observations
              |
              v
       EvaluationHarness
        /      |       \
 bounded   stable    classified
 parallel  ordering  retry/failure
        \      |       /
              v
 aggregate -> baseline delta -> release gates
              |
        +-----+------+
        |            |
   SQL evidence   JSON/Markdown
        |
   tenant-scoped API
        |
   operations console
```

`EvaluationHarness` executes through a `CaseExecutor` protocol. The included
`ObservationExecutor` makes checked-in fixtures and CI deterministic; a live Agent Graph adapter
can implement the same contract without changing aggregation, storage, APIs, or the console.

Each run records the immutable suite, case results, deterministic seed, provider/model identity,
prompt/tool-schema/policy/dataset versions, and a normalized environment fingerprint. Cases execute
under a semaphore. Only timeout and infrastructure failures retry. Result ordering always follows
the suite manifest.

Aggregates include resolution, Recall@10, MRR, unauthorized-call and regression rates, p50/p95
latency, calls, and tokens. Release gates combine an absolute resolution floor, baseline regression
limit, safety limits, privacy findings, and terminal infrastructure/invalid-output failures.
Missing baselines remain explicit rather than becoming a zero delta.

Evaluation rows use `(tenant_id, run_id)` identity and optimistic versions. Replay creates a new run
containing selected cases and links to the source run without mutating prior evidence. JSON response
models omit tenant identity; Markdown exports use deterministic ordering.

The zero-build console is served from package-owned HTML, CSS, and JavaScript. It consumes only
versioned APIs, stores bearer identity in JavaScript memory, applies a restrictive Content Security
Policy, and remains available when production disables interactive OpenAPI documentation.

## Dependency Direction

```text
api / cli / worker
        -> graph
           -> agents
              -> domain ports
                 <- tools / search / sandbox / storage adapters
```

`domain` imports no FastAPI, LangGraph, OpenSearch, Docker, GitHub, or OpenAI implementation. External systems remain replaceable behind typed boundaries.
