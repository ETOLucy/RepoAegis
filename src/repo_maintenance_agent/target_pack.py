from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from repo_maintenance_agent.config import Settings

_TARGET_PACK_SCHEMA = "repoaegis-target-pack/v2"
_DEFAULT_TARGET_ID = "repoaegis-v2"
_SENSITIVE_PATTERNS = (
    "OPENAI_API_KEY",
    "REPO_AGENT_API_TOKENS",
    "experimental_bearer_token",
    "ghp_",
    "sk-",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN OPENSSH PRIVATE KEY",
)
_IGNORED_PARTS = frozenset({".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "target"})


@dataclass(frozen=True, slots=True)
class TargetPack:
    manifest: dict[str, object]
    digest: str


def build_target_pack(settings: Settings, *, repo_root: Path) -> TargetPack:
    """Build an immutable, content-addressed target pack describing a RepoAegis runtime."""
    root = Path(repo_root).resolve()
    if not (root / "pyproject.toml").is_file():
        raise ValueError("repo root must contain pyproject.toml")
    manifest: dict[str, object] = {
        "schema_version": _TARGET_PACK_SCHEMA,
        "target_id": _DEFAULT_TARGET_ID,
        "runtime": _collect_runtime(root),
        "images": dict(settings.sandbox_image_digests),
        "policy": _collect_policy(root),
    }
    _reject_sensitive(manifest)
    digest = _canonical_digest(manifest)
    manifest["digest"] = digest
    return TargetPack(manifest=manifest, digest=digest)


def _collect_runtime(root: Path) -> dict[str, object]:
    return {
        "commit_sha": _git_head(root),
        "source_digest": _tree_digest(
            root,
            include=(
                "src",
                "tests",
                "pyproject.toml",
                "docker-compose.yml",
                "Dockerfile",
                "sandbox",
            ),
        ),
        "pyproject_digest": _file_digest(root / "pyproject.toml"),
    }


def _collect_policy(root: Path) -> dict[str, object]:
    policy = root / "src" / "repo_maintenance_agent" / "policies" / "permissions.py"
    return {"permissions_digest": _file_digest(policy)}


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("target pack requires a git checkout")
    commit = result.stdout.strip()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ValueError("target pack requires a valid git commit")
    return commit


def _tree_digest(root: Path, *, include: tuple[str, ...]) -> str:
    hasher = hashlib.sha256()
    for relative in sorted(include):
        path = root / relative
        if path.is_dir():
            for file in sorted(path.rglob("*")):
                if not file.is_file() or _ignored(file):
                    continue
                _hash_file(hasher, relative, root, file)
        elif path.is_file():
            _hash_file(hasher, relative, root, path)
    return hasher.hexdigest()


def _hash_file(hasher: hashlib._Hash, scope: str, root: Path, file: Path) -> None:
    identity = f"{scope}:{file.relative_to(root).as_posix()}".encode("utf-8")
    hasher.update(identity + b"\0")
    hasher.update(file.read_bytes() + b"\0")


def _file_digest(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"target pack file missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ignored(path: Path) -> bool:
    return _IGNORED_PARTS.intersection(path.parts) != set()


def _canonical_digest(manifest: dict[str, object]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject_sensitive(manifest: dict[str, object]) -> None:
    serialized = json.dumps(manifest).lower()
    for pattern in _SENSITIVE_PATTERNS:
        if pattern.lower() in serialized:
            raise ValueError(f"target pack contains sensitive material: {pattern}")