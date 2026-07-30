from __future__ import annotations

from typing import Any, TypeVar, cast

from openai import AsyncOpenAI
from pydantic import BaseModel

from repo_maintenance_agent.config import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class OpenAIModelGateway:
    def __init__(self, *, client: Any, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIModelGateway:
        if settings.openai_api_key is None:
            raise RuntimeError("OPENAI_API_KEY is required for live model execution")
        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        return cls(client=client, model=settings.openai_model)

    async def structured(
        self,
        *,
        system: str,
        input_text: str,
        schema: type[SchemaT],
    ) -> SchemaT:
        response = await self._client.responses.parse(
            model=self._model,
            instructions=system,
            input=input_text,
            text_format=schema,
            store=False,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("model did not return the requested structured output")
        return cast(SchemaT, parsed)
