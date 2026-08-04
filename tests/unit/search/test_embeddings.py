from types import SimpleNamespace

import pytest

from repo_maintenance_agent.search.embeddings import OpenAIEmbeddingClient


class RecordingEmbeddings:
    def __init__(self) -> None:
        self.kwargs = {}

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            ],
            usage=SimpleNamespace(total_tokens=7),
        )


@pytest.mark.asyncio
async def test_openai_embedding_client_batches_and_restores_index_order() -> None:
    embeddings = RecordingEmbeddings()
    client = SimpleNamespace(embeddings=embeddings)
    adapter = OpenAIEmbeddingClient(client, model="text-embedding-test")

    result = await adapter.embed(["first", "second"])

    assert embeddings.kwargs == {
        "model": "text-embedding-test",
        "input": ["first", "second"],
        "encoding_format": "float",
    }
    assert result.vectors == ((1.0, 0.0), (0.0, 1.0))
    assert result.input_tokens == 7
