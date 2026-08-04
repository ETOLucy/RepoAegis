import pytest
from pydantic import SecretStr, ValidationError

from repo_maintenance_agent.config import Settings


def test_settings_reads_openai_key_without_exposing_it(
    monkeypatch,
) -> None:
    fake_key = "sk-" + "not-a-real-key-value"
    monkeypatch.setenv("OPENAI_API_KEY", fake_key)

    settings = Settings()

    assert isinstance(settings.openai_api_key, SecretStr)
    assert settings.openai_api_key.get_secret_value() == fake_key
    assert fake_key not in repr(settings)


def test_settings_does_not_load_dotenv_implicitly(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=must-not-load\n", encoding="utf-8")

    settings = Settings()

    assert settings.openai_api_key is None


def test_settings_accepts_standard_openai_model_variable(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test-model")

    settings = Settings()

    assert settings.openai_model == "gpt-test-model"


def test_settings_rejects_partial_sandbox_runner_configuration() -> None:
    with pytest.raises(ValidationError, match="sandbox runner URL and token"):
        Settings(sandbox_runner_url="http://sandbox-runner:8080")
