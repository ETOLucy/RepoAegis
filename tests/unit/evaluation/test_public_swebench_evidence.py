from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[3]
EVIDENCE = ROOT / "docs" / "evidence" / "swebench-holdout-v2.json"


def test_public_swebench_evidence_keeps_the_frozen_denominator() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    generation = evidence["generation"]
    grading = evidence["official_grading"]
    cases = evidence["cases"]

    assert evidence["now_consumed"] is True
    assert len(cases) == generation["frozen_tasks"] == grading["strict_total"] == 8
    assert len({case["instance_id"] for case in cases}) == 8
    assert generation["predictions_generated"] + generation["generation_failures"] == 8
    assert grading["submitted_predictions"] == generation["predictions_generated"] == 4
    assert grading["resolved_predictions"] == grading["strict_resolved"] == 3
    assert grading["strict_resolved_rate"] == grading["strict_resolved"] / 8


def test_public_swebench_evidence_token_total_matches_categories() -> None:
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    usage = evidence["generation"]["usage"]
    expected = (
        usage["cache_hit_input_tokens"]
        + usage["cache_miss_input_tokens"]
        + usage["output_tokens"]
    )

    assert expected == usage["total_tokens"] == 387775
    assert not any("cny" in key.lower() for key in usage)
