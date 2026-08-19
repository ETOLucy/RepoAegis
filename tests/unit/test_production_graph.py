from pathlib import Path

from pydantic import SecretStr
import pytest

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.production_graph import ProductionGraphFactory
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.tools.gateway import InMemoryOperationLog
from repo_maintenance_agent.tools.github import LocalDraftRecordAdapter

def test_build_index_raises_when_no_embedding_keys(tmp_path: Path) -> None:
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text('{"defaultAction":"SCMP_ACT_ERRNO"}', encoding="utf-8")
    factory = ProductionGraphFactory(
        settings=Settings(
            environment="test",
            openai_api_key=None,            # 显式覆盖环境变量
            openai_embedding_api_key=None,  # 显式覆盖环境变量
            database_url=SecretStr("sqlite+pysqlite:///:memory:"),
            artifact_root=str(tmp_path / "artifacts"),
            sandbox_seccomp_profile=seccomp,
        ),
        artifacts=FileArtifactStore(tmp_path / "artifacts"),
        operations=InMemoryOperationLog(),
    )
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY or OPENAI_EMBEDDING_API_KEY"):
        factory._build_index(tmp_path)

def test_production_graph_registers_complete_local_delivery_toolset(
    tmp_path: Path,
) -> None:
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text('{"defaultAction":"SCMP_ACT_ERRNO"}', encoding="utf-8")
    factory = ProductionGraphFactory(
        settings=Settings(
            environment="test",
            database_url=SecretStr("sqlite+pysqlite:///:memory:"),
            artifact_root=str(tmp_path / "artifacts"),
            sandbox_seccomp_profile=seccomp,
        ),
        artifacts=FileArtifactStore(tmp_path / "artifacts"),
        operations=InMemoryOperationLog(),
    )

    adapters = factory.build_adapters(tmp_path)

    assert set(adapters) == {
        "search_code",
        "apply_patch",
        "run_verification",
        "git_diff",
        "read_files",
        "git_commit",
        "git_push",
        "create_draft_pr",
    }
    assert isinstance(adapters["create_draft_pr"], LocalDraftRecordAdapter)
