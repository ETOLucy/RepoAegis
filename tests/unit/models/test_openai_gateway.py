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

