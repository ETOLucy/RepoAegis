from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.production_graph import ProductionGraphFactory
from repo_maintenance_agent.search.adapters.local import LocalLexicalSearch
from repo_maintenance_agent.storage.artifacts import FileArtifactStore
from repo_maintenance_agent.tools.gateway import InMemoryOperationLog
from repo_maintenance_agent.tools.github import LocalDraftRecordAdapter


def test_build_index_raises_when_no_embedding_keys(tmp_path: Path) -> None:
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text('{"defaultAction":"SCMP_ACT_ERRNO"}', encoding="utf-8")
    factory = ProductionGraphFactory(
        settings=Settings(
            environment="test",
            openai_api_key=None,
            openai_embedding_api_key=None,
            database_url=SecretStr("sqlite+pysqlite:///:memory:"),
            artifact_root=str(tmp_path / "artifacts"),
            sandbox_seccomp_profile=seccomp,
        ),
        artifacts=FileArtifactStore(tmp_path / "artifacts"),
        operations=InMemoryOperationLog(),
    )
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY or OPENAI_EMBEDDING_API_KEY"):
        factory._build_index(tmp_path)


@patch("repo_maintenance_agent.production_graph.OpenAIModelGateway")
@patch("repo_maintenance_agent.production_graph.default_lexical_search")
def test_production_graph_registers_complete_local_delivery_toolset(
    mock_lexical,
    mock_gateway_class,
    tmp_path: Path,
) -> None:
    mock_gateway_class.from_settings.return_value = AsyncMock()
    mock_lexical.return_value = LocalLexicalSearch(tmp_path)
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text('{"defaultAction":"SCMP_ACT_ERRNO"}', encoding="utf-8")
    factory = ProductionGraphFactory(
        settings=Settings(
            environment="test",
            OPENAI_API_KEY="sk-fake-test-key",
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
