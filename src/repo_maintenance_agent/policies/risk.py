from __future__ import annotations

from pathlib import PurePosixPath

from repo_maintenance_agent.domain.models import RiskLevel, ToolPermission

_DEPENDENCY_FILES = {
    "cargo.lock", "cargo.toml", "go.mod", "go.sum", "package-lock.json",
    "package.json", "pnpm-lock.yaml", "poetry.lock", "pyproject.toml",
    "requirements.txt", "uv.lock", "yarn.lock",
}
_CI_PREFIXES = (".github/workflows/", ".circleci/", ".gitlab/")
_CI_FILES = {".gitlab-ci.yml", "azure-pipelines.yml", "jenkinsfile"}
_SENSITIVE_NAMES = {".env", ".env.example", "secrets.yml", "secrets.yaml"}


def deterministic_risk(
    paths: tuple[str, ...], allowed_tools: tuple[ToolPermission, ...]
) -> tuple[RiskLevel, tuple[str, ...]]:
    reasons: set[str] = set()
    for raw_path in paths:
        path = raw_path.replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        lowered = path.lower()
        name = PurePosixPath(lowered).name
        parts = set(PurePosixPath(lowered).parts)
        if name in _DEPENDENCY_FILES or name.startswith("requirements"):
            reasons.add(f"dependency manifest: {raw_path}")
        if lowered.startswith(_CI_PREFIXES) or name in _CI_FILES:
            reasons.add(f"CI configuration: {raw_path}")
        if parts & {"auth", "authentication", "authorization", "security", "crypto"}:
            reasons.add(f"authentication or security path: {raw_path}")
        if parts & {"migration", "migrations", "alembic"}:
            reasons.add(f"database migration: {raw_path}")
        if name in _SENSITIVE_NAMES or "secret" in name or "credential" in name:
            reasons.add(f"sensitive configuration: {raw_path}")
    if {ToolPermission.GIT_WRITE, ToolPermission.GITHUB_WRITE} & set(allowed_tools):
        reasons.add("remote repository write")
    return (RiskLevel.HIGH if reasons else RiskLevel.LOW, tuple(sorted(reasons)))


def higher_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2, RiskLevel.CRITICAL: 3}
    return left if order[left] >= order[right] else right
