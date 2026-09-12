import shutil
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


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep binary not installed")
def test_build_index_works_without_any_openai_credentials(tmp_path: Path) -> None:
    """The dependency-aware index (BM25 + Symbol + Graph + lexical/history) needs
    no embedding/OpenSearch credentials — unlike the old vector/hybrid channel."""
    seccomp = tmp_path / "seccomp.json"
    seccomp.write_text('{"defaultAction":"SCMP_ACT_ERRNO"}', encoding="utf-8")
    factory = ProductionGraphFactory(
        settings=Settings(
            environment="test",
            openai_api_key=None,
            database_url=SecretStr("sqlite+pysqlite:///:memory:"),
            artifact_root=str(tmp_path / "artifacts"),
            sandbox_seccomp_profile=seccomp,
        ),
        artifacts=FileArtifactStore(tmp_path / "artifacts"),
        operations=InMemoryOperationLog(),
    )
    index = factory._build_index(tmp_path)
    assert index is not None


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
        "goto_definition",
        "find_references",
        "apply_patch",
        "run_verification",
        "run_repro",
        "git_diff",
        "git_blame",
        "read_files",
        "git_commit",
        "git_push",
        "create_draft_pr",
    }
    assert isinstance(adapters["create_draft_pr"], LocalDraftRecordAdapter)
