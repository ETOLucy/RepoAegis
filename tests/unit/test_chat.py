from __future__ import annotations

from pathlib import Path

import pytest

from repo_maintenance_agent.chat import ChatAnswer, ChatEngine
from repo_maintenance_agent.config import Settings


class FakeModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def structured(self, *, system, input_text, schema):
        self.calls.append({"system": system, "input_text": input_text, "schema": schema})
        return ChatAnswer(answer="The function is in src/app.py lines 1-3.")


def _make_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text(
        "def load_config():\n    return {'env': 'demo'}\n\n\ndef run():\n    pass\n",
        encoding="utf-8",
    )
    (src / "service.py").write_text(
        "class RepoService:\n    def search(self, query):\n        return []\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_chat_engine_returns_answer_with_retrieved_hits(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    model = FakeModel()
    engine = ChatEngine(
        Settings(),
        repo_root=repo,
        commit_sha="a" * 40,
        model=model,
    )

    result = await engine.answer("how does load_config work", top_k=3)

    assert isinstance(result["answer"], str) and result["answer"]
    assert len(result["hits"]) >= 1
    assert result["hits"][0]["path"] == "src/app.py"
    assert model.calls, "model should have been called"


@pytest.mark.asyncio
async def test_chat_engine_passes_snippets_to_model(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    model = FakeModel()
    engine = ChatEngine(
        Settings(),
        repo_root=repo,
        commit_sha="a" * 40,
        model=model,
    )

    await engine.answer("search service", top_k=2)

    assert model.calls
    import json

    payload = json.loads(model.calls[0]["input_text"])
    assert payload["question"] == "search service"
    assert any("service.py" in snippet["path"] for snippet in payload["retrieved_snippets"])