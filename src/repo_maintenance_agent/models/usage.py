from __future__ import annotations

from decimal import Decimal
from threading import Lock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from repo_maintenance_agent.evaluation.models import ModelUsage

_MILLION = Decimal(1_000_000)


class ModelBudgetExceeded(RuntimeError):
    pass


class UsageRates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_hit_input_cny_per_million: Decimal = Field(ge=0)
    cache_miss_input_cny_per_million: Decimal = Field(ge=0)
    output_cny_per_million: Decimal = Field(ge=0)

    def price(self, usage: ModelUsage) -> Decimal:
        return (
            Decimal(usage.input_cache_hit_tokens)
            * self.cache_hit_input_cny_per_million
            + Decimal(usage.input_cache_miss_tokens)
            * self.cache_miss_input_cny_per_million
            + Decimal(usage.output_tokens) * self.output_cny_per_million
        ) / _MILLION


class UsageLedger:
    def __init__(self, *, limit_cny: Decimal, rates: UsageRates) -> None:
        if limit_cny <= 0:
            raise ValueError("model budget limit must be positive")
        self._limit_cny = limit_cny
        self._rates = rates
        self._spent_cny = Decimal("0")
        self._reserved_cny = Decimal("0")
        self._hit_tokens = 0
        self._miss_tokens = 0
        self._output_tokens = 0
        self._reasoning_tokens = 0
        self._lock = Lock()

    def reserve(self, maximum_cost_cny: Decimal) -> Decimal:
        if maximum_cost_cny <= 0:
            raise ValueError("maximum call cost must be positive")
        with self._lock:
            projected = self._spent_cny + self._reserved_cny + maximum_cost_cny
            if projected > self._limit_cny:
                raise ModelBudgetExceeded("model spend would exceed the hard limit")
            self._reserved_cny += maximum_cost_cny
        return maximum_cost_cny

    def record(self, usage: ModelUsage, *, reservation_cny: Decimal) -> Decimal:
        charged = self._rates.price(usage)
        with self._lock:
            if reservation_cny <= 0 or reservation_cny > self._reserved_cny:
                raise ValueError("usage reservation is invalid")
            self._reserved_cny -= reservation_cny
            self._spent_cny += charged
            self._hit_tokens += usage.input_cache_hit_tokens
            self._miss_tokens += usage.input_cache_miss_tokens
            self._output_tokens += usage.output_tokens
            self._reasoning_tokens += usage.reasoning_tokens
            if self._spent_cny + self._reserved_cny > self._limit_cny:
                raise ModelBudgetExceeded("provider usage exceeded the hard limit")
        return charged

    @property
    def spent_cny(self) -> Decimal:
        with self._lock:
            return self._spent_cny

    @property
    def remaining_cny(self) -> Decimal:
        with self._lock:
            return self._limit_cny - self._spent_cny - self._reserved_cny

    def snapshot(self) -> ModelUsage:
        with self._lock:
            return ModelUsage(
                input_cache_hit_tokens=self._hit_tokens,
                input_cache_miss_tokens=self._miss_tokens,
                output_tokens=self._output_tokens,
                reasoning_tokens=self._reasoning_tokens,
                estimated_cost_cny=self._spent_cny,
            )


def usage_from_response(response: Any) -> ModelUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return ModelUsage()
    hit = _integer(usage, "prompt_cache_hit_tokens")
    if hit == 0:
        input_details = getattr(usage, "input_tokens_details", None)
        hit = _integer(input_details, "cached_tokens")
    explicit_miss = _optional_integer(usage, "prompt_cache_miss_tokens")
    total_input = _integer(usage, "prompt_tokens") or _integer(usage, "input_tokens")
    miss = explicit_miss if explicit_miss is not None else max(0, total_input - hit)
    output = _integer(usage, "completion_tokens") or _integer(usage, "output_tokens")
    completion_details = getattr(usage, "completion_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    reasoning = _integer(completion_details, "reasoning_tokens") or _integer(
        output_details, "reasoning_tokens"
    )
    return ModelUsage(
        input_cache_hit_tokens=hit,
        input_cache_miss_tokens=miss,
        output_tokens=output,
        reasoning_tokens=reasoning,
    )


def _integer(value: Any, field: str) -> int:
    result = _optional_integer(value, field)
    return 0 if result is None else result


def _optional_integer(value: Any, field: str) -> int | None:
    result = getattr(value, field, None)
    return result if isinstance(result, int) and not isinstance(result, bool) else None
