from pathlib import Path

from pydantic import SecretStr

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.production_graph import ProductionGraphFactory
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.tools.gateway import InMemoryOperationLog
from repo_maintenance_agent.tools.github import LocalDraftRecordAdapter


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
        "git_commit",
        "git_push",
        "create_draft_pr",
    }
    assert isinstance(adapters["create_draft_pr"], LocalDraftRecordAdapter)
