"""Model access, metered.

One protocol (``LLM``) with two implementations: DeepSeek over its
OpenAI-compatible endpoint, and whatever a test wants to inject. Everything
above this module depends on the protocol, so no test needs the network and no
provider detail leaks into the agent loop.

Every call returns its ``Usage``, priced at DeepSeek's published per-million
rates including the off-peak discount and the cache-hit tier. Cost is computed
here rather than estimated later because the agent loop's budget has to stop a
run *while* it is running.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

Message = dict[str, Any]
ToolSpec = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Prices:
    """USD per million tokens, at peak. Off-peak is half (DeepSeek, 2026-09)."""

    cache_hit_in: float = 0.006
    cache_miss_in: float = 0.30
    out: float = 1.20

    def at(self, moment: datetime) -> Prices:
        if is_peak(moment):
            return self
        return Prices(self.cache_hit_in / 2, self.cache_miss_in / 2, self.out / 2)


def is_peak(moment: datetime) -> bool:
    """Peak is 01:00-04:00 and 06:00-10:00 UTC, Monday through Friday."""
    utc = moment.astimezone(UTC)
    if utc.weekday() >= 5:
        return False
    hour = utc.hour + utc.minute / 60
    return 1 <= hour < 4 or 6 <= hour < 10


@dataclass(frozen=True, slots=True)
class Usage:
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def prompt_tokens(self) -> int:
        return self.cache_hit_tokens + self.cache_miss_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.cache_hit_tokens + other.cache_hit_tokens,
            self.cache_miss_tokens + other.cache_miss_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cost_usd + other.cost_usd,
        )


def price(usage: Usage, prices: Prices, *, moment: datetime | None = None) -> float:
    rate = prices.at(moment or datetime.now(UTC))
    return (
        usage.cache_hit_tokens * rate.cache_hit_in
        + usage.cache_miss_tokens * rate.cache_miss_in
        + usage.completion_tokens * rate.out
    ) / 1_000_000


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Completion:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    raw_message: Message = field(default_factory=dict)


class LLM(Protocol):
    async def complete(
        self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None
    ) -> Completion: ...


class LLMTimeout(RuntimeError):
    """The provider did not answer inside the ceiling we enforce ourselves."""


class BudgetExceeded(RuntimeError):
    """The run spent its allowance. Raised before a call, never after."""

    def __init__(self, spent: float, limit: float) -> None:
        super().__init__(f"budget exhausted: ${spent:.4f} of ${limit:.4f}")
        self.spent = spent
        self.limit = limit


class Budget:
    """A spend meter for one task.

    Checked before each call rather than after, so an expensive run stops
    instead of overshooting by one more request. A limit of 0 means unmetered,
    which is only ever right in tests.
    """

    def __init__(self, limit_usd: float) -> None:
        self.limit = limit_usd
        self.used = Usage()

    @property
    def spent(self) -> float:
        return self.used.cost_usd

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.spent) if self.limit else float("inf")

    def check(self) -> None:
        if self.limit and self.spent >= self.limit:
            raise BudgetExceeded(self.spent, self.limit)

    def charge(self, usage: Usage) -> None:
        self.used = self.used + usage


class DeepSeek:
    """DeepSeek through its OpenAI-compatible endpoint.

    The SDK retries 429 and 5xx with backoff, and takes a per-request timeout --
    but a call has been observed hanging for sixteen minutes with that timeout
    set to two, so the ceiling here is enforced with our own ``wait_for`` as
    well. Trusting a library's timeout is trusting that every path inside it
    honours it; a wall clock we hold ourselves does not have that problem.

    ``client`` is injectable so tests can hand in a stub.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-flash",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        prices: Prices | None = None,
        budget: Budget | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
        self._client = client
        self._model = model
        # Room for the SDK's own attempts, and not a second more.
        self._ceiling = timeout_seconds * (max_retries + 1) + 10.0
        self._prices = prices or Prices()
        self.budget = budget or Budget(0.0)

    async def complete(
        self, messages: Sequence[Message], *, tools: Sequence[ToolSpec] | None = None
    ) -> Completion:
        self.budget.check()
        kwargs: dict[str, Any] = {"model": self._model, "messages": list(messages)}
        if tools:
            kwargs["tools"] = list(tools)
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(**kwargs), self._ceiling
            )
        except TimeoutError:
            raise LLMTimeout(
                f"no reply within {self._ceiling:.0f}s (model {self._model})"
            ) from None
        completion = _parse(response, self._prices)
        self.budget.charge(completion.usage)
        log.info(
            "llm.call",
            model=self._model,
            finish_reason=completion.finish_reason,
            tool_calls=len(completion.tool_calls),
            prompt_tokens=completion.usage.prompt_tokens,
            cache_hit_tokens=completion.usage.cache_hit_tokens,
            completion_tokens=completion.usage.completion_tokens,
            cost_usd=round(completion.usage.cost_usd, 6),
            spent_usd=round(self.budget.spent, 6),
        )
        return completion


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _parse(response: Any, prices: Prices) -> Completion:
    """Read the provider's reply defensively: fields differ across gateways."""
    import json

    choice = (_field(response, "choices") or [None])[0]
    message = _field(choice, "message")
    raw_calls = _field(message, "tool_calls") or []
    calls: list[ToolCall] = []
    for call in raw_calls:
        function = _field(call, "function")
        raw_args = _field(function, "arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            # A malformed argument object is the loop's problem, not a crash here.
            arguments = {"__unparsed__": raw_args}
        calls.append(
            ToolCall(
                id=str(_field(call, "id", "")),
                name=str(_field(function, "name", "")),
                arguments=arguments,
            )
        )

    raw_usage = _field(response, "usage")
    prompt_tokens = int(_field(raw_usage, "prompt_tokens", 0) or 0)
    # DeepSeek reports the cache split; other gateways may not.
    hit = _field(raw_usage, "prompt_cache_hit_tokens")
    miss = _field(raw_usage, "prompt_cache_miss_tokens")
    cache_hit = int(hit or 0)
    cache_miss = int(miss) if miss is not None else max(0, prompt_tokens - cache_hit)
    usage = Usage(
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=cache_miss,
        completion_tokens=int(_field(raw_usage, "completion_tokens", 0) or 0),
    )
    usage = Usage(
        usage.cache_hit_tokens,
        usage.cache_miss_tokens,
        usage.completion_tokens,
        price(usage, prices),
    )
    text = str(_field(message, "content") or "")
    # The wire format demands an assistant turn carry content or tool calls; a
    # provider that returns neither would otherwise poison the next request with
    # a message the API rejects. Normalising content to a string keeps the
    # transcript replayable whatever came back.
    raw: Message = {"role": "assistant", "content": text}
    if raw_calls:
        raw["tool_calls"] = raw_calls
    return Completion(
        text=text,
        tool_calls=tuple(calls),
        finish_reason=str(_field(choice, "finish_reason", "stop") or "stop"),
        usage=usage,
        raw_message=raw,
    )
