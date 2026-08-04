from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI

from repo_maintenance_agent.config import Settings
from repo_maintenance_agent.search.index import EmbeddingBatch


class OpenAIEmbeddingClient:
    def __init__(self, client: Any, *, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_settings(cls, settings: Settings) -> OpenAIEmbeddingClient:
        if settings.openai_api_key is None:
            raise ValueError("embedding client requires model credentials")
        return cls(
            AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value()),
            model=settings.openai_embedding_model,
        )

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        if not texts or any(not text for text in texts):
            raise ValueError("embedding input must contain non-empty text")
        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(texts))):
            raise ValueError("embedding response indices do not match input")
        return EmbeddingBatch(
            vectors=tuple(tuple(float(value) for value in item.embedding) for item in ordered),
            input_tokens=int(response.usage.total_tokens),
        )
