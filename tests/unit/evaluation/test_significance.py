from __future__ import annotations

import pytest

from repo_maintenance_agent.evaluation.significance import (
    _normal_ppf,
    clopper_pearson_ci,
    cohens_h,
    minimum_effect_tier,
    paired_bootstrap_delta,
    required_n_for_power,
    wilson_ci,
)


def test_paired_bootstrap_delta_is_reproducible() -> None:
    baseline = [0.5, 0.6, 0.7, 0.8, 0.9]
    candidate = [0.6, 0.7, 0.8, 0.9, 1.0]
    first = paired_bootstrap_delta(baseline, candidate, seed=42)
    second = paired_bootstrap_delta(baseline, candidate, seed=42)

    assert first == second
    assert first.mean_delta == pytest.approx(0.1)


def test_paired_bootstrap_delta_detects_improvement() -> None:
    decision = paired_bootstrap_delta(
        [0.0, 0.1, 0.2, 0.3],
        [0.9, 1.0, 1.0, 1.0],
        seed=7,
    )

    assert decision.direction == "improvement"
    assert decision.significant is True
    assert decision.ci_lower > 0
    assert decision.mean_delta > 0


def test_paired_bootstrap_delta_detects_regression() -> None:
    decision = paired_bootstrap_delta(
        [0.9, 1.0, 1.0, 1.0],
        [0.0, 0.1, 0.2, 0.3],
        seed=7,
    )

    assert decision.direction == "regression"
    assert decision.significant is True
    assert decision.ci_upper < 0
    assert decision.mean_delta < 0


def test_paired_bootstrap_delta_is_inconclusive_when_overlapping() -> None:
    decision = paired_bootstrap_delta(
        [0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
        [0.5, 0.6, 0.4, 0.6, 0.4, 0.5],
        seed=3,
    )

    assert decision.direction == "inconclusive"
    assert decision.significant is False
    assert decision.ci_lower <= 0 <= decision.ci_upper


def test_paired_bootstrap_delta_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        paired_bootstrap_delta([0.5, 0.6], [0.5], seed=1)


def test_paired_bootstrap_delta_rejects_empty_sequences() -> None:
    with pytest.raises(ValueError):
        paired_bootstrap_delta([], [], seed=1)


def test_wilson_ci_three_of_eight_is_within_loose_bounds() -> None:
    lower, upper = wilson_ci(3, 8)

    assert 0.10 <= lower <= upper <= 0.75


def test_wilson_ci_zero_samples_returns_zero_interval() -> None:
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_clopper_pearson_ci_three_of_eight_matches_known_value() -> None:
    lower, upper = clopper_pearson_ci(3, 8)

    assert lower == pytest.approx(0.085, abs=0.01)
    assert upper == pytest.approx(0.755, abs=0.01)
    assert 0.0 <= lower <= upper <= 1.0


def test_clopper_pearson_ci_zero_successes_lower_bound_is_zero() -> None:
    lower, upper = clopper_pearson_ci(0, 10)

    assert lower == 0.0
    assert 0.0 <= upper <= 1.0


def test_clopper_pearson_ci_all_successes_upper_bound_is_one() -> None:
    lower, upper = clopper_pearson_ci(10, 10)

    assert upper == 1.0
    assert 0.0 <= lower <= 1.0


def test_clopper_pearson_ci_zero_samples_returns_zero_interval() -> None:
    assert clopper_pearson_ci(0, 0) == (0.0, 0.0)
    assert clopper_pearson_ci(3, -2) == (0.0, 0.0)


def test_clopper_pearson_ci_rejects_invalid_k() -> None:
    with pytest.raises(ValueError):
        clopper_pearson_ci(5, 3)
    with pytest.raises(ValueError):
        clopper_pearson_ci(-1, 5)


def test_minimum_effect_tier_adapts_to_sample_size() -> None:
    assert minimum_effect_tier(500) == 0.03
    assert minimum_effect_tier(300) == 0.03
    assert minimum_effect_tier(200) == 0.05
    assert minimum_effect_tier(100) == 0.05
    assert minimum_effect_tier(50) == 0.10
    assert minimum_effect_tier(1) == 0.10


def test_minimum_effect_tier_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        minimum_effect_tier(0)
    with pytest.raises(ValueError):
        minimum_effect_tier(-10)


def test_cohens_h_is_zero_for_equal_rates() -> None:
    assert cohens_h(0.30, 0.30) == pytest.approx(0.0, abs=1e-12)


def test_cohens_h_known_value_three_tenths_to_four_tenths() -> None:
    assert cohens_h(0.30, 0.40) == pytest.approx(0.207, abs=0.01)


def test_cohens_h_rejects_out_of_range_rates() -> None:
    with pytest.raises(ValueError):
        cohens_h(-0.1, 0.5)
    with pytest.raises(ValueError):
        cohens_h(0.5, 1.5)


def test_required_n_for_power_small_improvement_matches_plan() -> None:
    # Plan section 1.1 expects ~464 for 0.30 -> 0.36.
    assert required_n_for_power(0.30, 0.36) == pytest.approx(464, abs=50)


def test_required_n_for_power_large_improvement_matches_plan() -> None:
    # Plan section 1.1 expects ~30 for 0.30 -> 0.55.
    assert required_n_for_power(0.30, 0.55) == pytest.approx(30, abs=5)


def test_required_n_for_power_rejects_zero_effect() -> None:
    with pytest.raises(ValueError):
        required_n_for_power(0.30, 0.30)


def test_normal_ppf_sanity_975_percentile_is_about_one_point_ninety_six() -> None:
    assert _normal_ppf(0.975) == pytest.approx(1.96, abs=0.01)
