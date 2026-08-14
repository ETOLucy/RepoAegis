import pytest

from repo_maintenance_agent.tools.context7 import Context7Adapter


class FakeMcpInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def call(self, tool: str, arguments: dict[str, str]) -> dict[str, object]:
        self.calls.append((tool, arguments))
        if tool == "resolve-library-id":
            return {"library_id": "/org/library"}
        return {"content": "official documentation result"}


@pytest.mark.asyncio
async def test_context7_resolves_library_before_querying_docs() -> None:
    invoker = FakeMcpInvoker()
    adapter = Context7Adapter(invoker)

    result = await adapter.query("FastAPI", "How is router-level authentication configured?")

    assert result.library_id == "/org/library"
    assert [call[0] for call in invoker.calls] == ["resolve-library-id", "query-docs"]
    assert invoker.calls[1][1]["query"] == "How is router-level authentication configured?"
