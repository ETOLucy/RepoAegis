# Security Best-Practices Report

## Executive Summary

The Python/FastAPI control plane was reviewed against tenant isolation, authentication, input/output validation, command execution, sandboxing, secret handling, queue consistency, and supply-chain controls. No known critical or high-severity findings remain in the reviewed implementation. Automated tests, static analysis, dependency audit, privacy scanning, and immutable deployment pins are release gates.

## Verified Controls

### SEC-001: Tenant object authorization

- Severity: verified control
- Location: `src/repo_maintenance_agent/api/app.py:28`, `src/repo_maintenance_agent/storage/sql.py:97`
- Evidence: the authenticated principal supplies `tenant_id`; task reads and writes include tenant and task identity.
- Result: a guessed cross-tenant task ID returns the same generic 404 as an unknown resource.

### SEC-002: Production API baseline

- Severity: verified control
- Location: `src/repo_maintenance_agent/api/app.py:44`, `src/repo_maintenance_agent/api/app.py:49`
- Evidence: debug is disabled, production docs/OpenAPI are disabled, trusted hosts are enforced, explicit response models shape task output, and write models reject extra fields.
- Result: internal state and tenant identifiers are not returned by task endpoints.

### SEC-003: Credential handling

- Severity: verified control
- Location: `src/repo_maintenance_agent/config.py:18`, `src/repo_maintenance_agent/models/openai_gateway.py:37`
- Evidence: credentials use `SecretStr`, are loaded only from runtime environment, and OpenAI Responses set `store=False`.
- Result: CLI/config output reports credential availability without returning credential values.

### SEC-004: Command and path injection prevention

- Severity: verified control
- Location: `src/repo_maintenance_agent/tools/process.py:61`, `src/repo_maintenance_agent/tools/process.py:77`, `src/repo_maintenance_agent/policies/permissions.py:87`
- Evidence: processes use argument arrays with an executable allowlist and sanitized environment; all path-like tool arguments resolve under the assigned workspace.
- Result: shell interpolation and workspace escape are rejected before execution.

### SEC-005: Patch containment

- Severity: verified control
- Location: `src/repo_maintenance_agent/tools/patch.py:29`, `src/repo_maintenance_agent/tools/patch.py:42`
- Evidence: `git apply --numstat` is parsed before mutation, undeclared paths are rejected, and `git apply --check --whitespace=error-all` precedes application.
- Result: model output cannot silently modify a file outside its declared change set.

### SEC-006: Sandbox isolation

- Severity: verified control
- Location: `src/repo_maintenance_agent/sandbox/docker.py:25`
- Evidence: digest-only images, controlled setup egress, offline test/lint phases, read-only root, UID 10001, dropped capabilities, `no-new-privileges`, optional explicit seccomp, and CPU/memory/PID/tmpfs limits.
- Result: repository tests do not inherit control-plane credentials or host privileges.

### SEC-007: Remote-write approval

- Severity: verified control
- Location: `src/repo_maintenance_agent/policies/permissions.py:54`
- Evidence: GitHub write permission requires a positive decision whose plan hash exactly matches current state.
- Result: stale approvals and model-generated authorization claims cannot grant remote-write access.

### SEC-008: Queue fencing and concurrency

- Severity: verified control
- Location: `src/repo_maintenance_agent/storage/sql.py:238`, `src/repo_maintenance_agent/storage/sql.py:265`
- Evidence: claims use row locks with skip-locked semantics; claim and heartbeat rotate `lease_id`; ack/nack compare the current lease; task updates use optimistic versions.
- Result: an expired worker cannot acknowledge or overwrite work owned by a replacement worker.

### SEC-009: Privacy publication gate

- Severity: verified control
- Location: `src/repo_maintenance_agent/security/scanner.py:19`, `src/repo_maintenance_agent/security/scanner.py:58`, `src/repo_maintenance_agent/security/scanner.py:76`
- Evidence: the scanner covers tracked and untracked non-ignored files and detects OpenAI/GitHub credential shapes, private keys, personal Windows paths, and private local proxy data.
- Result: findings are redacted and cause a non-zero CI/pre-push result.

## Production Deployment Requirements

The application expects TLS termination, request-size and rate limits, encrypted storage, centralized audit export, short-lived GitHub App installation tokens, and a managed secret broker at the platform edge. The local Compose profile binds externally visible services to loopback and disables OpenSearch security only for local development.

## Verification Commands

```text
pytest --cov --cov-report=term-missing
ruff check src tests
mypy src
python -m repo_maintenance_agent.security.scanner
pip-audit
docker compose config --quiet
```
