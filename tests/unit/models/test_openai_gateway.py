from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from repo_maintenance_agent.models.openai_gateway import OpenAIModelGateway


class Answer(BaseModel):
    summary: str


class FakeResponses:
    def __init__(self) -> None:
        self.arguments: dict[str, object] = {}

    async def parse(self, **kwargs):
        self.arguments = kwargs
        return SimpleNamespace(output_parsed=Answer(summary="structured"))


class FakeClient:
    def __init__(self) -> None:
        self.responses = FakeResponses()


@pytest.mark.asyncio
async def test_gateway_uses_responses_structured_output_without_storing_prompt() -> None:
    client = FakeClient()
    gateway = OpenAIModelGateway(client=client, model="gpt-test-model")

    result = await gateway.structured(
        system="You are a planner.",
        input_text="Plan the fix.",
        schema=Answer,
    )

    assert result == Answer(summary="structured")
    assert client.responses.arguments["model"] == "gpt-test-model"
    assert client.responses.arguments["store"] is False
    assert client.responses.arguments["text_format"] is Answer


@pytest.mark.asyncio
async def test_gateway_rejects_empty_parsed_response() -> None:
    client = FakeClient()

    async def empty_parse(**kwargs):
        return SimpleNamespace(output_parsed=None)

    client.responses.parse = empty_parse
    gateway = OpenAIModelGateway(client=client, model="gpt-test-model")

    with pytest.raises(RuntimeError, match="structured"):
        await gateway.structured(system="system", input_text="input", schema=Answer)



def test_from_settings_passes_base_url_and_model(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "repo_maintenance_agent.models.openai_gateway.AsyncOpenAI",
        FakeAsyncOpenAI,
    )
    from repo_maintenance_agent.config import Settings

    gateway = OpenAIModelGateway.from_settings(
        Settings(
            OPENAI_API_KEY="test-key",
            OPENAI_BASE_URL="https://api.deepseek.com",
            OPENAI_MODEL="deepseek-chat",
        )
    )
    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://api.deepseek.com"
    assert gateway._model == "deepseek-chat"

@pytest.mark.asyncio
async def test_gateway_retries_once_on_invalid_structured_output() -> None:
    from pydantic import ValidationError

    class FlakyResponses:
        def __init__(self) -> None:
            self.calls = 0

        async def parse(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ValidationError.from_exception_data(
                    "Answer", [{"type": "missing", "loc": ("summary",), "input": {}}]
                )
            return SimpleNamespace(output_parsed=Answer(summary="retried"))

    class FlakyClient:
        def __init__(self) -> None:
            self.responses = FlakyResponses()

    gateway = OpenAIModelGateway(client=FlakyClient(), model="gpt-test-model")

    result = await gateway.structured(
        system="system", input_text="input", schema=Answer
    )

    assert result == Answer(summary="retried")
    assert gateway._client.responses.calls == 2