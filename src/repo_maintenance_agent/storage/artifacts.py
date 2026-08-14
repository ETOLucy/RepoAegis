from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repo_maintenance_agent.domain.errors import ResourceNotFound
from repo_maintenance_agent.storage.sql import ArtifactRow

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


class FileArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._metadata: dict[str, tuple[str, Path, str]] = {}

    async def put(
        self,
        tenant_id: str,
        task_id: str,
        name: str,
        content: bytes,
        media_type: str,
    ) -> str:
        artifact_id = str(uuid4())
        safe_name = _sanitize_name(name)
        tenant_key = hashlib.sha256(tenant_id.encode()).hexdigest()[:24]
        task_key = hashlib.sha256(task_id.encode()).hexdigest()[:24]
        directory = (self._root / tenant_key / task_key).resolve()
        if not directory.is_relative_to(self._root):
            raise ValueError("artifact directory escaped root")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{artifact_id}-{safe_name}"
        target.write_bytes(content)
        self._metadata[artifact_id] = (tenant_id, target, media_type)
        return artifact_id

    async def get(self, tenant_id: str, artifact_id: str) -> bytes:
        metadata = self._metadata.get(artifact_id)
        if metadata is None or metadata[0] != tenant_id:
            raise ResourceNotFound("artifact not found")
        try:
            return metadata[1].read_bytes()
        except OSError as error:
            raise ResourceNotFound("artifact not found") from error


def _sanitize_name(name: str) -> str:
    basename = Path(name.replace("\\", "/")).name
    cleaned = _SAFE_NAME.sub("_", basename).strip("._")
    return cleaned[:120] or "artifact.bin"


class SqlFileArtifactStore:
    def __init__(self, engine: Engine, root: Path) -> None:
        self._engine = engine
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    async def put(
        self,
        tenant_id: str,
        task_id: str,
        name: str,
        content: bytes,
        media_type: str,
    ) -> str:
        safe_name = _sanitize_name(name)
        content_sha = hashlib.sha256(content).hexdigest()
        artifact_id = hashlib.sha256(
            "\0".join(
                (tenant_id, task_id, safe_name, media_type, content_sha)
            ).encode()
        ).hexdigest()
        tenant_key = hashlib.sha256(tenant_id.encode()).hexdigest()[:24]
        task_key = hashlib.sha256(task_id.encode()).hexdigest()[:24]
        relative = Path(tenant_key) / task_key / artifact_id
        target = (self._root / relative).resolve()
        if not target.is_relative_to(self._root):
            raise ValueError("artifact path escaped root")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != content_sha:
                raise RuntimeError("content-addressed artifact collision")
        else:
            temporary = target.with_name(f".{uuid4().hex}.tmp")
            temporary.write_bytes(content)
            os.replace(temporary, target)
        try:
            with Session(self._engine) as session:
                session.add(
                    ArtifactRow(
                        artifact_id=artifact_id,
                        tenant_id=tenant_id,
                        task_id=task_id,
                        relative_path=relative.as_posix(),
                        media_type=media_type,
                        content_sha256=content_sha,
                    )
                )
                session.commit()
        except IntegrityError:
            pass
        return artifact_id

    async def get(self, tenant_id: str, artifact_id: str) -> bytes:
        with Session(self._engine) as session:
            row = session.get(ArtifactRow, artifact_id)
            if row is None or row.tenant_id != tenant_id:
                raise ResourceNotFound("artifact not found")
            relative_path = row.relative_path
            expected_sha = row.content_sha256
        target = (self._root / relative_path).resolve()
        if not target.is_relative_to(self._root):
            raise ResourceNotFound("artifact not found")
        try:
            content = target.read_bytes()
        except OSError as error:
            raise ResourceNotFound("artifact not found") from error
        if hashlib.sha256(content).hexdigest() != expected_sha:
            raise ResourceNotFound("artifact content integrity check failed")
        return content

