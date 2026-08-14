from pathlib import Path

import pytest

from repo_maintenance_agent.storage.artifacts import SqlFileArtifactStore
from repo_maintenance_agent.storage.sql import Base


@pytest.mark.asyncio
async def test_sql_file_artifact_metadata_survives_store_reconstruction(
    tmp_path: Path,
) -> None:
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'artifacts.db').as_posix()}")
    Base.metadata.create_all(engine)
    root = tmp_path / "artifacts"
    first = SqlFileArtifactStore(engine, root)

    artifact_id = await first.put(
        "tenant-a",
        "task-1",
        "proposed.patch",
        b"patch-bytes",
        "text/x-diff",
    )
    reconstructed = SqlFileArtifactStore(engine, root)

    assert await reconstructed.get("tenant-a", artifact_id) == b"patch-bytes"
