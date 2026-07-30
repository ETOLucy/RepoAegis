from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import uuid4

from repo_maintenance_agent.domain.errors import ResourceNotFound

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

