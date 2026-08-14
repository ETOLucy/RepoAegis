import importlib
import sys
from pathlib import Path

import pytest


def test_importing_application_entrypoint_has_no_runtime_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REPO_AGENT_API_TOKENS", raising=False)
    sys.modules.pop("repo_maintenance_agent.main", None)

    module = importlib.import_module("repo_maintenance_agent.main")

    assert callable(module.build_application)
    assert not hasattr(module, "app")


def test_token_configuration_rejects_empty_and_non_string_mappings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPO_AGENT_API_TOKENS", "{}")
    sys.modules.pop("repo_maintenance_agent.main", None)
    module = importlib.import_module("repo_maintenance_agent.main")

    with pytest.raises(RuntimeError, match="at least one token"):
        module.parse_api_tokens()

    monkeypatch.setenv("REPO_AGENT_API_TOKENS", '{"token": 7}')
    with pytest.raises(RuntimeError, match="tenant strings or identity objects"):
        module.parse_api_tokens()


def test_application_factory_creates_sqlite_parent_without_openai_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "nested" / "repo-agent.db"
    monkeypatch.setenv("REPO_AGENT_API_TOKENS", '{"api-token":"tenant-a"}')
    monkeypatch.setenv("REPO_AGENT_DATABASE_URL", f"sqlite+pysqlite:///{database.as_posix()}")
    monkeypatch.setenv("REPO_AGENT_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("REPO_AGENT_ENVIRONMENT", "production")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    sys.modules.pop("repo_maintenance_agent.main", None)
    module = importlib.import_module("repo_maintenance_agent.main")

    app = module.build_application()

    assert database.is_file()
    assert app.docs_url is None
