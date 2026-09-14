"""Metering is the part that must be right: a wrong price hides a runaway run."""

from datetime import UTC, datetime
from typing import Any

import pytest

from repoaegis.agent.llm import (
    Budget,
    BudgetExceeded,
    DeepSeek,
    LLMTimeout,
    Prices,
    Usage,
    is_peak,
    price,
)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        ("2026-09-14T02:30:00+00:00", True),  # Monday, inside 01-04
        ("2026-09-14T04:00:00+00:00", False),  # boundary is exclusive
        ("2026-09-14T09:59:00+00:00", True),  # inside 06-10
        ("2026-09-14T12:00:00+00:00", False),  # afternoon is off-peak
        ("2026-09-12T02:30:00+00:00", False),  # Saturday: never peak
    ],
)
def test_peak_window(moment: str, expected: bool) -> None:
    assert is_peak(datetime.fromisoformat(moment)) is expected


def test_off_peak_is_half_price() -> None:
    usage = Usage(cache_hit_tokens=0, cache_miss_tokens=1_000_000, completion_tokens=1_000_000)
    peak = price(usage, Prices(), moment=datetime(2026, 9, 14, 2, 0, tzinfo=UTC))
    off = price(usage, Prices(), moment=datetime(2026, 9, 14, 20, 0, tzinfo=UTC))
    assert peak == pytest.approx(0.30 + 1.20)
    assert off == pytest.approx(peak / 2)


def test_cache_hits_cost_fifty_times_less() -> None:
    at_peak = datetime(2026, 9, 14, 2, 0, tzinfo=UTC)
    hit = price(Usage(cache_hit_tokens=1_000_000), Prices(), moment=at_peak)
    miss = price(Usage(cache_miss_tokens=1_000_000), Prices(), moment=at_peak)
    assert miss == pytest.approx(hit * 50)


def test_usage_adds_up() -> None:
    total = Usage(1, 2, 3, 0.5) + Usage(10, 20, 30, 0.25)
    assert (total.cache_hit_tokens, total.prompt_tokens, total.total_tokens) == (11, 33, 66)
    assert total.cost_usd == pytest.approx(0.75)


def test_budget_stops_before_the_next_call_not_after() -> None:
    budget = Budget(limit_usd=0.10)
    budget.check()
    budget.charge(Usage(completion_tokens=0, cost_usd=0.09))
    budget.check()  # still under
    budget.charge(Usage(completion_tokens=0, cost_usd=0.02))
    with pytest.raises(BudgetExceeded):
        budget.check()
    assert budget.remaining == 0.0


def test_zero_limit_means_unmetered() -> None:
    budget = Budget(limit_usd=0.0)
    budget.charge(Usage(cost_usd=99.0))
    budget.check()
    assert budget.remaining == float("inf")


class StubClient:
    """Shaped like the OpenAI SDK's client, minus the network."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []
        outer = self

        class Completions:
            async def create(self, **kwargs: Any) -> dict[str, Any]:
                outer.calls.append(kwargs)
                return outer.payload

        class Chat:
            completions = Completions()

        self.chat = Chat()


def reply(**message: Any) -> dict[str, Any]:
    return {
        "choices": [{"message": message, "finish_reason": message.pop("_finish", "stop")}],
        "usage": {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 800,
            "prompt_cache_miss_tokens": 200,
            "completion_tokens": 50,
        },
    }


async def test_complete_reads_text_and_meters_the_call() -> None:
    llm = DeepSeek(api_key="x", client=StubClient(reply(content="hello")), budget=Budget(1.0))
    out = await llm.complete([{"role": "user", "content": "hi"}])

    assert out.text == "hello"
    assert (out.usage.cache_hit_tokens, out.usage.cache_miss_tokens) == (800, 200)
    assert out.usage.cost_usd > 0
    assert llm.budget.spent == out.usage.cost_usd


async def test_complete_parses_tool_calls() -> None:
    payload = reply(
        content=None,
        tool_calls=[
            {
                "id": "call_1",
                "function": {"name": "grep", "arguments": '{"pattern": "resolve_redirects"}'},
            }
        ],
    )
    llm = DeepSeek(api_key="x", client=StubClient(payload))
    out = await llm.complete([{"role": "user", "content": "find it"}], tools=[{"type": "function"}])

    assert [(c.name, c.arguments) for c in out.tool_calls] == [
        ("grep", {"pattern": "resolve_redirects"})
    ]


async def test_malformed_arguments_do_not_crash_the_client() -> None:
    payload = reply(
        content=None,
        tool_calls=[{"id": "c", "function": {"name": "grep", "arguments": "{not json"}}],
    )
    llm = DeepSeek(api_key="x", client=StubClient(payload))
    out = await llm.complete([{"role": "user", "content": "x"}])
    assert out.tool_calls[0].arguments == {"__unparsed__": "{not json"}


async def test_a_gateway_without_cache_fields_still_prices() -> None:
    payload = reply(content="ok")
    del payload["usage"]["prompt_cache_hit_tokens"]
    del payload["usage"]["prompt_cache_miss_tokens"]
    llm = DeepSeek(api_key="x", client=StubClient(payload))
    out = await llm.complete([{"role": "user", "content": "x"}])
    assert (out.usage.cache_hit_tokens, out.usage.cache_miss_tokens) == (0, 1000)


async def test_an_exhausted_budget_blocks_the_request() -> None:
    stub = StubClient(reply(content="ok"))
    budget = Budget(limit_usd=0.000001)
    budget.charge(Usage(cost_usd=0.01))
    llm = DeepSeek(api_key="x", client=stub, budget=budget)
    with pytest.raises(BudgetExceeded):
        await llm.complete([{"role": "user", "content": "x"}])
    assert stub.calls == []  # nothing was sent


async def test_an_empty_reply_never_becomes_an_invalid_assistant_turn() -> None:
    """The bug this guards: a provider returned neither content nor tool calls,
    we replayed that message, and the next request was rejected with 400
    'content or tool_calls must be set' -- wedging the task for good."""
    payload = reply(content=None)
    llm = DeepSeek(api_key="x", client=StubClient(payload))
    out = await llm.complete([{"role": "user", "content": "x"}])

    assert out.tool_calls == ()
    assert out.raw_message["role"] == "assistant"
    assert out.raw_message["content"] == ""  # a string, never None
    assert "tool_calls" not in out.raw_message


async def test_a_hanging_provider_is_cut_off_by_our_own_clock() -> None:
    """The SDK's timeout was set to 120s and a call still hung for sixteen
    minutes, so the ceiling is enforced here rather than trusted to the library."""
    import asyncio

    class Hanging:
        class chat:
            class completions:
                @staticmethod
                async def create(**kwargs: Any) -> Any:
                    await asyncio.sleep(60)

    llm = DeepSeek(api_key="x", client=Hanging(), timeout_seconds=0.05, max_retries=0)
    with pytest.raises(LLMTimeout, match="no reply within"):
        await llm.complete([{"role": "user", "content": "x"}])
