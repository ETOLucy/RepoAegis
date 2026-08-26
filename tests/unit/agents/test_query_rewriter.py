from __future__ import annotations

import pytest

from repo_maintenance_agent.agents.query_rewriter import (
    rewrite_queries_with_model,
)
from repo_maintenance_agent.search.rewriter import (
    QueryRewritePlan,
    RewrittenQuery,
)


class FakeRewriterModel:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def structured(self, *, system, input_text, schema, max_attempts: int = 3):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model unavailable")
        return QueryRewritePlan(
            queries=[
                RewrittenQuery(text="load_config default", kind="general"),
                RewrittenQuery(
                    text="src/config.py", kind="path", key_paths=("src/config.py",)
                ),
            ],
            raw="{}",
        )


@pytest.mark.asyncio
async def test_rewrite_with_model_falls_back_to_rules_on_failure() -> None:
    model = FakeRewriterModel(fail=True)
    plan = await rewrite_queries_with_model(
        model,
        'Crash on "NoSuchKey" in `src/config.py`',
    )
    assert model.calls == 1
    texts = [q.text for q in plan.queries]
    assert "NoSuchKey" in texts
    assert any(q.kind == "path" for q in plan.queries)
