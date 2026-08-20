from __future__ import annotations

import random
from dataclasses import dataclass
from math import asin, ceil, exp, lgamma, log, log1p, sqrt


@dataclass(frozen=True)
class BootstrapDecision:
    mean_delta: float
    ci_lower: float
    ci_upper: float
    significant: bool
    direction: str


def paired_bootstrap_delta(
    baseline_scores: tuple[float, ...] | list[float],
    candidate_scores: tuple[float, ...] | list[float],
    *,
    seed: int,
    resamples: int = 10_000,
) -> BootstrapDecision:
    """Percentile bootstrap of paired score deltas (candidate - baseline)."""
    if not baseline_scores or not candidate_scores:
        raise ValueError("score sequences must not be empty")
    if len(baseline_scores) != len(candidate_scores):
        raise ValueError("baseline and candidate score sequences must have equal length")

    n = len(baseline_scores)
    deltas = [
        candidate - baseline
        for baseline, candidate in zip(baseline_scores, candidate_scores, strict=True)
    ]
    mean_delta = sum(deltas) / n

    rng = random.Random(seed)  # noqa: S311 - deterministic non-cryptographic bootstrap RNG
    resampled = []
    for _ in range(resamples):
        resampled.append(sum(deltas[rng.randrange(n)] for _ in range(n)) / n)
    resampled.sort()

    ci_lower = resampled[round(0.025 * (resamples - 1))]
    ci_upper = resampled[round(0.975 * (resamples - 1))]
    if ci_lower > 0:
        direction = "improvement"
    elif ci_upper < 0:
        direction = "regression"
    else:
        direction = "inconclusive"
    return BootstrapDecision(
        mean_delta=mean_delta,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        significant=direction != "inconclusive",
        direction=direction,
    )


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes out of n trials."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denominator = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    half_width = z * sqrt((p * (1 - p) + z2 / (4 * n)) / n) / denominator
    return (max(0.0, center - half_width), min(1.0, center + half_width))


# ---------------------------------------------------------------------------
# Exact binomial confidence intervals and power analysis
# (pure standard library; scipy is intentionally not used)
# ---------------------------------------------------------------------------

MAX_BETACF_ITERATIONS = 1000
BETACF_EPS = 3e-14
BETACF_FPMIN = 1e-300
PPF_BISECTION_ITERATIONS = 100
PPF_TOLERANCE = 1e-10


def _betacf(a: float, b: float, x: float) -> float:
    """Evaluate the continued fraction behind the regularized beta function.

    Classic Numerical Recipes ``betacf``; the caller must pass the branch for
    which ``x`` stays small (see :func:`_regularized_beta`).
    """
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < BETACF_FPMIN:
        d = BETACF_FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAX_BETACF_ITERATIONS + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < BETACF_FPMIN:
            d = BETACF_FPMIN
        c = 1.0 + aa / c
        if abs(c) < BETACF_FPMIN:
            c = BETACF_FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < BETACF_FPMIN:
            d = BETACF_FPMIN
        c = 1.0 + aa / c
        if abs(c) < BETACF_FPMIN:
            c = BETACF_FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < BETACF_EPS:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    """Regularized incomplete beta function I_x(a, b), pure stdlib.

    Uses the continued-fraction evaluation of Numerical Recipes (``betacf``)
    combined with a ``lgamma``-based starting factor; handles the ``x`` near
    0/1 edges and the symmetry branch that keeps the continued fraction well
    conditioned. Small ``a``/``b`` values are rejected up front.
    """
    if a <= 0.0 or b <= 0.0:
        raise ValueError(f"a and b must be positive, got a={a}, b={b}")
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = lgamma(a + b) - lgamma(a) - lgamma(b) + a * log(x) + b * log1p(-x)
    bt = exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _beta_ppf(p: float, a: float, b: float) -> float:
    """Inverse CDF of Beta(a, b): bisection on I_x(a, b) = p."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    if a <= 0.0 or b <= 0.0:
        raise ValueError(f"a and b must be positive, got a={a}, b={b}")
    low, high = 0.0, 1.0
    for _ in range(PPF_BISECTION_ITERATIONS):
        mid = 0.5 * (low + high)
        if _regularized_beta(mid, a, b) < p:
            low = mid
        else:
            high = mid
        if high - low < PPF_TOLERANCE:
            break
    return 0.5 * (low + high)


def _normal_ppf(q: float) -> float:
    """Approximate standard-normal quantile (Abramowitz & Stegun 26.2.23).

    The A&S rational approximation returns the *upper-tail* quantile
    ``f(p) = Phi^{-1}(1 - p)`` for ``p`` in ``(0, 0.5]``; the cumulative
    quantile is recovered by symmetry. Accuracy is ~1e-3, plenty for power
    planning.
    """
    if q <= 0.0:
        return float("-inf")
    if q >= 1.0:
        return float("inf")
    p = 1.0 - q if q > 0.5 else q
    t = sqrt(-2.0 * log(p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    x = t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)
    return x if q > 0.5 else -x


def clopper_pearson_ci(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact Clopper-Pearson confidence interval for a binomial proportion.

    ``lower`` is the ``alpha/2`` quantile of ``Beta(k, n - k + 1)`` (``0.0``
    when ``k == 0``); ``upper`` is the ``1 - alpha/2`` quantile of
    ``Beta(k + 1, n - k)`` (``1.0`` when ``k == n``). ``n <= 0`` returns
    ``(0.0, 0.0)``; invalid ``k`` (negative or ``> n``) raises ValueError.

    Reference value: ``clopper_pearson_ci(3, 8)`` approximately equals
    ``(0.085, 0.755)`` at ``alpha=0.05`` (publicly known exact binomial
    interval; assertion tolerance +-0.01).
    """
    if n <= 0:
        return (0.0, 0.0)
    if k < 0 or k > n:
        raise ValueError(f"k must satisfy 0 <= k <= n, got k={k}, n={n}")
    lower = 0.0 if k == 0 else _beta_ppf(alpha / 2.0, float(k), float(n - k + 1))
    upper = 1.0 if k == n else _beta_ppf(1.0 - alpha / 2.0, float(k + 1), float(n - k))
    return (lower, upper)


def minimum_effect_tier(n: int) -> float:
    """Minimum effect tier (Cohen's h) that adapts to sample size.

    - ``n >= 300`` -> ``0.03``
    - ``100 <= n < 300`` -> ``0.05``
    - ``n < 100`` -> ``0.10``
    - ``n <= 0`` raises :class:`ValueError`.
    """
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if n >= 300:
        return 0.03
    if n >= 100:
        return 0.05
    return 0.10


def cohens_h(p1: float, p2: float) -> float:
    """Effect size Cohen's h = 2*(asin(sqrt(p2)) - asin(sqrt(p1)))."""
    if not 0.0 <= p1 <= 1.0:
        raise ValueError(f"p1 must be within [0, 1], got {p1}")
    if not 0.0 <= p2 <= 1.0:
        raise ValueError(f"p2 must be within [0, 1], got {p2}")
    return 2.0 * (asin(sqrt(p2)) - asin(sqrt(p1)))


def required_n_for_power(
    p1: float,
    p2: float,
    *,
    power: float = 0.80,
    alpha: float = 0.05,
) -> int:
    """Per-arm sample size needed to detect ``p1 -> p2`` in a paired design.

    Normal-approximation estimate ``n ~= (z_{1-alpha/2} + z_power)^2 / h^2``
    with ``h = cohens_h(p1, p2)`` and ``z`` from :func:`_normal_ppf`. This is
    a paired-design (McNemar-style) approximation; the result is rounded up.
    Raises :class:`ValueError` when ``h == 0`` (a zero effect cannot be sized).
    """
    h = cohens_h(p1, p2)
    if h == 0.0:
        raise ValueError("cannot size a study for a zero effect (p1 == p2)")
    z_alpha = _normal_ppf(1.0 - alpha / 2.0)
    z_power = _normal_ppf(power)
    return ceil((z_alpha + z_power) ** 2 / (h * h))
