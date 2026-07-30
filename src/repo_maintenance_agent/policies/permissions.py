from __future__ import annotations

from pathlib import Path
from typing import Any

from repo_maintenance_agent.domain.errors import AuthorizationDenied
from repo_maintenance_agent.domain.models import (
    RepoTaskState,
    TaskStatus,
    ToolCall,
    ToolPermission,
)

_AGENT_PERMISSIONS: dict[str, frozenset[ToolPermission]] = {
    "intake": frozenset({ToolPermission.GITHUB_READ}),
    "research": frozenset({ToolPermission.GITHUB_READ, ToolPermission.REPO_READ}),
    "planning": frozenset({ToolPermission.REPO_READ}),
    "coding": frozenset(
        {
            ToolPermission.REPO_READ,
            ToolPermission.SANDBOX_WRITE,
            ToolPermission.SANDBOX_EXECUTE,
        }
    ),
    "verification": frozenset(
        {ToolPermission.REPO_READ, ToolPermission.SANDBOX_EXECUTE}
    ),
    "review": frozenset({ToolPermission.REPO_READ}),
    "pr": frozenset(
        {ToolPermission.REPO_READ, ToolPermission.GITHUB_READ, ToolPermission.GITHUB_WRITE}
    ),
    "control": frozenset({ToolPermission.CONTROL}),
}

_PATH_KEYS = frozenset({"path", "paths", "cwd", "file", "files", "target"})


class PermissionPolicy:
    def authorize(self, call: ToolCall, state: RepoTaskState, workspace_root: Path) -> None:
        if (
            call.task_id != state.task_id
            or call.tenant_id != state.tenant_id
            or call.repo_id != state.repo_id
            or call.commit_sha != state.commit_sha
        ):
            raise AuthorizationDenied("tool call scope mismatch")

        allowed = _AGENT_PERMISSIONS.get(call.agent, frozenset())
        if call.permission not in allowed:
            raise AuthorizationDenied(
                f"agent {call.agent!r} is not allowed permission {call.permission}"
            )

        if call.permission is ToolPermission.GITHUB_WRITE:
            approval = state.approval
            if (
                approval is None
                or not approval.approved
                or state.plan_hash is None
                or approval.plan_hash != state.plan_hash
            ):
                raise AuthorizationDenied("matching human approval is required")

        if (
            call.permission in {ToolPermission.SANDBOX_WRITE, ToolPermission.SANDBOX_EXECUTE}
            and state.status
            not in {
                TaskStatus.CODING,
                TaskStatus.VERIFYING,
                TaskStatus.REVIEWING,
            }
        ):
                raise AuthorizationDenied("task stage does not allow sandbox mutation")

        self._validate_paths(call.arguments, workspace_root.resolve())

    def _validate_paths(self, arguments: dict[str, Any], root: Path) -> None:
        for key, value in arguments.items():
            if key not in _PATH_KEYS:
                continue
            values = value if isinstance(value, list | tuple) else [value]
            for candidate in values:
                if not isinstance(candidate, str):
                    raise AuthorizationDenied("path values must be strings")
                path = Path(candidate)
                resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
                if not resolved.is_relative_to(root):
                    raise AuthorizationDenied("path resolves outside assigned workspace")
