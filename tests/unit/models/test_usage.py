from decimal import Decimal

import pytest

from repo_maintenance_agent.evaluation.models import ModelUsage
from repo_maintenance_agent.models.usage import (
    ModelBudgetExceeded,
    UsageLedger,
    UsageRates,
)


def test_ledger_prices_provider_categories_without_double_charging_reasoning() -> None:
    ledger = UsageLedger(
        limit_cny=Decimal("1.00"),
        rates=UsageRates(
            cache_hit_input_cny_per_million=Decimal("0.20"),
            cache_miss_input_cny_per_million=Decimal("2.00"),
            output_cny_per_million=Decimal("8.00"),
        ),
    )
    reservation = ledger.reserve(Decimal("0.50"))

    charged = ledger.record(
        ModelUsage(
            input_cache_hit_tokens=500_000,
            input_cache_miss_tokens=100_000,
            output_tokens=25_000,
            reasoning_tokens=10_000,
        ),
        reservation_cny=reservation,
    )

    assert charged == Decimal("0.500000")
    assert ledger.spent_cny == Decimal("0.500000")
    assert ledger.remaining_cny == Decimal("0.500000")


def test_ledger_refuses_a_call_that_could_cross_the_hard_limit() -> None:
    ledger = UsageLedger(
        limit_cny=Decimal("0.49"),
        rates=UsageRates(
            cache_hit_input_cny_per_million=Decimal("0.20"),
            cache_miss_input_cny_per_million=Decimal("2.00"),
            output_cny_per_million=Decimal("8.00"),
        ),
    )

    with pytest.raises(ModelBudgetExceeded, match="hard limit"):
        ledger.reserve(Decimal("0.50"))
