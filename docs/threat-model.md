# Threat Model

## Scope and Assets

The system processes private source code and lets model-selected actions affect temporary workspaces and GitHub. Protected assets are:

- GitHub installation tokens, model credentials, and API bearer tokens
- private repository source, issues, comments, and commit history
- generated patches, verification logs, artifacts, and pull request drafts
- tenant identity, task state, approvals, operation logs, and search indexes
- worker, model, search, database, and sandbox capacity

## Attacker Capabilities

The model assumes an attacker can:

- author issue text, repository files, test output, and documentation containing prompt injection
- guess task IDs and send malformed or oversized API input
- create paths, symlinks, patches, branches, and Git metadata with adversarial names
- trigger retries, worker crashes, timeouts, and duplicate delivery
- observe public pull requests and any data intentionally returned by the API

The attacker does not initially control the host, database administrator, secret manager, or Docker daemon.

## Trust Boundaries

1. User or GitHub input enters the authenticated Task API.
2. Untrusted issue and repository content enters model context.
3. Model-selected actions cross strict schemas and the Tool Gateway.
4. Patch mutation crosses `GitPatchApplier`.
5. Repository commands cross into the Docker Sandbox.
6. External reads cross GitHub, OpenSearch, OpenAI, and Context7 adapters.
7. Remote writes cross an immutable plan approval boundary.
8. Worker ownership crosses a rotating queue-lease fencing boundary.

## Abuse Paths and Controls

| Abuse path | Primary controls | Verification |
|---|---|---|
| Prompt injection requests secrets or privileges | content treated as data; fixed system policy; deny-by-default tools | agent and gateway tests |
| Cross-tenant task/index access | authenticated tenant injection; object authorization; tenant/repo/commit filters | API, SQL, and search tests |
| Path traversal or undeclared patch files | resolved containment; safe relative paths; numstat allowlist | local search and patch tests |
| Command injection | argument arrays; no shell; executable/subcommand allowlists | process and adapter tests |
| Secret leakage through logs or child processes | environment allowlist; secret broker; `SecretStr`; recursive redaction | tracing and privacy tests |
| Malicious repository build | non-root container; read-only root; controlled setup egress; offline checks; resource limits; no Docker socket | sandbox command tests |
| Duplicate or stale worker writes | optimistic versions; rotating lease IDs; idempotency keys | queue, worker, and SQL tests |
| Approval replay after plan change | approval contains exact SHA-256 plan hash | API and policy tests |
| Incorrect patch presented as fixed | apply preflight; sandbox tests/lints; bounded review loop | patch, verifier, and graph tests |
| Dependency substitution | immutable image and Action pins; dependency audit in CI | Compose validation and CI |

## Operational Boundary

The Docker daemon is privileged infrastructure and belongs on dedicated workers. Production deployment uses TLS at the edge, short-lived GitHub App credentials, encrypted PostgreSQL and artifact storage, network policy, centralized audit export, request-size/rate controls, and a managed secret broker. The checked-in Compose file is loopback-only local infrastructure; its OpenSearch security setting is not a production profile.
