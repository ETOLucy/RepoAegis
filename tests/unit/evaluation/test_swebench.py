from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from repo_maintenance_agent.evaluation.models import ModelUsage
from repo_maintenance_agent.evaluation.swebench import (
    SWEbenchPrediction,
    write_predictions,
)


def test_prediction_writer_emits_exact_official_jsonl(tmp_path) -> None:
    output = tmp_path / "predictions.jsonl"
    predictions = (
        SWEbenchPrediction(
            instance_id="django__django-11099",
            model_patch="diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n",
            model_name_or_path="deepseek-chat/baseline",
        ),
        SWEbenchPrediction(
            instance_id="sympy__sympy-20590",
            model_patch="--- a/core.py\n+++ b/core.py\n@@ -1 +1 @@\n-old\n+new\n",
            model_name_or_path="deepseek-chat/baseline",
        ),
    )

    write_predictions(output, predictions)

    assert output.read_text(encoding="utf-8") == (
        '{"instance_id":"django__django-11099","model_patch":"diff --git a/app.py '
        'b/app.py\\n--- a/app.py\\n+++ b/app.py\\n","model_name_or_path":'
        '"deepseek-chat/baseline"}\n'
        '{"instance_id":"sympy__sympy-20590","model_patch":"--- a/core.py\\n'
        '+++ b/core.py\\n@@ -1 +1 @@\\n-old\\n+new\\n","model_name_or_path":'
        '"deepseek-chat/baseline"}\n'
    )


def test_prediction_rejects_non_patch_content() -> None:
    with pytest.raises(ValidationError, match="unified diff"):
        SWEbenchPrediction(
            instance_id="django__django-11099",
            model_patch="I changed the file successfully.",
            model_name_or_path="deepseek-chat/baseline",
        )


def test_model_usage_preserves_provider_token_categories() -> None:
    usage = ModelUsage(
        input_cache_hit_tokens=700,
        input_cache_miss_tokens=300,
        output_tokens=80,
        reasoning_tokens=20,
        estimated_cost_cny=Decimal("0.0123"),
    )

    assert usage.input_tokens == 1_000
    assert usage.total_tokens == 1_080
    assert usage.estimated_cost_cny == Decimal("0.0123")

    with pytest.raises(ValidationError):
        ModelUsage(
            input_cache_hit_tokens=-1,
            input_cache_miss_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            estimated_cost_cny=Decimal("0"),
        )
