# Evaluation Operations Design

## Purpose

RepoAegis needs an evaluation system that proves behavior across repeatable
repository tasks, not only a function that grades one observation. The same evidence must be
available to CI, operators, and interview reviewers through stable APIs and a lightweight
operations console.

## Scope

This change adds:

- versioned evaluation suites and cases
- concurrent, bounded suite execution
- immutable run provenance and environment fingerprints
- per-case attempts, failure classification, timing, token, and retrieval metrics
- baseline comparison and explicit release-gate decisions
- deterministic replay requests for failed cases
- tenant-scoped SQL persistence and APIs
- a same-origin, zero-build operations console
- JSON and Markdown report export
- an official project README with a verified console screenshot

It does not add a model training system, hosted dataset download service, GPU scheduler, or
general-purpose frontend framework.

## Architecture

```text
CLI / CI / Console
        |
        v
FastAPI evaluation routes
        |
        +---- EvaluationService ---- Executor protocol
        |              |                  |
        |              |                  +---- deterministic observations
        |              |                  +---- future live Agent Graph adapter
        |              |
        |              +---- graders + baseline comparator + release gates
        |
        +---- EvaluationRepository ---- in-memory / SQL adapters
        |
        +---- immutable JSON / Markdown exports
```

The evaluation domain remains independent from FastAPI and SQLAlchemy. Executors return strict
observations; the service owns scheduling, retry policy, aggregation, provenance, and gate
decisions. Persistence is tenant-scoped. API schemas never expose tenant identifiers.

## Domain Model

`EvaluationSuite` contains a stable suite ID, display name, version, cases, concurrency limit,
default attempt limit, and release gates. A case continues to identify an immutable repository
commit, gold files, hidden commands, forbidden paths, and timeout.

`EvaluationRun` is immutable after completion except for its lifecycle timestamps and accumulated
case results. It records:

- run ID, tenant, suite identity and version
- status: queued, running, completed, failed, or cancelled
- candidate label and optional baseline run ID
- model, prompt, tool-schema, policy, and dataset versions
- environment fingerprint and deterministic seed
- creation, start, and completion timestamps
- aggregate metrics, baseline deltas, and release-gate decision

`EvaluationCaseResult` records attempt count, outcome, failure category, observation, report,
error summary, and timestamps. Failure categories are timeout, infrastructure, policy, execution,
invalid output, and none.

## Execution Semantics

The service executes cases under an `asyncio.Semaphore`. Each attempt is bounded by the case
timeout. Only infrastructure and timeout failures retry, up to the configured attempt limit.
Result ordering follows suite case ordering regardless of completion order.

The deterministic seed, suite version, immutable commit, provider/model name, prompt version,
tool-schema version, policy version, and a normalized platform fingerprint make a run
reproducible. Secrets and absolute user paths are excluded from provenance.

Replay creates a new run containing selected cases and records the source run ID. It never
mutates the original result.

## Comparison And Gates

Aggregate metrics include resolution rate, Recall@10, MRR, unauthorized-call rate, regression
rate, p50/p95 latency, total model calls, and total tokens.

Baseline comparison reports candidate minus baseline for each comparable metric. A release fails
when:

- resolution rate is below the configured absolute minimum
- resolution-rate regression exceeds the configured maximum
- unauthorized-tool-call rate exceeds its maximum
- regression rate exceeds its maximum
- privacy findings exceed their maximum
- any case ends in an infrastructure or invalid-output failure

Gate output contains individual checks and one final pass/fail value. Missing baseline data is
reported explicitly and does not silently become zero.

## Storage And API

The repository exposes create, get, list, and save operations scoped by `tenant_id`. SQL stores
the complete strict run document plus indexed identity, status, and timestamps. Optimistic
versions prevent lost updates.

Authenticated routes:

- `POST /v1/evaluations/runs`
- `GET /v1/evaluations/runs`
- `GET /v1/evaluations/runs/{run_id}`
- `POST /v1/evaluations/runs/{run_id}/replay`
- `GET /v1/evaluations/runs/{run_id}/report.json`
- `GET /v1/evaluations/runs/{run_id}/report.md`
- `GET /v1/tasks`

Unknown and cross-tenant run IDs return the same generic 404. Request models reject extra fields.
List endpoints are bounded and use stable newest-first ordering.

## Console

FastAPI serves `/console` and versioned static assets from the Python package. The console uses
plain HTML, CSS, and JavaScript and has no Node build step.

The first screen is the operational workspace:

- compact left navigation for Runs and Tasks
- run table with status, candidate, gate, resolution, safety, and duration
- selected-run evidence panel with provenance, gate checks, metric deltas, and case timeline
- replay action for failed cases
- task queue table for operational context

The console asks for an API token and keeps it only in JavaScript memory. It never writes the token
to local storage, session storage, cookies, URLs, or logs. Empty, loading, unauthorized, and
network-failure states provide direct recovery actions.

Visual direction is a restrained engineering console: white and cool-gray working surfaces,
graphite text, green/red status signals, and a cobalt interaction accent. IBM Plex Sans is used
when available with system fallbacks; JetBrains Mono is reserved for identifiers and metrics.
The signature element is a dense execution rail that aligns case outcomes with baseline deltas.
Cards are limited to individual run summaries; page regions remain unframed.

The layout supports 1440x900 desktop and 390x844 mobile without overlap. Keyboard focus,
semantic tables, reduced motion, and sufficient contrast are required.

## README

The README follows an official open-source structure:

1. concise product statement and CI/license/runtime badges
2. verified console screenshot
3. problem and architecture
4. engineering guarantees
5. quick start
6. evaluation harness workflow
7. operations console usage
8. CLI/API examples
9. security and deployment boundaries
10. repository layout and documentation index

Claims must be backed by tests, CI, or clearly labeled design intent. No benchmark success rate is
published without a checked-in reproducible dataset and result artifact.

## Testing And Release

TDD covers:

- suite/run model validation
- concurrency bounds, stable ordering, timeout/retry, and replay
- aggregation, comparison, gates, and report rendering
- tenant object authorization and bounded API lists
- SQL round trips and optimistic updates
- console routes, asset security, and API-token handling

Release verification includes pytest with at least 80% coverage, Ruff, mypy, YAML/JSON parsing,
wheel build, dependency audit, privacy scan, Compose validation, Playwright desktop/mobile
screenshots, and GitHub CI Docker builds.

## Privacy

Evaluation inputs and reports can contain repository content, so outputs pass through existing
redaction before persistence or export. Error summaries are bounded and must not contain process
environment values. The repository scanner remains a publication gate.
